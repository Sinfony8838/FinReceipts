"""Budgeted paired evaluation: one shared draft, deterministic gate, at most two repairs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from finreceipts.agent.tools import GET_FACT_SPEC, run_get_fact
from finreceipts.platform.engine import SYSTEM, expected_answer, tools
from finreceipts.platform.eval_budget import EvalBudget
from finreceipts.platform.models import (
    Adapter,
    DispatchUnknown,
    ProviderRejected,
    cost_micros,
    request_body,
    reservation_micros,
    tool_result,
)
from finreceipts.runtime.codec import decode_lookup, implementation_id
from finreceipts.verify.target import VerificationTarget, verify_target_answer

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals" / "platform_v2_20261024"
OUT = ROOT / "runs" / "platform-v2-live"


def run_case(case, provider, budget):
    profile = {
        "provider": provider,
        "model": "MiniMax-M3.1-Flash" if provider == "minimax" else "deepseek-flash",
        "effort": "medium",
        "max_output_tokens": 4096,
    }
    lookup = decode_lookup(case["evidence"])
    target = VerificationTarget.model_validate(case["request"]["target"])
    messages = [
        {
            "role": "user",
            "content": json.dumps(
                {**case["request"], "evidence": case["evidence"]}, ensure_ascii=False
            ),
        }
    ]
    record = {
        "case_id": case["id"],
        "provider": provider,
        "profile": profile,
        "implementation_id": implementation_id(),
        "date": datetime.now(UTC).isoformat(),
        "evidence": case["evidence"],
        "calls": [],
        "answers": [],
        "status": "RUNNING",
    }
    destination = OUT / f"{provider}-{case['id']}.json"
    started = time.monotonic()
    adapter = Adapter(provider)
    repairs = 0

    def save():
        record["elapsed_seconds"] = time.monotonic() - started
        destination.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        for attempt in range(6):
            request_body(profile, messages, SYSTEM, tools())
            key = budget.reserve(reservation_micros(profile), case["id"], provider)
            record["calls"].append({"attempt_id": key, "status": "DISPATCHING"})
            save()
            try:
                response = adapter.complete(profile, messages, SYSTEM, tools())
            except ProviderRejected as exc:
                budget.settle(key, 0, {"http_status": exc.status})
                record["calls"][-1].update(status="REJECTED", http_status=exc.status)
                record["status"] = "REJECTED"
                # Explicitly rejected 429 alone may retry, each new HTTP attempt is reserved.
                if exc.retryable and attempt < 2:
                    time.sleep(2**attempt)
                    continue
                break
            except DispatchUnknown:
                record["calls"][-1]["status"] = "UNKNOWN"
                record["status"] = "UNKNOWN"
                break
            budget.settle(key, cost_micros(profile, response.usage), response.usage)
            record["calls"][-1].update(
                status="SETTLED",
                response=asdict(response),
                estimated_micros=cost_micros(profile, response.usage),
            )
            messages.append(response.assistant)
            if response.stop_reason in {"length", "max_tokens"}:
                record["status"] = "TRUNCATED"
                break
            if response.tools:
                for call in response.tools:
                    args = call["arguments"]
                    allowed = (
                        call["name"] == GET_FACT_SPEC.name
                        and isinstance(args, dict)
                        and set(args) == {"ticker", "metric", "fiscal_year"}
                        and args["ticker"] == case["request"]["ticker"]
                        and args["metric"] in lookup
                        and type(args["fiscal_year"]) is int
                        and args["fiscal_year"] in {target.fiscal_year, target.fiscal_year - 1}
                    )
                    result = (
                        run_get_fact(lambda ticker, as_of: lookup, args, None)
                        if allowed
                        else json.dumps({"error": "tool_scope_denied"})
                    )
                    messages.append(tool_result(provider, call["id"], result))
                save()
                continue
            audit = verify_target_answer(response.text, target, lookup)
            record["answers"].append(
                {
                    "answer": response.text,
                    "audit": audit.model_dump(mode="json"),
                    "attempts_so_far": len(record["calls"]),
                }
            )
            missing = expected_answer(target, lookup) == "ANSWER: N/A"
            if audit.ok or missing or repairs >= 2:
                record["status"] = "DONE"
                break
            repairs += 1
            messages.append(
                {
                    "role": "user",
                    "content": "Revise the answer using this deterministic audit. "
                    + audit.model_dump_json(),
                }
            )
            save()
        if record["status"] == "RUNNING":
            record["status"] = "ATTEMPT_LIMIT"
        record["comparison"] = {
            "direct_answer": "first answer, no gate",
            "verify_only": "same first answer with deterministic gate",
            "bounded_revision": "last answer with at most two repairs",
            "cost_allocation": "shared draft counted once; repairs are incremental",
            "rules_answer": expected_answer(target, lookup),
        }
        record["messages"] = messages
    finally:
        adapter.close()
        save()
    print(f"{provider} {case['id']} {record['status']} attempts={len(record['calls'])}", flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["minimax", "deepseek"], required=True)
    parser.add_argument("--split", choices=["dev", "frozen"], required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    environment = dict(
        s.split("=", 1)
        for s in (ROOT / ".env.v2").read_text(encoding="utf-8-sig").splitlines()
        if "=" in s and not s.startswith("#")
    )
    if (
        environment.get("MODEL_PROVIDER", "fake") != "fake"
        or int(environment.get("MODEL_BUDGET_MICROS", "20000000")) > 10000000
    ):
        raise SystemExit(
            "Allocate at most CNY 10 to the business configuration and keep its provider fake "
            "before starting the separate CNY 10 evaluation purse."
        )
    key_name = "MINIMAX_API_KEY" if args.provider == "minimax" else "DEEPSEEK_API_KEY"
    if not environment.get(key_name):
        raise SystemExit("UNRUN: provider credential missing")
    os.environ[key_name] = environment[key_name].strip().strip('"').strip("'")
    if environment.get("MINIMAX_BASE_URL"):
        os.environ["MINIMAX_BASE_URL"] = environment["MINIMAX_BASE_URL"]
    manifest = json.loads((DATA / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == digest, name
    cases = json.loads((DATA / (args.split + ".json")).read_text())
    if args.provider == "deepseek":
        cases = [c for c in cases if c["id"] in manifest["deepseek_comparison_ids"]]
    if args.limit:
        cases = cases[: args.limit]
    budget = EvalBudget(OUT)
    try:
        for case in cases:
            if (OUT / f"{args.provider}-{case['id']}.json").exists():
                print(f"Preserving existing result: {case['id']}", flush=True)
                continue
            record = run_case(case, args.provider, budget)
            if record["status"] in {"UNKNOWN", "REJECTED"}:
                break  # Do not amplify an auth, account, network or protocol incident.
    finally:
        print(
            f"Conservative settled plus unresolved reservation: CNY {budget.total() / 1e6:.6f}",
            flush=True,
        )
        budget.close()


if __name__ == "__main__":
    main()
