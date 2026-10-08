from datetime import date

import pytest

from finreceipts.xbrl.metrics import (
    CONCEPTS,
    MetricKind,
    MetricSpec,
    build_lookup,
    compute,
    lookup_from_companyfacts,
)
from tests.conftest import make_fact


def test_direct(aapl_lookup):
    ev = compute(MetricSpec(MetricKind.DIRECT, CONCEPTS["revenue"]), aapl_lookup, 2024)
    assert ev.expected_value == 391_035_000_000


def test_growth(aapl_lookup):
    ev = compute(MetricSpec(MetricKind.YOY_GROWTH, CONCEPTS["revenue"]), aapl_lookup, 2023)
    assert ev.expected_value == pytest.approx((383_285 - 394_328) / 394_328 * 100)
    assert len(ev.facts) == 2


def test_ratio(aapl_lookup):
    spec = MetricSpec(MetricKind.RATIO, CONCEPTS["net income"], CONCEPTS["revenue"])
    ev = compute(spec, aapl_lookup, 2024)
    assert ev.expected_value == pytest.approx(93_736 / 391_035 * 100)
    assert spec.unit == "percent" and "percentage" in spec.name


def test_missing_inputs_return_none(aapl_lookup):
    assert compute(MetricSpec(MetricKind.DIRECT, CONCEPTS["revenue"]), aapl_lookup, 1990) is None
    assert (
        compute(MetricSpec(MetricKind.YOY_GROWTH, CONCEPTS["revenue"]), aapl_lookup, 2007) is None
    )


def test_fallback_tags_merge_by_year():
    facts = {
        "Revenues": [make_fact(10.0, end="2017-09-30", start="2016-10-01", filed="2017-11-01")],
        "RevenueFromContractWithCustomerExcludingAssessedTax": [make_fact(20.0)],
    }
    lk = build_lookup(facts)
    assert lk["revenue"][2017].value == 10.0
    assert lk["revenue"][2023].value == 20.0


def test_lookup_point_in_time(edgar):
    lk = lookup_from_companyfacts(edgar.companyfacts(320193), as_of=date(2023, 12, 31))
    assert max(lk["revenue"]) == 2023
