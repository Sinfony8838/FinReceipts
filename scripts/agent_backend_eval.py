"""prepare -> offline dev -> freeze -> offline frozen; live requires an explicit config."""

import argparse
import json
from pathlib import Path

from finreceipts.evals.agent_backend import freeze, prepare, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "freeze"])
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--dev", type=Path)
    parser.add_argument("--split", choices=["dev", "frozen"], default="dev")
    parser.add_argument("--live-config", type=Path)
    parser.add_argument("--history", type=Path, default=Path("evals/experiments/20261016-workflow"))
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare(args.suite, args.history)
    elif args.action == "freeze":
        if args.dev is None:
            parser.error("--dev is required")
        result = freeze(args.suite, args.dev)
    else:
        if args.out is None:
            parser.error("--out is required")
        config = (
            json.loads(args.live_config.read_text(encoding="utf-8")) if args.live_config else None
        )
        result = run(args.suite, args.out, args.split, config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if isinstance(result, dict) and result.get("complete") is False:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
