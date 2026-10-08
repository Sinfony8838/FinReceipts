"""Build small, offline test fixtures from the local SEC cache.

Keeps only the XBRL tags FinReceipts uses for a few companies so the fixtures
stay small (<200 KB) while remaining real SEC data. Run after `finreceipts record`.
"""

from __future__ import annotations

from pathlib import Path

from finreceipts.tools.edgar import COMPANYFACTS_URL, TICKERS_URL, EdgarClient
from finreceipts.tools.http_cache import JsonCache
from finreceipts.xbrl.metrics import all_xbrl_tags

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sec"
TICKERS = ("AAPL", "NVDA")


def main() -> None:
    out = JsonCache(FIXTURES)
    tags = all_xbrl_tags()
    with EdgarClient(mode="replay") as c:
        tickers = c.get_json(TICKERS_URL)
        keep = {k: v for k, v in tickers.items() if v["ticker"] in TICKERS}
        out.put(TICKERS_URL, keep)
        for t in TICKERS:
            cik = c.ticker_to_cik(t)
            cf = c.companyfacts(cik)
            gaap = cf["facts"]["us-gaap"]
            trimmed = {
                "cik": cf["cik"],
                "entityName": cf["entityName"],
                "facts": {"us-gaap": {k: v for k, v in gaap.items() if k in tags}},
            }
            out.put(COMPANYFACTS_URL.format(cik=cik), trimmed)
            print(t, len(trimmed["facts"]["us-gaap"]), "tags")


if __name__ == "__main__":
    main()
