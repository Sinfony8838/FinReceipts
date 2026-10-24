"""Offline contract suite and explicit, budgeted live entry. Never overwrite evidence."""

from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import html
import json
import math
import random
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

from finreceipts.agent.graph import AgentDeps, initial_state, step_nodes, verification_passed
from finreceipts.agent.pit_wiring import scope_guard
from finreceipts.config import Settings
from finreceipts.evals.dataset import EvalItem, read_jsonl
from finreceipts.llm.client import (
    AnthropicCompatClient,
    FatalAPIError,
    LLMResponse,
    ToolCall,
    Usage,
)
from finreceipts.runtime.codec import canonical, implementation_id
from finreceipts.verify.numbers import extract_numbers
from finreceipts.verify.target import VerificationTarget, target_evidence
from finreceipts.xbrl.metrics import lookup_from_companyfacts

SCENARIOS = ("normal", "wrong_unit", "time_confusion", "missing_evidence", "tool_timeout")
STRATEGIES = ("direct", "audit_only", "bounded_revision")


def save(path: Path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def oracle_value(item: EvalItem) -> Decimal:
    """Independent answer arithmetic from evidence rows, not the new verifier."""
    values = [Decimal(str(e.value)) for e in item.evidence]
    value = values[0]
    if item.kind == "ratio":
        value = values[0] / values[1] * 100
    elif item.kind == "yoy_growth":
        value = (values[0] - values[1]) / abs(values[1]) * 100
    if not math.isclose(float(value), item.expected_value, rel_tol=1e-12, abs_tol=1e-8):
        raise ValueError("independent answer key mismatch: " + item.id)
    return value


def item_target(item):
    metric, denominator = item.metric, None
    if item.kind == "ratio":
        metric, denominator = metric.split(" as a percentage of ")
    elif item.kind == "yoy_growth":
        metric = metric.removeprefix("year-over-year growth in ")
    return VerificationTarget(
        kind=item.kind, metric=metric, fiscal_year=item.fiscal_year, denominator=denominator
    ).model_dump(mode="json")


def prepare(root: Path, history: Path):
    pool = read_jsonl(history / "candidate-pool.jsonl")
    exposed = {i.id for i in read_jsonl(history / "sample.jsonl")}
    facts_bytes = gzip.decompress((history / "facts.json.gz").read_bytes())
    facts = json.loads(facts_bytes)
    rng = random.Random(42)
    selected = []
    for kind, count in (("direct", 4), ("ratio", 2), ("yoy_growth", 2)):
        options = sorted(
            (i for i in pool if i.kind == kind and i.id not in exposed), key=lambda i: i.id
        )
        chosen = rng.sample(options, count)
        selected.extend(
            (i, "dev" if index % 2 == 0 else "frozen") for index, i in enumerate(chosen)
        )
    cases = []
    for item, split in selected:
        value = oracle_value(item)
        for evidence in item.evidence:
            raw = facts[item.ticker]["facts"]["us-gaap"][evidence.concept]["units"][evidence.unit]
            if not any(
                r["accn"] == evidence.accn
                and r["end"] == str(evidence.end)
                and str(r["filed"]) == str(evidence.filed)
                and Decimal(str(r["val"])) == Decimal(str(evidence.value))
                for r in raw
            ):
                raise ValueError("source row mismatch")
        alternate = next(
            i
            for i in pool
            if i.ticker == item.ticker
            and i.metric == item.metric
            and i.fiscal_year != item.fiscal_year
        )
        for scenario in SCENARIOS:
            cases.append(
                {
                    "id": item.id + ":" + scenario,
                    "group": item.id,
                    "split": split,
                    "scenario": scenario,
                    "item": item.model_dump(mode="json"),
                    "target": item_target(item),
                    "oracle_decimal": str(value),
                    "wrong_period_decimal": str(oracle_value(alternate)),
                    "wrong_fiscal_year": alternate.fiscal_year,
                    "origin": "retained_sec_facts_with_scripted_faults",
                }
            )
    root.mkdir(parents=True, exist_ok=False)
    save(root / "cases.json", cases)
    (root / "facts.json.gz").write_bytes(gzip.compress(facts_bytes, mtime=0))
    manifest = {
        "schema": 1,
        "seed": 42,
        "groups": 8,
        "cases": 40,
        "splits": {s: [c["id"] for c in cases if c["split"] == s] for s in ("dev", "frozen")},
        "cases_sha256": file_sha(root / "cases.json"),
        "facts_sha256": file_sha(root / "facts.json.gz"),
        "history_sample_sha256": file_sha(history / "sample.jsonl"),
        "excluded_historical_ids": sorted(exposed),
        "source_retrieved_at": None,
        "selection": "legacy_filed_date",
        "scope": "same two-company snapshots; grouped cases, not an external benchmark",
        "new_live_evaluation": "UNRUN",
    }
    save(root / "manifest.json", manifest)
    save(
        root / "live-config.example.json",
        {
            "model": "REQUIRED",
            "budget_cny": None,
            "max_http_attempts": 60,
            "prices_cny_per_million": {
                "input": None,
                "output": None,
                "cache_read": None,
                "cache_write": None,
            },
            "price_source": "REQUIRED",
            "price_checked_at": "REQUIRED",
            "temperature": 0,
            "max_tokens": 1024,
            "max_revisions": 2,
            "tools": True,
            "model_escalation": False,
        },
    )
    return manifest


def load_suite(root):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, key in (("cases.json", "cases_sha256"), ("facts.json.gz", "facts_sha256")):
        if file_sha(root / name) != manifest[key]:
            raise ValueError("prepared input hash mismatch")
    cases = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    if len(cases) != 40 or len({c["id"] for c in cases}) != 40:
        raise ValueError("case coverage mismatch")
    groups = {}
    for case in cases:
        group = groups.setdefault(case["group"], {"split": case["split"], "scenarios": []})
        if group["split"] != case["split"] or case["group"] in manifest["excluded_historical_ids"]:
            raise ValueError("split leakage")
        group["scenarios"].append(case["scenario"])
    if any(sorted(g["scenarios"]) != sorted(SCENARIOS) for g in groups.values()):
        raise ValueError("scenario coverage mismatch")
    if any(
        sorted(c["id"] for c in cases if c["split"] == s) != sorted(manifest["splits"][s])
        for s in ("dev", "frozen")
    ):
        raise ValueError("split manifest mismatch")
    return cases, json.loads(gzip.decompress((root / "facts.json.gz").read_bytes())), manifest


def policy():
    return {
        "implementation_id": implementation_id(),
        "strategies": list(STRATEGIES),
        "temperature": 0,
        "max_tokens": 1024,
        "max_revisions": 2,
        "use_tools": True,
        "shared_initial_answer": True,
        "model_escalation": False,
    }


def freeze(root, dev):
    summary = json.loads((dev / "summary.json").read_text(encoding="utf-8"))
    if summary["split"] != "dev" or summary["policy"] != policy() or not summary["complete"]:
        raise ValueError("a completed development run of this policy is required")
    _, _, manifest = load_suite(root)
    if summary["manifest_sha256"] != file_sha(root / "manifest.json"):
        raise ValueError("development evidence belongs to another suite")
    save(
        root / "policy-lock.json",
        {
            "policy": policy(),
            "cases_sha256": manifest["cases_sha256"],
            "dev_summary_sha256": file_sha(dev / "summary.json"),
        },
    )


class Trace:
    def __init__(self, out):
        self.stream = (out / "trace.jsonl").open("x", encoding="utf-8")
        self.rows, self.context = [], {}

    def add(self, **row):
        row = {
            "seq": len(self.rows) + 1,
            "time": datetime.now(UTC).isoformat(),
            **self.context,
            **row,
        }
        self.rows.append(row)
        self.stream.write(canonical(row) + "\n")
        self.stream.flush()

    def tool(self, status, call, result, latency):
        self.add(
            kind="tool",
            status=status,
            name=call.name,
            arguments=call.arguments,
            raw_result=result,
            latency_s=latency,
        )


class RecordedLLM:
    def __init__(self, delegate, trace):
        self.delegate, self.trace = delegate, trace

    def complete(self, messages, **kwargs):
        call_id = (
            sum(r["kind"] == "model" and r["status"] == "started" for r in self.trace.rows) + 1
        )
        self.trace.add(
            kind="model",
            status="started",
            call_id=call_id,
            messages=[
                {
                    **m,
                    **(
                        {"tool_calls": [asdict(t) for t in m["tool_calls"]]}
                        if m.get("tool_calls")
                        else {}
                    ),
                }
                for m in messages
            ],
            config={k: v for k, v in kwargs.items() if k != "tools"},
        )
        started = time.perf_counter()
        try:
            result = self.delegate.complete(messages, **kwargs)
        except Exception as exc:
            self.trace.add(
                kind="model",
                status="error",
                call_id=call_id,
                error_code=type(exc).__name__,
                usage=None,
                cost=None,
                latency_s=time.perf_counter() - started,
            )
            raise
        self.trace.add(
            kind="model",
            status="completed",
            call_id=call_id,
            response={
                "text": result.text,
                "tool_calls": [asdict(t) for t in result.tool_calls],
                "usage": asdict(result.usage),
                "stop_reason": result.stop_reason,
            },
            latency_s=time.perf_counter() - started,
        )
        return result


def quantity(value, unit):
    return str(value) + ("%" if unit == "percent" else "元" if unit.startswith("CNY") else " USD")


def failed_rows(case, strategies, trace, exc, latency=None):
    calls = [
        r
        for r in trace.rows
        if r["kind"] == "model" and r["status"] == "started" and r.get("case_id") == case["id"]
    ]
    initial = sum(r.get("phase") == "initial" for r in calls)
    return [
        {
            "id": case["id"],
            "strategy": strategy,
            "scenario": case["scenario"],
            "status": "error",
            "error_code": type(exc).__name__,
            "answer": None,
            "contract_passed": False,
            "numeric_matched": False,
            "latency_s": latency,
            "shared_model_calls": initial,
            "incremental_model_calls": len(calls) - initial
            if strategy == "bounded_revision"
            else 0,
            "usage": None,
            "cost": None,
        }
        for strategy in strategies
    ]


class FixtureResponder:
    def __init__(self, case):
        self.case = case

    def complete(self, messages, **kwargs):
        case, target = self.case, self.case["target"]
        repaired = any(
            m["role"] == "user" and "A verifier checked" in m["content"] for m in messages
        )
        if not repaired and not any(m["role"] == "tool" for m in messages):
            call = ToolCall(
                "fixture-tool",
                "get_financial_fact",
                {
                    "ticker": case["item"]["ticker"],
                    "metric": target["metric"],
                    "fiscal_year": target["fiscal_year"],
                },
            )
            return LLMResponse(text="", tool_calls=[call], usage=Usage(calls=1))
        value, unit = case["oracle_decimal"], case["item"]["expected_unit"]
        if not repaired:
            if case["scenario"] == "wrong_unit":
                unit = "USD" if unit == "percent" else "CNY"
            elif case["scenario"] == "time_confusion":
                value = case["wrong_period_decimal"]
            elif case["scenario"] == "tool_timeout":
                return LLMResponse(text="ANSWER: N/A", usage=Usage(calls=1))
        return LLMResponse(text="ANSWER: " + quantity(value, unit), usage=Usage(calls=1))


def answer_matches(case, answer):
    # Independent arithmetic oracle; parsing and published tolerance remain shared limitations.
    final = (answer or "").strip().splitlines()
    if not final or not final[-1].startswith("ANSWER:"):
        return False
    mentions = extract_numbers(final[-1].removeprefix("ANSWER:"), keep_years=True)
    if len(mentions) != 1 or mentions[0].unit_conflict:
        return False
    m, unit = mentions[0], case["item"]["expected_unit"]
    if m.unit != unit and (m.unit, unit) not in {("USD", "USD/shares"), ("CNY", "CNY/shares")}:
        return False
    expected = Decimal(case["oracle_decimal"])
    allowed = max(
        Decimal("0.05") if unit == "percent" else abs(expected) * Decimal("0.005"),
        Decimal(str(m.rounding_step)),
    )
    return abs(Decimal(str(m.value)) - expected) <= allowed + Decimal("1e-12")


class HTTPBudget:
    """Persist every HTTP reservation and raw response; unknown attempts retain their hold."""

    def __init__(self, config, trace):
        self.config, self.trace, self.holds, self.stopped = config, trace, {}, False
        self.bounds = {}

    def stop(self):
        self.stopped = True
        raise FatalAPIError("live protocol or budget boundary reached")

    def request(self, request):
        body = json.loads(request.content)
        cfg, prices = self.config, self.config["prices_cny_per_million"]
        if self.stopped or body.get("model") != cfg["model"] or body.get("thinking"):
            self.stop()
        tokens = body.get("max_tokens", 1024)
        bound = len(request.content) + 4096
        reserve = (
            bound * max(prices["input"], prices["cache_read"], prices["cache_write"])
            + tokens * prices["output"]
        ) / 1e6
        if (
            tokens > 1024
            or bound > 64000
            or len(self.holds) >= cfg["max_http_attempts"]
            or sum(self.holds.values()) + reserve > cfg["budget_cny"]
        ):
            self.stop()
        index = len(self.holds) + 1
        self.holds[index] = reserve
        self.bounds[index] = (bound, tokens)
        request.extensions["fin_attempt"] = index
        self.trace.add(
            kind="http",
            status="reserved",
            attempt=index,
            held_cny=reserve,
            input_bound=bound,
            output_bound=tokens,
            request_body=body,
        )

    def response(self, response):
        response.read()
        index = response.request.extensions["fin_attempt"]
        try:
            body = response.json()
        except ValueError:
            body = None
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        cost = None
        if response.status_code == 200 and {"input_tokens", "output_tokens"} <= usage.keys():
            prices = self.config["prices_cny_per_million"]
            cost = (
                sum(
                    usage.get(key, 0) * prices[price]
                    for key, price in (
                        ("input_tokens", "input"),
                        ("output_tokens", "output"),
                        ("cache_read_input_tokens", "cache_read"),
                        ("cache_creation_input_tokens", "cache_write"),
                    )
                )
                / 1e6
            )
            previous_hold = self.holds[index]
            self.holds[index] = cost
        self.trace.add(
            kind="http",
            status="response",
            attempt=index,
            http_status=response.status_code,
            raw_response=body,
            usage=usage or None,
            estimated_cny=cost,
        )
        if cost is not None:
            inputs = sum(
                usage.get(k, 0)
                for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            )
            if (
                cost > previous_hold
                or inputs > self.bounds[index][0]
                or usage["output_tokens"] > self.bounds[index][1]
            ):
                self.stop()
        if isinstance(body, dict) and any(
            b.get("type") == "thinking" for b in body.get("content", [])
        ):
            self.stop()


def live_client(config, trace, *, validate_only=False):
    required = {
        "model",
        "budget_cny",
        "max_http_attempts",
        "prices_cny_per_million",
        "price_source",
        "price_checked_at",
        "temperature",
        "max_tokens",
        "max_revisions",
        "tools",
        "model_escalation",
    }
    if set(config) != required or config["model"] in {"", "REQUIRED", "fake", "fixture"}:
        raise ValueError("complete explicit live configuration is required")
    prices = config["prices_cny_per_million"]
    if set(prices) != {"input", "output", "cache_read", "cache_write"} or any(
        not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) or v < 0
        for v in prices.values()
    ):
        raise ValueError("all price categories must be explicit finite non-negative numbers")
    budget = config["budget_cny"]
    if (
        not isinstance(budget, (int, float))
        or isinstance(budget, bool)
        or not math.isfinite(budget)
        or budget <= 0
        or type(config["max_http_attempts"]) is not int
        or not 1 <= config["max_http_attempts"] <= 1000
        or config["temperature"] != 0
        or config["max_tokens"] != 1024
        or config["max_revisions"] != 2
        or config["tools"] is not True
        or config["model_escalation"] is not False
        or config["price_source"] == "REQUIRED"
        or config["price_checked_at"] == "REQUIRED"
    ):
        raise ValueError("invalid budget or policy configuration")
    settings = Settings.from_env()
    if settings.llm_provider != "anthropic" or not settings.llm_base_url.startswith("https://"):
        raise ValueError("live entry requires an explicitly configured HTTPS Anthropic endpoint")
    if not settings.allow_coding_plan and "coding" in settings.llm_base_url.lower():
        raise ValueError("coding-plan endpoints require existing explicit opt-in")
    secret = settings.resolve_api_key()
    if validate_only:
        return None
    client = AnthropicCompatClient(secret, settings.llm_base_url)
    budgeter = HTTPBudget(config, trace)
    client._client._client.event_hooks["request"].append(budgeter.request)
    client._client._client.event_hooks["response"].append(budgeter.response)

    class Guarded:
        def complete(self, *args, **kwargs):
            try:
                return client.complete(*args, **kwargs)
            except Exception:
                if budgeter.stopped:
                    raise FatalAPIError("live protocol or budget boundary reached") from None
                raise

    return Guarded(), client._client.close


