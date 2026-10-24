"""Freeze a separate 36-case SEC dataset, splitting by company before model use."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from finreceipts.platform.engine import prepare
from finreceipts.runtime.codec import canonical
from finreceipts.xbrl.metrics import lookup_from_companyfacts

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "evals" / "platform_v2_20261024"
COMPANIES = {
    "dev": [("AAPL", 320193), ("MSFT", 789019), ("NVDA", 1045810)],
    "frozen": [
        ("GOOGL", 1652044),
        ("AMZN", 1018724),
        ("META", 1326801),
        ("ADBE", 796343),
        ("CRM", 1108524),
        ("ORCL", 1341439),
    ],
}


def main():
    if (DEST / "manifest.json").exists():
        raise SystemExit("Already frozen; do not overwrite this dataset version.")
    DEST.mkdir(parents=True, exist_ok=True)
    sources = []
    with httpx.Client(
        headers={"User-Agent": "FinReceipts research project contact@example.com"}, timeout=45
    ) as client:
        for split, companies in COMPANIES.items():
            rows = []
            for ticker, cik in companies:
                source = DEST / (ticker + ".source.json")
                url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
                if not source.exists():
                    response = client.get(url)
                    response.raise_for_status()
                    source.write_bytes(response.content)
                raw = source.read_bytes()
                sources.append(
                    {
                        "ticker": ticker,
                        "split": split,
                        "url": url,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
                lookup = lookup_from_companyfacts(json.loads(raw))
                targets = [
                    {"kind": "direct", "metric": "revenue", "fiscal_year": 2023},
                    {
                        "kind": "ratio",
                        "metric": "net income",
                        "denominator": "revenue",
                        "fiscal_year": 2023,
                    },
                    {"kind": "yoy_growth", "metric": "revenue", "fiscal_year": 2023},
                    # Explicit missing evidence negative control, same frozen company source.
                    {"kind": "direct", "metric": "revenue", "fiscal_year": 2099},
                ]
                for i, target in enumerate(targets):
                    request = {
                        "kind": "research",
                        "ticker": ticker,
                        "target": target,
                        "question": (
                            f"Report the requested annual financial target for {ticker}; "
                            "briefly explain the calculation and cite filing evidence. "
                            "If unavailable, refuse to invent a number."
                        ),
                    }
                    prepared = prepare(request, lookup)
                    rows.append(
                        {
                            "id": f"{ticker}-{i + 1}",
                            "request": request,
                            "evidence": prepared["evidence"],
                            "source_sha256": sources[-1]["sha256"],
                        }
                    )
            (DEST / (split + ".json")).write_text(canonical(rows) + "\n", encoding="utf-8")
    files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in DEST.iterdir() if p.is_file()
    }
    manifest = {
        "version": "platform-v2-20261024",
        "frozen_at": datetime.now(UTC).isoformat(),
        "company_split": COMPANIES,
        "sources": sources,
        "files": files,
        "deepseek_comparison_ids": [
            f"{t}-{i}" for t, _ in COMPANIES["frozen"][:3] for i in range(1, 5)
        ],
        "scope": (
            "27 supported-target candidates and 9 missing-evidence controls; "
            "no model result informed selection."
        ),
    }
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Frozen 12 development and 24 test cases across disjoint companies.")


if __name__ == "__main__":
    main()
