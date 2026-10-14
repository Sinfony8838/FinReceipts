"""Scope checks with retained real SEC facts and generic boundary examples."""

import pytest

from finreceipts.models import Verdict
from finreceipts.verify.verifier import Verifier


def test_explicit_revenue_decline_uses_the_named_metric_and_period(aapl_lookup):
    (result,) = Verifier(aapl_lookup).audit_text(
        "The Company's total net sales decreased 3% during 2023 compared to 2022."
    )
    assert result.ok
    assert result.claim.metric == "year-over-year growth in revenue"
    assert result.claim.entity == "Apple Inc."
    assert [str(f.end) for f in result.evidence.facts] == ["2023-09-30", "2022-09-24"]
    assert all(f.concept.startswith("Revenue") for f in result.evidence.facts)


@pytest.mark.parametrize("modifier", ["Regional", "Subsidiary", "Adjusted", "Non-GAAP"])
def test_unresolved_modifiers_never_fall_back_to_numeric_collision(aapl_lookup, modifier):
    (result,) = Verifier(aapl_lookup).audit_text(f"{modifier} revenue in 2023 was $383.3 billion.")
    assert result.verdict is Verdict.UNSUPPORTED
    assert result.evidence is None
    assert result.reasons[0].startswith("text scope unresolved:")


def test_wrong_explicit_year_cannot_use_correct_number_from_another_year(aapl_lookup):
    (result,) = Verifier(aapl_lookup).audit_text("Revenue in 2022 was $383.3 billion.")
    assert result.verdict is Verdict.CONTRADICTED
    assert str(result.evidence.facts[0].end) == "2022-09-24"
    assert "value differs" in result.reasons[0]


def test_requested_year_must_agree_with_text(aapl_lookup):
    (result,) = Verifier(aapl_lookup).audit_text(
        "Revenue in 2023 was $383.3 billion.", fiscal_years={2022}
    )
    assert not result.ok
    assert result.reasons == ["text fiscal year conflicts with requested scope"]


def test_ambiguous_two_year_amounts_abstain(aapl_lookup):
    results = Verifier(aapl_lookup).audit_text(
        "Revenue in 2023 was $383.3 billion and in 2022 was $394.3 billion."
    )
    assert len(results) == 2
    assert all(not result.ok for result in results)


def test_absolute_change_is_not_a_direct_revenue_amount(aapl_lookup):
    (result,) = Verifier(aapl_lookup).audit_text(
        "Revenue decreased $11.0 billion during 2023 compared to 2022."
    )
    assert not result.ok
    assert "difference metric" in result.reasons[0]


def test_later_growth_word_does_not_relabel_an_earlier_direct_amount(aapl_lookup):
    results = Verifier(aapl_lookup).audit_text(
        "Revenue for fiscal year 2023 was $383.3 billion, down 3% from a year ago."
    )
    assert len(results) == 2 and all(result.ok for result in results)
    assert results[0].claim.metric == "revenue"
    assert results[1].claim.metric == "year-over-year growth in revenue"


def test_missing_year_abstains_and_legacy_matching_requires_explicit_opt_in(aapl_lookup):
    (result,) = Verifier(aapl_lookup).audit_text("Revenue was $383.3 billion.")
    assert not result.ok
    assert result.reasons == ["text scope unresolved: no explicit fiscal year"]
    (legacy,) = Verifier(aapl_lookup).audit_text("Revenue was $383.3 billion.", numeric_only=True)
    assert legacy.ok and legacy.claim.metric is None


@pytest.mark.parametrize(
    "text",
    [
        "Revenue in 2023 was up 3%.",
        "Revenue in 2023 was $383.3 billion and $97.0 billion.",
        "Revenue in 2023 was $383.3 billion and CNY 383.3 billion.",
        "Revenue in 2023 was 383.3 billion.",
        "Unrecognized metric in 2023 was $383.3 billion.",
    ],
)
def test_ambiguous_operations_amounts_units_and_metrics_abstain(aapl_lookup, text):
    results = Verifier(aapl_lookup).audit_text(text)
    assert results and all(not r.ok for r in results)
    assert all(r.evidence is None for r in results)


def test_corporate_abbreviation_does_not_erase_a_wrong_subject(aapl_lookup):
    (result,) = Verifier(aapl_lookup).audit_text(
        "Another Corp. Revenue in 2023 was $383.3 billion."
    )
    assert not result.ok
    assert "entity or dimensional modifier" in result.reasons[0]


