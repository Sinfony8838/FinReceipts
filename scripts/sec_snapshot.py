"""Build or validate a companyfacts observation bundle. No LLM calls."""

from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path

from finreceipts.pit.sec_snapshot import capture_snapshot, validate_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--output", type=Path, required=True)
    inputs = capture.add_mutually_exclusive_group(required=True)
    inputs.add_argument(
        "--local-json", type=Path, help="JSON or .gz; re-observed now, never backdated"
    )
    inputs.add_argument(
        "--cik", type=int, help="One live SEC request; needs authorized User-Agent env"
    )
    validate = commands.add_parser("validate")
    validate.add_argument("bundle", type=Path)
    args = parser.parse_args()
    if args.command == "capture":
        if args.cik is not None and os.environ.get("FINRECEIPTS_OFFLINE", "").strip() in {
            "1",
            "true",
            "yes",
        }:
            parser.error("live capture conflicts with FINRECEIPTS_OFFLINE; use --local-json")
        raw = None
        if args.local_json is not None:
            opener = gzip.open if args.local_json.suffix == ".gz" else open
            with opener(args.local_json, "rb") as stream:
                raw = stream.read(32 * 1024 * 1024 + 1)
        capture_snapshot(
            args.output,
            local_bytes=raw,
            cik=args.cik,
            user_agent=os.environ.get("FINRECEIPTS_SEC_USER_AGENT"),
        )
        bundle = args.output
    else:
        bundle = args.bundle
    print(json.dumps(validate_snapshot(bundle), indent=2))


if __name__ == "__main__":
    main()
