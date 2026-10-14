"""Verify an exported JSON run without a server, database or model credentials.

Usage: python scripts/verify_run_export.py runs/my-export.json [--anchor FINAL_EVENT_HASH]
The optional separately saved anchor detects a fully rewritten chain. This is
an integrity check, not proof of source authenticity or historical availability.
"""

import argparse
import json
from pathlib import Path

from finreceipts.runtime.codec import implementation_id
from finreceipts.runtime.service import counter_audit
from finreceipts.runtime.store import verify_export


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--anchor")
    args = parser.parse_args()
    bundle = json.loads(args.path.read_text(encoding="utf-8"))
    verify_export(bundle)
    if args.anchor and bundle["events"][-1]["hash"] != args.anchor:
        parser.error("final event hash differs from the separately saved anchor")
    if bundle["run"]["request"]["implementation_id"] != implementation_id():
        parser.error(
            "integrity passed but implementation differs; replay requires the original build"
        )
    result = counter_audit(bundle["run"])
    print(
        json.dumps(
            {"integrity": "PASS", "anchor": bundle["events"][-1]["hash"], "counter_audit": result},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
