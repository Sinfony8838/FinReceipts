#!/usr/bin/env python3
"""Record A-share / cninfo HTTP responses into tests/fixtures (fetch only, no redistribute)."""

from __future__ import annotations

import shutil
from pathlib import Path

from finreceipts.config import Settings
from finreceipts.tools.akshare_a import DEFAULT_A_SHARE_UNIVERSE, AShareClient
from finreceipts.tools.cninfo import CninfoClient
from finreceipts.tools.http_cache import JsonCache

ROOT = Path(__file__).resolve().parents[1]
ASHARE_FIX = ROOT / "tests" / "fixtures" / "ashare"
CNINFO_FIX = ROOT / "tests" / "fixtures" / "cninfo"
# Small fixture universe for offline CI (subset of DEFAULT_A_SHARE_UNIVERSE).
FIXTURE_CODES = ["600519", "300750", "600036", "002594", "601318", "600276"]


def main() -> None:
    ASHARE_FIX.mkdir(parents=True, exist_ok=True)
    CNINFO_FIX.mkdir(parents=True, exist_ok=True)
    settings = Settings(offline=False, cache_dir=ASHARE_FIX)
    with AShareClient(settings, cache=JsonCache(ASHARE_FIX), mode="refresh") as c:
        for code in FIXTURE_CODES:
            name = DEFAULT_A_SHARE_UNIVERSE.get(code, code)
            cf = c.companyfacts(code)
            n = sum(len(v) for v in cf["facts"].values())
            print(f"ashare {code} {name}: {n} annual metric rows")
    from datetime import date

    with CninfoClient(settings, cache=JsonCache(CNINFO_FIX), mode="refresh") as cn:
        for code in FIXTURE_CODES[:3]:
            rows = cn.annual_report_announcements(
                code, start=date(2022, 1, 1), end=date(2025, 12, 31)
            )
            print(f"cninfo {code}: {len(rows)} annual announcements")
    # Copy into .cache for local gen-eval convenience
    cache_a = ROOT / ".cache" / "ashare"
    cache_a.mkdir(parents=True, exist_ok=True)
    for p in ASHARE_FIX.glob("*.json.gz"):
        shutil.copy2(p, cache_a / p.name)
    print("done")


if __name__ == "__main__":
    main()
