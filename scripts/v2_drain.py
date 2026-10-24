"""Observe accepted synthetic load tasks until terminal; never cancel or reset them."""

import argparse
import json
import time
from pathlib import Path

from v2_load import ROOT, percentile, sql


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    load = json.loads(args.report.read_text(encoding="utf-8"))
    assert load["mode"] in {"burst", "queue"}
    begin, end = int(load["started_at"] * 1000), int(load["finished_at"] * 1000)
    selection = (
        "FROM tasks t JOIN batches b ON b.id=t.batch_id "
        f"WHERE b.title LIKE 'load-fake-%' AND t.created_at BETWEEN {begin} AND {end}"
    )
    samples, started = [], time.monotonic()
    while True:
        counts = {
            line.split("\t")[0]: int(line.split("\t")[1])
            for line in sql("SELECT t.status,COUNT(*) " + selection + " GROUP BY t.status;")
            .strip()
            .splitlines()
        }
        pending = sum(counts.get(state, 0) for state in ("QUEUED", "READY", "RUNNING"))
        samples.append({"time": time.time(), "states": counts, "pending": pending})
        if pending == 0 or time.monotonic() - started >= args.timeout:
            break
        time.sleep(5)
    records = [
        line.split("\t")
        for line in sql(
            "SELECT t.status,t.tenant_id,t.created_at,t.updated_at,"
            "(SELECT MIN(e.created_at) FROM events e WHERE e.task_id=t.id "
            "AND e.kind='step_claimed') " + selection + ";"
        )
        .strip()
        .splitlines()
    ]
    completed = [r for r in records if r[0] == "COMPLETED"]
    elapsed = (
        (max(int(r[3]) for r in completed) - min(int(r[2]) for r in records)) / 1000
        if completed
        else None
    )
    expected = load["success"] * (5 if load["mode"] == "burst" else 1)
    report = {
        "load_report": args.report.relative_to(ROOT).as_posix(),
        "status": "PASS"
        if len(records) == expected == len(completed) and not pending
        else "PARTIAL",
        "expected_accepted_tasks": expected,
        "observed_tasks": len(records),
        "completed_tasks": len(completed),
        "final_states": counts,
        "completed_by_tenant": {
            tenant: sum(r[1] == tenant for r in completed) for tenant in ("alpha", "beta")
        },
        "first_accept_to_last_completion_seconds": elapsed,
        "complete_tasks_per_second": len(completed) / elapsed if elapsed else None,
        "end_to_end_p95_ms": percentile([int(r[3]) - int(r[2]) for r in completed], 0.95),
        "first_claim_wait_p95_ms": percentile(
            [int(r[4]) - int(r[2]) for r in records if r[4] != "NULL"], 0.95
        ),
        "post_first_claim_p95_ms": percentile(
            [int(r[3]) - int(r[4]) for r in completed if r[4] != "NULL"], 0.95
        ),
        "samples": samples,
        "finished_at": time.time(),
    }
    dest = args.report.with_name(args.report.stem + "-drain.json")
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}))


if __name__ == "__main__":
    main()