def test_eps_alias_is_case_insensitive(aapl_lookup):
    (result,) = Verifier(aapl_lookup).audit_text("Diluted EPS in 2023 was $6.13.")
    assert result.ok
    assert result.claim.metric == "diluted EPS"


@pytest.mark.parametrize(
    "text",
    [
        "Revenue for Q2 2023 was $383.3 billion.",
        "Revenue for the first quarter of 2023 was $383.3 billion.",
        "Revenue from the Europe business in 2023 was $383.3 billion.",
        "Revenue for a subsidiary in 2023 was $383.3 billion.",
        "Revenue for another issuer in 2023 was $383.3 billion.",
        "Revenue excluding charges in 2023 was $383.3 billion.",
        "Revenue in 2023 was $383.3 billion under IFRS.",
        "Revenue in 2023 was never $383.3 billion.",
        "Revenue in 2023 might reach $383.3 billion.",
        "Revenue in 2023 would have been $383.3 billion.",
        "Revenue in 2023 was $383.3 billion, with 2022 on another page.",
        "Revenue in 2023 decreased 3 percentage points year-over-year.",
        "Other Issuer reported results and revenue in 2023 was $383.3 billion.",
        "Other Issuer reported results. Its revenue in 2023 was $383.3 billion.",
        (
            "Revenue for Other Issuer in 2023 was $383.3 billion. "
            "Its revenue in 2023 was $383.3 billion."
        ),
        (
            "Other Issuer reported results. Revenue in 2023 was $383.3 billion. "
            "Its revenue in 2023 was $383.3 billion."
        ),
        "Revenue was N0 and net income in 2023 was $383.3 billion.",
        "Revenue in 2023 was $383.3 billion and net income in 2022 was $99.8 billion.",
    ],
)
def test_unparsed_claim_metadata_never_borrows_an_annual_amount(aapl_lookup, text):
    rows = Verifier(aapl_lookup).audit_text(text)
    assert rows and all(r.verdict is Verdict.UNSUPPORTED and r.evidence is None for r in rows)


@pytest.mark.parametrize("separator", ["; ", ". ", "\n"])
def test_direction_is_local_to_current_metric_clause(aapl_lookup, separator):
    rows = Verifier(aapl_lookup).audit_text(
        "Revenue fell 3%" + separator + "Net income rose 2.8% year-over-year in 2023."
    )
    assert rows[-1].verdict is Verdict.CONTRADICTED
    assert rows[-1].evidence.expected_value < 0
    assert not any("sign inferred" in r for r in rows[-1].reasons)


def test_direction_is_local_with_coordinated_metrics(aapl_lookup):
    rows = Verifier(aapl_lookup).audit_text(
        "Revenue fell 3% year-over-year and net income rose 2.8% year-over-year in 2023."
    )
    assert [r.verdict for r in rows] == [Verdict.SUPPORTED, Verdict.CONTRADICTED]


def test_per_share_amount_cannot_verify_total_currency_amount(aapl_lookup):
    from finreceipts.models import Claim, Evidence

    fact = aapl_lookup["revenue"][2023]
    row = Verifier(aapl_lookup).verify_against(
        Claim(text="per-share revenue", value=fact.value, unit="USD/shares"),
        Evidence(facts=[fact], expected_value=fact.value, expected_unit="USD"),
    )
    assert row.verdict is Verdict.CONTRADICTED
    assert row.reasons == ["unit mismatch: USD/shares vs USD"]


@pytest.mark.parametrize("literal", ["USD 3 percent", "$3%", "CNY 3 percent", "usd 3%"])
def test_same_quantity_conflicting_units_abstain(aapl_lookup, literal):
    (row,) = Verifier(aapl_lookup).audit_text(f"Revenue in 2023 declined {literal} year-over-year.")
    assert row.verdict is Verdict.UNSUPPORTED and row.evidence is None
    assert row.reasons == [
        "text scope unresolved: currency and percentage markers conflict on one quantity"
    ]


def test_independent_amount_and_ratio_remain_supported(aapl_lookup):
    rows = Verifier(aapl_lookup).audit_text(
        "Revenue in 2023 was USD 383.3 billion, down 3 percent from a year ago."
    )
    assert len(rows) == 2 and all(row.ok for row in rows)


def test_only_conflicting_quantity_is_rejected(aapl_lookup):
    rows = Verifier(aapl_lookup).audit_text(
        "Revenue in 2023 was USD 383.3 billion, down USD 3 percent from a year ago."
    )
    assert [row.verdict for row in rows] == [Verdict.SUPPORTED, Verdict.UNSUPPORTED]
    assert rows[1].evidence is None
