from datetime import date

import pytest

from finreceipts.models import PeriodType
from finreceipts.tools.edgar import parse_concept_facts
from finreceipts.xbrl.facts import annual_series, fiscal_year_label, is_annual, restated_years
from tests.conftest import make_fact


@pytest.mark.parametrize(
    ("end", "fy"),
    [("2023-09-30", 2023), ("2024-01-28", 2024), ("2023-01-01", 2022), ("2023-12-31", 2023)],
)
def test_fiscal_year_label(end, fy):
    assert fiscal_year_label(date.fromisoformat(end)) == fy


def test_is_annual_filters_quarters_and_forms():
    assert is_annual(make_fact(1.0))
    assert not is_annual(make_fact(1.0, start="2023-07-01"))  # quarter
    assert not is_annual(make_fact(1.0, form="10-Q"))
    instant = make_fact(1.0, start=None)
    assert instant.period_type is PeriodType.INSTANT and is_annual(instant)


def test_annual_series_prefers_original_or_latest():
    orig = make_fact(100.0, filed="2023-11-03", accn="a")
    restated = make_fact(110.0, filed="2024-11-01", accn="b")
    assert annual_series([orig, restated], prefer="original")[2023].value == 100.0
    assert annual_series([orig, restated], prefer="latest")[2023].value == 110.0
    with pytest.raises(ValueError):
        annual_series([orig], prefer="whatever")


def test_annual_series_point_in_time():
    orig = make_fact(100.0, filed="2023-11-03")
    restated = make_fact(110.0, filed="2024-11-01")
    assert (
        annual_series([orig, restated], as_of=date(2024, 1, 1), prefer="latest")[2023].value == 100
    )
    assert annual_series([orig, restated], as_of=date(2023, 10, 1)) == {}


def test_real_apple_revenue(edgar):
    facts = parse_concept_facts(
        edgar.companyfacts(320193),
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        unit="USD",
    )
    original = annual_series(facts, prefer="original")[2023]
    assert original.value == 383_285_000_000
    assert original.end == date(2023, 9, 30)
    assert original.accn == "0000320193-23-000106"  # FY2023 10-K
    latest = annual_series(facts)[2023]  # same value, re-reported as a comparative later
    assert latest.value == original.value and latest.filed > original.filed


def test_real_nvidia_eps_split_restatement(edgar):
    facts = parse_concept_facts(edgar.companyfacts(1045810), "EarningsPerShareDiluted")
    assert annual_series(facts, prefer="original")[2024].value == pytest.approx(11.93)
    # restated for the June 2024 10-for-1 stock split in later filings
    assert annual_series(facts, prefer="latest")[2024].value == pytest.approx(1.19)


def test_restated_years_detects_changes(edgar):
    facts = parse_concept_facts(edgar.companyfacts(320193), "NetIncomeLoss", unit="USD")
    assert 2008 in restated_years(facts)
