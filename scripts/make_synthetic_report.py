"""Render a fictional example; no source company or model output is used."""

from datetime import date
from pathlib import Path

from finreceipts.models import Fact
from finreceipts.report.html import render_report
from finreceipts.verify.verifier import Verifier


def main():
    fact = Fact(
        cik=0,
        entity_name="Synthetic Example Co (fictional)",
        concept="Revenues",
        value=1000.0,
        unit="USD",
        start=date(2023, 1, 1),
        end=date(2023, 12, 31),
        filed=date(2024, 3, 1),
        form="Synthetic",
        accn="synthetic",
        filing_url="https://example.invalid/synthetic-evidence",
    )
    answer = "Revenue was $1,000. The company employed 42 people."
    audit = Verifier({"revenue": {2023: fact}}).audit_text(answer, fiscal_years={2023})
    output = Path("examples/synthetic_receipts.html")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_report(
            title="Synthetic receipts demonstration (fictional data)",
            question="Illustration only: all company facts are fictional.",
            answer=answer,
            audit=audit,
        )
        .replace("SEC XBRL filings", "fictional test facts")
        .replace("EDGAR ↗", "Fictional evidence ↗")
        .replace("a value in a public filing", "a fictional test value")
        .replace("always check the original filing", "this example is not a real filing"),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
