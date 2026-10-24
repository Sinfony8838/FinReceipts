"""Run the deterministic route on the frozen SEC evidence, with zero model calls."""

import hashlib
import json
import time
from pathlib import Path

from finreceipts.platform.engine import prepare
from finreceipts.runtime.codec import decode_lookup, implementation_id

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals/platform_v2_20261024"


def main():
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == digest
    records = []
    for split in ("dev", "frozen"):
        for case in json.loads((DATA / f"{split}.json").read_text(encoding="utf-8")):
            started = time.perf_counter()
            outcome = prepare(
                {**case["request"], "kind": "metric"}, decode_lookup(case["evidence"])
            )
            records.append(
                {
                    "case_id": case["id"],
                    "split": split,
                    "target": case["request"]["target"],
                    "source_sha256": case["source_sha256"],
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "machine_verdict": outcome["machine_verdict"],
                    "result": outcome["result"],
                    "next_step": outcome["next_step"],
                }
            )
    assert all(r["next_step"] == "DONE" for r in records)
    report = {
        "scope": (
            "Deterministic numeric route on versioned SEC evidence; same verifier, "
            "no prose generation or independent human labels."
        ),
        "date": time.time(),
        "implementation_id": implementation_id(),
        "items": records,
        "http_attempts": 0,
        "model_tokens": 0,
        "model_cost_micros": 0,
        "supported": sum(r["machine_verdict"] == "SUPPORTED" for r in records),
        "missing_refusals": sum(r["machine_verdict"] == "UNSUPPORTED" for r in records),
        "total": len(records),
    }
    path = ROOT / "runs/v2-rules-evaluation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "items"}))


if __name__ == "__main__":
    main()