def evaluate_case(case, facts, trace, live=None, model="fixture"):
    case_started = time.perf_counter()
    item = EvalItem.model_validate(case["item"])
    oracle_value(item)
    lookup = lookup_from_companyfacts(facts[item.ticker], as_of=item.as_of)
    allowed_years = {item.fiscal_year, item.fiscal_year - 1}
    lookup = {
        metric: {year: fact for year, fact in series.items() if year in allowed_years}
        for metric, series in lookup.items()
    }
    if case["scenario"] == "missing_evidence":
        lookup = {}
    target = VerificationTarget.model_validate(case["target"])
    _, unavailable = target_evidence(target, lookup)
    if unavailable:
        return [
            {
                "id": case["id"],
                "strategy": s,
                "scenario": case["scenario"],
                "status": "rejected",
                "reason_codes": [unavailable],
                "answer": None,
                "numeric_matched": False,
                "contract_passed": case["scenario"] == "missing_evidence",
                "latency_s": time.perf_counter() - case_started,
                "shared_model_calls": 0,
                "incremental_model_calls": 0,
                "target_audit": None,
            }
            for s in STRATEGIES
        ]

    def provider(ticker, as_of):
        if ticker != item.ticker or as_of != item.as_of:
            raise ValueError("outside frozen scope")
        if case["scenario"] == "tool_timeout" and trace.context["phase"] == "initial":
            raise TimeoutError
        return lookup

    llm = RecordedLLM(live or FixtureResponder(case), trace)

    def guard_tool(call, state):
        scope_guard(call, {**state, "fiscal_years": sorted(allowed_years)})

    deps = AgentDeps(
        llm=llm,
        lookup_provider=provider,
        tool_guard=guard_tool,
        tool_observer=trace.tool,
        mark_untrusted=True,
    )
    nodes = step_nodes(deps)
    state = initial_state(
        item.question,
        ticker=item.ticker,
        fiscal_years=[item.fiscal_year],
        model=model,
        mode="verify",
        feedback="receipts",
        use_tools=True,
        as_of=item.as_of,
        max_revisions=2,
        target=case["target"],
    )
    t0 = case_started
    trace.context["phase"] = "initial"
    state.update(nodes["answer"](state))
    initial_latency = time.perf_counter() - t0
    shared = sum(
        r["kind"] == "model" and r["status"] == "started" and r.get("case_id") == case["id"]
        for r in trace.rows
    )
    trace.context["phase"] = "audit"
    state.update(nodes["verify"](state))
    audit_latency = time.perf_counter() - t0
    initial = copy.deepcopy(state)
    results = []
    for strategy in STRATEGIES:
        current = copy.deepcopy(initial)
        if strategy == "bounded_revision":
            try:
                while not verification_passed(current) and current["revisions"] < 2:
                    trace.context["phase"] = "repair_feedback"
                    current.update(nodes["revise"](current))
                    trace.context["phase"] = "repair"
                    current.update(nodes["answer"](current))
                    trace.context["phase"] = "repair_audit"
                    current.update(nodes["verify"](current))
            except Exception as exc:
                results.extend(failed_rows(case, [strategy], trace, exc, time.perf_counter() - t0))
                trace.context["fatal"] = isinstance(exc, FatalAPIError)
                return results
        matched = answer_matches(case, current["answer"])
        total = sum(
            r["kind"] == "model" and r["status"] == "started" and r.get("case_id") == case["id"]
            for r in trace.rows
        )
        results.append(
            {
                "id": case["id"],
                "strategy": strategy,
                "scenario": case["scenario"],
                "status": "completed",
                "answer": current["answer"],
                "numeric_matched": matched,
                "contract_passed": matched,
                "reason_codes": current["target_audit"]["reason_codes"],
                "target_audit": current["target_audit"],
                "attempts": current["attempts"],
                "revisions": current["revisions"],
                "latency_s": initial_latency
                if strategy == "direct"
                else audit_latency
                if strategy == "audit_only"
                else time.perf_counter() - t0,
                "shared_model_calls": shared,
                "incremental_model_calls": total - shared,
            }
        )
    return results


