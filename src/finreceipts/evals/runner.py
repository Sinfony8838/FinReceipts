"""Run the agent over an eval set and compute metrics.

Metrics (all computed from the *final* answer of each item):

- ``accuracy``: the number on the 'ANSWER:' line matches the ground truth
  under the verifier's rounding-aware tolerance;
- ``unsupported_number_rate``: share of all numbers written (years excluded)
  for which the unbound verifier found no receipt;
- ``answers_with_unsupported``: share of answers containing >=1 such number;
- token usage (total and per model), LLM calls, latency (mean / p50 / p95),
  revisions and, for ``mode="cascade"``, the escalation rate;
- ``cost``: optional, only when per-model prices are supplied (no prices are
  hard-coded; pass them explicitly so the numbers stay auditable).
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from finreceipts.agent.pit_wiring import FactsProvider, PitContext, SealedToolCache, scope_guard
from finreceipts.agent.tools import LookupProvider
from finreceipts.evals.controls import control_metadata, controlled_provider, validate_controls
from finreceipts.evals.dataset import EvalItem
from finreceipts.llm.client import FatalAPIError, LLMClient, Usage
from finreceipts.models import Verdict
from finreceipts.tools.akshare_a import AShareClient
from finreceipts.tools.edgar import EdgarClient, parse_concept_facts
from finreceipts.verify.numbers import extract_numbers
from finreceipts.verify.serialization import serialize_audit
from finreceipts.verify.verifier import Tolerance, check_value
from finreceipts.xbrl.metrics import all_xbrl_tags, lookup_from_companyfacts


def edgar_lookup_provider(client: EdgarClient) -> LookupProvider:
    """Cached (ticker, as_of) -> FactLookup backed by an :class:`EdgarClient`."""

    @lru_cache(maxsize=256)
    def provider(ticker: str, as_of: date | None) -> Any:
        cf = client.companyfacts(client.ticker_to_cik(ticker))
        return lookup_from_companyfacts(cf, as_of=as_of)

    return provider


def edgar_facts_provider(client: EdgarClient) -> FactsProvider:
    """Raw SEC rows; PIT selection must run before annualisation."""

    @lru_cache(maxsize=256)
    def provider(ticker: str):
        cf = client.companyfacts(client.ticker_to_cik(ticker))
        return {tag: parse_concept_facts(cf, tag) for tag in all_xbrl_tags()}

    return provider


def ashare_lookup_provider(client: AShareClient) -> LookupProvider:
    """Cached (code, as_of) -> FactLookup backed by an :class:`AShareClient`."""

    @lru_cache(maxsize=256)
    def provider(ticker: str, as_of: date | None) -> Any:
        return client.lookup(ticker, as_of=as_of)

    return provider


def score_answer(item: EvalItem, answer: str, tol: Tolerance | None = None) -> tuple[bool, str]:
    """Return (correct, parsed_text) for the final 'ANSWER:' value."""
    from finreceipts.agent.graph import final_answer_text

    text = final_answer_text(answer)
    mentions = extract_numbers(text)
    if not mentions:
        return False, text
    m = mentions[0]
    verdict, _, _ = check_value(
        m.value,
        item.expected_value,
        claimed_unit=m.unit,
        expected_unit=item.expected_unit,
        rounding_step=m.rounding_step,
        negative_context=m.negative_context,
        tol=tol,
    )
    return verdict is Verdict.SUPPORTED, m.text


@dataclass
class ItemResult:
    id: str
    kind: str
    correct: bool
    parsed: str
    numbers: int
    unsupported: int
    revisions: int
    input_tokens: int
    output_tokens: int
    llm_calls: int
    latency_s: float
    answer: str
    attempts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    escalated: bool = False
    final_model: str = ""
    usage_by_model: dict[str, dict[str, float]] = field(default_factory=dict)
    # None means unavailable (e.g. execution failed); [] means a completed audit
    # found no numeric claims. Historical files are never backfilled.
    audit: list[dict[str, Any]] | None = None
    audit_schema_version: int = 1
    verification_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunConfig:
    model: str
    mode: str = "baseline"
    feedback: str = "flags"
    use_tools: bool = False
    point_in_time: bool = True
    max_revisions: int = 2
    escalate_model: str | None = None
    guard_tools: bool = False
    mark_untrusted: bool = False
    sealed_tool_cache: bool = False


def run_item(graph: Any, item: EvalItem, cfg: RunConfig) -> ItemResult:
    from finreceipts.agent.graph import initial_state

    state = initial_state(
        item.question,
        ticker=item.ticker,
        fiscal_years=sorted({item.fiscal_year, item.fiscal_year - 1}),
        model=cfg.model,
        mode=cfg.mode,
        feedback=cfg.feedback,
        use_tools=cfg.use_tools,
        as_of=item.as_of if cfg.point_in_time else None,
        max_revisions=cfg.max_revisions,
        escalate_model=cfg.escalate_model,
    )
    t0 = time.perf_counter()
    try:
        out = graph.invoke(state)
    except FatalAPIError:
        raise
    except Exception as exc:
        return ItemResult(
            item.id,
            item.kind,
            False,
            "",
            0,
            0,
            0,
            0,
            0,
            0,
            time.perf_counter() - t0,
            "",
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
    wall = time.perf_counter() - t0
    usage: Usage = out.get("usage") or Usage()
    audit = out.get("audit", [])
    correct, parsed = score_answer(item, out.get("answer", ""))
    return ItemResult(
        id=item.id,
        kind=item.kind,
        correct=correct,
        parsed=parsed,
        numbers=len(audit),
        unsupported=sum(1 for r in audit if not r.ok),
        revisions=out.get("revisions", 0),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        llm_calls=usage.calls,
        latency_s=wall,
        answer=out.get("answer", ""),
        attempts=out.get("attempts", []),
        audit=serialize_audit(audit),
        escalated=bool(out.get("escalated")),
        final_model=out.get("model", cfg.model),
        usage_by_model={
            m: {
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "calls": u.calls,
                "llm_latency_s": round(u.latency_s, 3),
            }
            for m, u in (out.get("usage_by_model") or {}).items()
        },
    )


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = max(0, min(len(values) - 1, round(q * (len(values) - 1))))
    return values[k]


Prices = dict[str, tuple[float, float]]
"""model -> (price per 1M input tokens, price per 1M output tokens), any currency."""


def _tokens_by_model(results: list[ItemResult]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in results:
        for m, u in r.usage_by_model.items():
            agg = out.setdefault(
                m, {"input_tokens": 0, "output_tokens": 0, "calls": 0, "llm_latency_s": 0.0}
            )
            for k in agg:
                agg[k] += u.get(k, 0)
    for agg in out.values():
        agg["mean_call_latency_s"] = agg["llm_latency_s"] / agg["calls"] if agg["calls"] else 0.0
        agg["llm_latency_s"] = round(agg["llm_latency_s"], 3)
    return out


def cost_of(tokens_by_model: dict[str, dict[str, float]], prices: Prices) -> float | None:
    """Total cost under ``prices``; ``None`` if any used model has no price."""
    total = 0.0
    for m, u in tokens_by_model.items():
        if m not in prices:
            return None
        p_in, p_out = prices[m]
        total += u["input_tokens"] / 1e6 * p_in + u["output_tokens"] / 1e6 * p_out
    return total


def summarize(results: list[ItemResult], prices: Prices | None = None) -> dict[str, Any]:
    ok = [r for r in results if r.error is None]
    n = len(results)
    nums = sum(r.numbers for r in ok)
    lat = [r.latency_s for r in ok]
    by_kind: dict[str, dict[str, float]] = {}
    for kind in sorted({r.kind for r in results}):
        rs = [r for r in results if r.kind == kind]
        by_kind[kind] = {"n": len(rs), "accuracy": sum(r.correct for r in rs) / len(rs)}
    return {
        "n_items": n,
        "n_errors": n - len(ok),
        "accuracy": sum(r.correct for r in results) / n if n else 0.0,
        "unsupported_number_rate": (sum(r.unsupported for r in ok) / nums) if nums else 0.0,
        "answers_with_unsupported": (sum(1 for r in ok if r.unsupported) / len(ok)) if ok else 0.0,
        "numbers_total": nums,
        "input_tokens": sum(r.input_tokens for r in ok),
        "output_tokens": sum(r.output_tokens for r in ok),
        "llm_calls": sum(r.llm_calls for r in ok),
        "mean_revisions": statistics.fmean([r.revisions for r in ok]) if ok else 0.0,
        "latency_mean_s": statistics.fmean(lat) if lat else 0.0,
        "latency_p50_s": _pct(lat, 0.5),
        "latency_p95_s": _pct(lat, 0.95),
        "escalation_rate": (sum(r.escalated for r in ok) / len(ok)) if ok else 0.0,
        "tokens_by_model": (tbm := _tokens_by_model(ok)),
        "cost": cost_of(tbm, prices) if prices else None,
        "by_kind": by_kind,
    }


def run_eval(
    items: Iterable[EvalItem],
    llm: LLMClient,
    provider: LookupProvider,
    cfg: RunConfig,
    *,
    out_dir: Path | None = None,
    on_item: Callable[[ItemResult], None] | None = None,
    prices: Prices | None = None,
    pit_context: PitContext | None = None,
    facts_provider: FactsProvider | None = None,
) -> dict[str, Any]:
    """Run items with explicit controls; legacy filed-date filtering stays default.

    A strict PIT run requires host-supplied raw facts, catalog and cutoff. Its
    policy is authoritative, not each dataset item's legacy date-only as_of.
    The dataset answer key is not thereby validated for that cutoff.
    """
    items = list(items)
    validate_controls(
        items,
        point_in_time=cfg.point_in_time,
        use_tools=cfg.use_tools,
        guard_tools=cfg.guard_tools,
        mark_untrusted=cfg.mark_untrusted,
        sealed_tool_cache=cfg.sealed_tool_cache,
        pit_context=pit_context,
        facts_provider=facts_provider,
    )
    from finreceipts.agent.graph import AgentDeps, build_graph

    provider = controlled_provider(provider, pit_context, facts_provider)
    cache = SealedToolCache(pit_context) if cfg.sealed_tool_cache and pit_context else None
    graph = build_graph(
        AgentDeps(
            llm=llm,
            lookup_provider=provider,
            tool_guard=scope_guard if cfg.guard_tools else None,
            mark_untrusted=cfg.mark_untrusted,
            tool_cache=cache,
        )
    )
    results: list[ItemResult] = []
    aborted: str | None = None
    fh = None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        fh = (out_dir / "items.jsonl").open("w", encoding="utf-8")  # written per item
    try:
        for item in items:
            res = run_item(graph, item, cfg)
            res.verification_context = control_metadata(
                point_in_time=cfg.point_in_time, pit_context=pit_context, cache=None
            )
            res.verification_context["dataset_as_of"] = item.as_of.isoformat()
            results.append(res)
            if fh:
                fh.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
                fh.flush()
            if on_item:
                on_item(res)
    except FatalAPIError as exc:
        aborted = str(exc)
    finally:
        if fh:
            fh.close()
    summary: dict[str, Any] = {
        "result_schema_version": 2,
        "config": asdict(cfg),
        "verification_context": control_metadata(
            point_in_time=cfg.point_in_time, pit_context=pit_context, cache=cache
        ),
        **summarize(results, prices),
    }
    if hasattr(llm, "attempts"):
        summary["api_attempts"] = dict(llm.attempts)
        summary["api_failures"] = dict(getattr(llm, "failures", {}))
    summary["aborted"] = aborted
    if out_dir is not None:
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if aborted:
        raise FatalAPIError(aborted)
    return summary
