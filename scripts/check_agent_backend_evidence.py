"""Read-only recomputation of saved contract results and physical call counts."""

import argparse
import json
from pathlib import Path

from finreceipts.evals.agent_backend import STRATEGIES, answer_matches, file_sha, load_suite


def check(suite, output):
    cases, _, _ = load_suite(suite)
    hashes = json.loads((output / "SHA256SUMS.json").read_text(encoding="utf-8"))
    if not hashes or any(
        Path(name).name != name or file_sha(output / name) != sha for name, sha in hashes.items()
    ):
        raise ValueError("output hash mismatch")
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    rows = json.loads((output / "results.json").read_text(encoding="utf-8"))
    trace = [
        json.loads(line)
        for line in (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    if summary["manifest_sha256"] != file_sha(suite / "manifest.json"):
        raise ValueError("suite binding mismatch")
    selected = {
        c["id"]: c
        for c in cases
        if c["split"] == summary["split"]
        and (summary["evidence_type"] != "real_model" or c["scenario"] == "normal")
    }
    seen = set()
    for row in rows:
        key = (row["id"], row["strategy"])
        if row["id"] not in selected or row["strategy"] not in STRATEGIES or key in seen:
            raise ValueError("duplicate or out-of-split result")
        seen.add(key)
        case = selected[row["id"]]
        saved_case = json.loads(
            (output / (row["id"].replace(":", "_") + ".json")).read_text(encoding="utf-8")
        )
        if row not in saved_case:
            raise ValueError("per-case output mismatch")
        if row["status"] == "completed":
            phases = {"initial", "repair"} if row["strategy"] == "bounded_revision" else {"initial"}
            outputs = [
                event["response"]["text"]
                for event in trace
                if event.get("case_id") == row["id"]
                and event["kind"] == "model"
                and event["status"] == "completed"
                and event["phase"] in phases
                and not event["response"]["tool_calls"]
            ]
            if not outputs or row["answer"] != outputs[-1]:
                raise ValueError("answer not bound to raw model response")
            matched = answer_matches(case, row["answer"])
            if matched != row["numeric_matched"] or matched != row["contract_passed"]:
                raise ValueError("answer score mismatch")
        elif row["status"] == "rejected" and row["contract_passed"] != (
            case["scenario"] == "missing_evidence"
        ):
            raise ValueError("rejection score mismatch")
    for strategy in STRATEGIES:
        group = [r for r in rows if r["strategy"] == strategy]
        saved = summary["strategies"][strategy]
        if saved["success"] != sum(r["contract_passed"] for r in group) or saved["total"] != len(
            selected
        ):
            raise ValueError("summary denominator mismatch")
        latencies = sorted(r["latency_s"] for r in group if r["latency_s"] is not None)
        if latencies != saved["latencies_s"] or len(latencies) != saved["latency_n"]:
            raise ValueError("raw latency distribution mismatch")
        direct = {r["id"]: r for r in rows if r["strategy"] == "direct"}
        if strategy == "audit_only" and any(
            r["answer"] != direct[r["id"]]["answer"] for r in group
        ):
            raise ValueError("paired initial answer changed")
    for kind, field in (
        ("model", "actual_model_dispatches"),
        ("tool", "tool_attempts"),
        ("http", "http_attempts"),
    ):
        count = sum(
            r["kind"] == kind and r["status"] == ("reserved" if kind == "http" else "started")
            for r in trace
        )
        if count != summary[field]:
            raise ValueError("physical attempt count mismatch")
    if [r["seq"] for r in trace] != list(range(1, len(trace) + 1)):
        raise ValueError("trace sequence mismatch")
    return {
        "status": "PASS",
        "evidence_type": summary["evidence_type"],
        "split": summary["split"],
        "results": len(rows),
        "actual_model_dispatches": summary["actual_model_dispatches"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.suite, args.output), ensure_ascii=False, indent=2))