def run(root: Path, out: Path, split="dev", config=None):
    run_started = time.perf_counter()
    cases, facts, manifest = load_suite(root)
    current_policy = policy()
    if config is not None:
        live_client(config, None, validate_only=True)
    if split == "frozen":
        lock = json.loads((root / "policy-lock.json").read_text(encoding="utf-8"))
        if lock["policy"] != current_policy or lock["cases_sha256"] != manifest["cases_sha256"]:
            raise ValueError("frozen policy changed; create a new dataset version")
    selected = [
        c for c in cases if c["split"] == split and (config is None or c["scenario"] == "normal")
    ]
    out.mkdir(parents=True, exist_ok=False)
    if split == "frozen":
        save(
            root / ("frozen-live-consumed.json" if config else "frozen-offline-consumed.json"),
            {
                "policy": current_policy,
                "output": str(out.resolve()),
                "time": datetime.now(UTC).isoformat(),
            },
        )
    trace, results = Trace(out), []

    def close():
        return None

    stopped = None
    try:
        live, model = None, "fixture"
        if config is not None:
            live, close = live_client(config, trace)
            model = config["model"]
        for case in selected:
            trace.context = {"case_id": case["id"], "phase": "preflight"}
            try:
                rows = evaluate_case(case, facts, trace, live, model)
            except Exception as exc:
                rows = failed_rows(case, STRATEGIES, trace, exc)
                trace.context["fatal"] = isinstance(exc, FatalAPIError)
            results.extend(rows)
            save(out / (case["id"].replace(":", "_") + ".json"), rows)
            if trace.context.get("fatal"):
                stopped = "fatal_provider_or_budget"
                break
    except Exception as exc:
        stopped = stopped or type(exc).__name__
    finally:
        close()
        trace.stream.close()
    summary = {
        "evidence_type": "real_model" if config else "scripted_engineering_acceptance",
        "new_live_evaluation": "ATTEMPTED" if config else "UNRUN",
        "split": split,
        "policy": current_policy,
        "manifest_sha256": file_sha(root / "manifest.json"),
        "configuration": config,
        "complete": stopped is None and len(results) == len(selected) * 3,
        "stopped": stopped,
        "strategies": {},
    }
    for strategy in STRATEGIES:
        rows = [r for r in results if r["strategy"] == strategy]
        latencies = sorted(r["latency_s"] for r in rows if r["latency_s"] is not None)
        errors = {}
        for row in rows:
            for reason in row.get("reason_codes", [row.get("error_code", "unknown")]):
                errors[reason] = errors.get(reason, 0) + 1
        summary["strategies"][strategy] = {
            "success": sum(r["contract_passed"] for r in rows),
            "total": len(selected),
            "numeric_matched": sum(r["numeric_matched"] for r in rows),
            "answerable": sum(c["scenario"] != "missing_evidence" for c in selected),
            "completed": sum(r["status"] == "completed" for r in rows),
            "rejected": sum(r["status"] == "rejected" for r in rows),
            "errors": sum(r["status"] == "error" for r in rows),
            "unrun": len(selected) - len(rows),
            "error_types": errors,
            "latencies_s": latencies,
            "latency_n": len(latencies),
            "p50_s": latencies[round((len(latencies) - 1) * 0.5)] if latencies else None,
            "p95_s": latencies[round((len(latencies) - 1) * 0.95)] if latencies else None,
            "shared_model_calls": sum(r.get("shared_model_calls", 0) for r in rows),
            "incremental_model_calls": sum(r.get("incremental_model_calls", 0) for r in rows),
        }
    summary["actual_model_dispatches"] = sum(
        r["kind"] == "model" and r["status"] == "started" for r in trace.rows
    )
    summary["tool_attempts"] = sum(
        r["kind"] == "tool" and r["status"] == "started" for r in trace.rows
    )
    summary["http_attempts"] = sum(
        r["kind"] == "http" and r["status"] == "reserved" for r in trace.rows
    )
    summary["unknown_http_attempts"] = summary["http_attempts"] - sum(
        r["kind"] == "http" and r["status"] == "response" for r in trace.rows
    )
    summary["unknown_usage_attempts"] = summary["http_attempts"] - sum(
        r["kind"] == "http" and r["status"] == "response" and r.get("estimated_cny") is not None
        for r in trace.rows
    )
    summary["new_live_evaluation"] = (
        "ATTEMPTED" if config and summary["actual_model_dispatches"] else "UNRUN"
    )
    summary["estimated_cny"] = (
        sum(r.get("estimated_cny") or 0 for r in trace.rows)
        if config and not summary["unknown_usage_attempts"]
        else None
    )
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    save(
        out / "provenance.json",
        {
            "commit": commit,
            "source_tree_identity": current_policy["implementation_id"],
            "lockfile_sha256": file_sha(Path("uv.lock")),
            "packages": {p: version(p) for p in ("langgraph", "pydantic", "anthropic")},
        },
    )
    save(out / "results.json", results)
    summary["wall_time_s"] = time.perf_counter() - run_started
    save(out / "summary.json", summary)
    with (out / "latencies.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["case", "strategy", "status", "latency_s"])
        writer.writerows((r["id"], r["strategy"], r["status"], r["latency_s"]) for r in results)
    failures_by_id = {}
    for row in results:
        if not row["contract_passed"]:
            failures_by_id.setdefault(row["id"], row)
    failures = list(failures_by_id.values())[:3]
    plot = []
    for strategy, values in summary["strategies"].items():
        times = values["latencies_s"]
        scale = max(times, default=1) or 1
        dots = "".join(
            f'<circle cx="{20 + 700 * value / scale:.2f}" cy="20" r="3" />' for value in times
        )
        plot.append(
            f"<p>{strategy} · n={len(times)} · max={scale:.6f}s</p>"
            '<svg viewBox="0 0 740 40" role="img" aria-label="raw latency distribution">'
            f"{dots}</svg>"
        )
    report = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        "<title>Agent backend acceptance</title>"
        "<style>body{max-width:1000px;margin:2rem auto;font:16px system-ui}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}svg{width:100%;fill:#147d92}</style>"
        + (
            "<h1>工程契约验收</h1><p>固定响应及受控故障；不代表真实模型准确率或业务效果。"
            if config is None
            else "<h1>真实模型问答对照</h1><p>仅普通问答使用真实响应；不包含脚本错误响应。"
        )
        + "各策略共享初稿，实际调用不重复相加。拒绝缺证据与数值匹配分别统计。"
        "小样本分位数波动较大，以下同时展示逐题原始分布。</p>"
        + "".join(plot)
        + "<h2>报告</h2><pre>"
        + html.escape(json.dumps(summary, ensure_ascii=False, indent=2))
        + (
            "</pre><h2>失败案例（来源为人工注入）</h2><pre>"
            if config is None
            else "</pre><h2>本轮真实失败案例</h2><pre>"
        )
        + html.escape(json.dumps(failures, ensure_ascii=False, indent=2))
        + "</pre></html>"
    )
    (out / "report.html").write_text(report, encoding="utf-8")
    save(
        out / "SHA256SUMS.json", {p.name: file_sha(p) for p in sorted(out.iterdir()) if p.is_file()}
    )
    return summary
