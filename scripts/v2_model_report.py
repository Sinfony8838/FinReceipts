"""Summarize stored model evidence without making any new paid calls."""

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals/platform_v2_20261024"
LIVE = ROOT / "runs/platform-v2-live"
report = {
    "scope": "Numeric final-target contract; prose correctness is not certified.",
    "groups": {},
}
for provider, split in [("minimax", "dev"), ("minimax", "frozen"), ("deepseek", "frozen")]:
    cases = json.loads((DATA / f"{split}.json").read_text(encoding="utf-8"))
    if provider == "deepseek":
        selected = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))[
            "deepseek_comparison_ids"
        ]
        cases = [c for c in cases if c["id"] in selected]
    totals = Counter(total=len(cases))
    details = []
    for case in cases:
        path = LIVE / f"{provider}-{case['id']}.json"
        if not path.exists():
            totals["UNRUN"] += 1
            continue
        row = json.loads(path.read_text(encoding="utf-8"))
        totals[row["status"]] += 1
        totals["http_attempts"] += len(row["calls"])
        totals["elapsed_seconds_sum"] += row["elapsed_seconds"]
        for message in row.get("messages", []):
            content = message.get("content")
            bodies = (
                [content]
                if message.get("role") == "tool"
                else [b.get("content") for b in content if b.get("type") == "tool_result"]
                if isinstance(content, list)
                else []
            )
            for body in bodies:
                result = json.loads(body)
                if isinstance(result, dict) and result.get("error"):
                    totals["tool_error_results"] += 1
                    code = result["error"]
                    totals[
                        "tool_scope_denied"
                        if code == "tool_scope_denied"
                        else "tool_missing_evidence"
                        if code.startswith("no ")
                        else "tool_other_error"
                    ] += 1
        for call in row["calls"]:
            totals["estimated_micros"] += call.get("estimated_micros", 0)
            usage = call.get("response", {}).get("usage", {})
            totals["input_tokens"] += usage.get("input_tokens", 0)
            totals["output_tokens"] += usage.get("output_tokens", 0)
            totals["tool_calls"] += len(call.get("response", {}).get("tools", []))
        missing = case["request"]["target"]["fiscal_year"] == 2099
        answers = row["answers"]
        for label, index in [("initial", 0), ("final", -1)]:
            if answers:
                answer = answers[index]
                supported = answer["audit"]["verdict"].upper() == "SUPPORTED"
                # A missing-evidence answer passes only by explicit final abstention.
                abstained = answer["answer"].strip().splitlines()[-1].upper() == "ANSWER: N/A"
                totals[label + "_target_pass"] += int(abstained if missing else supported)
                totals[label + "_supported"] += int(supported)
                totals[label + "_missing_rejected"] += int(missing and not supported)
                totals[label + "_false_accept"] += int(missing and supported)
        if answers:
            shared_calls = answers[0]["attempts_so_far"]
            totals["shared_draft_attempts"] += shared_calls
            totals["incremental_repair_attempts"] += len(row["calls"]) - shared_calls
            totals["shared_draft_micros"] += sum(
                c.get("estimated_micros", 0) for c in row["calls"][:shared_calls]
            )
            totals["incremental_repair_micros"] += sum(
                c.get("estimated_micros", 0) for c in row["calls"][shared_calls:]
            )
        details.append(
            {
                "id": case["id"],
                "status": row["status"],
                "artifact": str(path.relative_to(ROOT)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    report["groups"][provider + "_" + split] = {"totals": dict(totals), "items": details}
entries = [json.loads(s) for s in (LIVE / "budget.jsonl").read_text(encoding="utf-8").splitlines()]
latest = {r["id"]: r for r in entries}
report["budget"] = {
    "authorized_total_cny": 20,
    "evaluation_purse_cny": 10,
    "business_ceiling_cny": 10,
    "settled_estimate_cny": sum(r["micros"] for r in latest.values() if r["state"] == "SETTLED")
    / 1e6,
    "unresolved_reserved_cny": sum(r["micros"] for r in latest.values() if r["state"] != "SETTLED")
    / 1e6,
    "note": "CNY 3/12 per million input/output tokens, input uncached; not a supplier invoice.",
}
report["limitations"] = [
    "Small, targeted numeric dataset; no general finance quality claim.",
    "Verification uses the same deterministic policy as the system, not independent human labels.",
    "The paired initial draft is shared, not charged three times.",
    "Historical artifacts are preserved; implementation IDs are recorded per case.",
]
destination = LIVE / "report.json"
destination.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print(
    json.dumps(
        {
            "groups": {k: v["totals"] for k, v in report["groups"].items()},
            "budget": report["budget"],
        },
        ensure_ascii=False,
    )
)
