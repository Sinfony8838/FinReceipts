"""Issuer-before-fiscal-year clauses must bind the same issuer, period and metric."""

import pytest

from finreceipts.models import Verdict
from finreceipts.verify.verifier import Verifier
from finreceipts.xbrl.metrics import lookup_from_companyfacts


@pytest.mark.parametrize(
    "prefix",
    [
        "Apple FY2023",
        "Apple FY 2023",
        "Apple fiscal year 2023",
        "Apple's FY2023",
        "Apple’s fiscal year 2023",
        "Apple Inc. FY2023",
        "Apple Inc.'s FY2023",
        "Apple Inc.’s fiscal year 2023",
        "APPLE fy2023",
        "The Company's FY2023",
        "FY2023 Apple",
        "FY2023, Apple Inc.'s",
    ],
)
def test_supported_subject_period_orders_keep_actual_evidence(aapl_lookup, prefix):
    text = f"{prefix} revenue was $383.285 billion."
    (row,) = Verifier(aapl_lookup).audit_text(text)
    assert row.verdict is Verdict.SUPPORTED
    assert row.claim.entity == "Apple Inc." and row.claim.metric == "revenue"
    assert row.evidence.expected_value == 383_285_000_000
    assert {(f.cik, str(f.end)) for f in row.evidence.facts} == {(320193, "2023-09-30")}
    assert text[slice(*row.claim.span)] == "$383.285 billion"


@pytest.mark.parametrize(
    "prefix",
    [
        "Another Issuer FY2023",
        "Pineapple FY2023",
        "Apple Europe FY2023",
        "Apple's subsidiary FY2023",
        "Adjusted Apple FY2023",
        "Apple FY2023 adjusted",
        "Apple FY2023 non-GAAP",
        "Apple Q4 FY2023",
        "Apple FY2023 Q4",
        "Apple FY2023 estimated",
        "Apple FY2023 and FY2022",
        "Apple FY2023/FY2022",
        "Apple FY2023 compared to FY2022",
        "FY2022 Apple FY2023",
        "Apple FY23",
        "Apple FY20230",
        "Apple FY2023X",
        "Apple calendar year 2023",
        "Apple 2023",
    ],
)
def test_subject_period_cannot_discard_unknown_words_or_periods(aapl_lookup, prefix):
    rows = Verifier(aapl_lookup).audit_text(f"{prefix} revenue was $383.285 billion.")
    assert rows and all(row.verdict is Verdict.UNSUPPORTED and row.evidence is None for row in rows)


@pytest.mark.parametrize(
    "tail",
    [
        "in 2023",  # Duplicate attachment is not silently removed, even if years agree.
        "in 2022",
        "under IFRS",
        "for Europe",
        "for Another Issuer",
    ],
)
def test_subject_period_still_requires_the_entire_body_to_parse(aapl_lookup, tail):
    (row,) = Verifier(aapl_lookup).audit_text(f"Apple FY2023 revenue was $383.285 billion {tail}.")
    assert row.verdict is Verdict.UNSUPPORTED and row.evidence is None


def test_subject_period_uses_named_year_not_another_matching_value(aapl_lookup):
    (row,) = Verifier(aapl_lookup).audit_text("Apple FY2022 revenue was $383.285 billion.")
    assert row.verdict is Verdict.CONTRADICTED
    assert str(row.evidence.facts[0].end) == "2022-09-24"
    (conflict,) = Verifier(aapl_lookup).audit_text(
        "Apple FY2023 revenue was $383.285 billion.", fiscal_years={2022}
    )
    assert conflict.verdict is Verdict.UNSUPPORTED and conflict.evidence is None
    assert conflict.reasons == ["text fiscal year conflicts with requested scope"]


def test_subject_period_preserves_growth_and_direction(aapl_lookup):
    rows = Verifier(aapl_lookup).audit_text(
        "Apple FY2023 revenue was $383.285 billion, down 3% year-over-year."
    )
    assert len(rows) == 2 and all(row.ok for row in rows)
    assert rows[1].claim.metric == "year-over-year growth in revenue"
    assert rows[1].evidence.expected_value < 0
    (wrong,) = Verifier(aapl_lookup).audit_text("Apple FY2023 revenue rose 3% year-over-year.")
    assert wrong.verdict is Verdict.CONTRADICTED


def test_generic_subject_does_not_reset_foreign_antecedent(aapl_lookup):
    (row,) = Verifier(aapl_lookup).audit_text(
        "Another Issuer reported results. Its FY2023 revenue was $383.285 billion."
    )
    assert row.verdict is Verdict.UNSUPPORTED and row.evidence is None
    (explicit,) = Verifier(aapl_lookup).audit_text(
        "Another Issuer reported results. Apple FY2023 revenue was $383.285 billion."
    )
    assert explicit.ok


def test_entity_aliases_come_from_current_evidence_not_apple_constants(edgar):
    lookup = lookup_from_companyfacts(edgar.companyfacts(1045810))
    fact = lookup["revenue"][2023]
    (row,) = Verifier(lookup).audit_text(f"NVIDIA FY2023 revenue was USD {fact.value:,.0f}.")
    assert row.ok and row.claim.entity == fact.entity_name
    assert {f.cik for f in row.evidence.facts} == {1045810}
    (other,) = Verifier(lookup).audit_text(f"Apple FY2023 revenue was USD {fact.value:,.0f}.")
    assert other.verdict is Verdict.UNSUPPORTED and other.evidence is None
