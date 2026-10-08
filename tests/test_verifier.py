from datetime import date

import pytest

from finreceipts.models import Claim, Evidence, Verdict
from finreceipts.verify.numbers import parse_number
from finreceipts.verify.verifier import Tolerance, Verifier, check_value
from tests.conftest import make_fact

REV = 383_285_000_000.0


@pytest.mark.parametrize(
    "claimed",
    [383_285_000_000.0, 383.3e9, 383.29e9, 383_285e6 * 1.004],
)
def test_supported_within_tolerance(claimed):
    verdict, _, _ = check_value(claimed, REV, claimed_unit="USD", expected_unit="USD")
    assert verdict is Verdict.SUPPORTED


def test_out_of_tolerance_is_contradicted():
    verdict, rel, reasons = check_value(390e9, REV, claimed_unit="USD", expected_unit="USD")
    assert verdict is Verdict.CONTRADICTED
    assert rel == pytest.approx(abs(390e9 - REV) / REV)
    assert "value differs" in reasons[0]


def test_rounding_tolerance_accepts_coarse_display():
    m = parse_number("$383 billion")
    verdict, _, _ = check_value(
        m.value, REV, claimed_unit="USD", expected_unit="USD", rounding_step=m.rounding_step
    )
    assert verdict is Verdict.SUPPORTED


def test_rounding_can_be_disabled():
    m = parse_number("$383 billion")
    verdict, _, _ = check_value(
        m.value,
        REV,
        claimed_unit="USD",
        expected_unit="USD",
        rounding_step=m.rounding_step,
        tol=Tolerance(rel=0.0001, rounding=False),
    )
    assert verdict is Verdict.CONTRADICTED


@pytest.mark.parametrize(("claimed", "factor"), [(383_285_000.0, "0.001"), (383_285e12, "1e+06")])
def test_scale_errors_are_named(claimed, factor):
    verdict, _, reasons = check_value(claimed, REV, claimed_unit="USD", expected_unit="USD")
    assert verdict is Verdict.CONTRADICTED
    assert any("scale error" in r and factor in r for r in reasons)


def test_sign_error():
    verdict, _, reasons = check_value(-REV, REV)
    assert verdict is Verdict.CONTRADICTED
    assert "sign error" in reasons


def test_unit_mismatch():
    verdict, _, reasons = check_value(2.8, 2.8, claimed_unit="percent", expected_unit="USD")
    assert verdict is Verdict.CONTRADICTED
    assert "unit mismatch" in reasons[0]


def test_unknown_unit_is_compatible():
    verdict, _, _ = check_value(REV, REV, claimed_unit=None, expected_unit="USD")
    assert verdict is Verdict.SUPPORTED


def test_eps_written_in_dollars_matches_usd_per_share():
    verdict, _, _ = check_value(6.13, 6.13, claimed_unit="USD", expected_unit="USD/shares")
    assert verdict is Verdict.SUPPORTED


def test_percentage_absolute_tolerance():
    assert check_value(-2.8, -2.80, expected_unit="percent")[0] is Verdict.SUPPORTED
    assert check_value(-2.84, -2.80, expected_unit="percent")[0] is Verdict.SUPPORTED
    assert check_value(-2.9, -2.80, expected_unit="percent")[0] is Verdict.CONTRADICTED


def test_decline_wording_flips_sign():
    m = parse_number("down 2.8%")
    assert m is not None and m.value == 2.8
    # parse_number drops context; build from a sentence instead
    from finreceipts.verify.numbers import extract_numbers

    (m,) = extract_numbers("revenue fell 2.8% from the prior year")
    verdict, _, reasons = check_value(
        m.value,
        -2.8,
        claimed_unit=m.unit,
        expected_unit="percent",
        negative_context=m.negative_context,
    )
    assert verdict is Verdict.SUPPORTED
    assert "sign inferred" in reasons[0]


def test_decline_wording_without_negative_expected_is_not_flipped():
    verdict, _, _ = check_value(2.8, 2.8, expected_unit="percent", negative_context=True)
    assert verdict is Verdict.SUPPORTED


def test_zero_expected():
    assert check_value(0.0, 0.0)[0] is Verdict.SUPPORTED
    assert check_value(1.0, 0.0)[0] is Verdict.CONTRADICTED


def _ev(value=REV, end="2023-09-30"):
    return Evidence(facts=[make_fact(value, end=end)], expected_value=value, expected_unit="USD")


def test_verify_against_period_mismatch():
    v = Verifier({})
    claim = Claim(text="$383.3 billion", value=383.3e9, unit="USD", period_end=date(2022, 9, 24))
    res = v.verify_against(claim, _ev())
    assert res.verdict is Verdict.CONTRADICTED
    assert "period mismatch" in res.reasons[0]


def test_verify_against_period_slack_allows_52_53_week_years():
    v = Verifier({})
    claim = Claim(text="$383.3 billion", value=383.3e9, unit="USD", period_end=date(2023, 10, 1))
    assert v.verify_against(claim, _ev(), parse_number("$383.3 billion")).ok


def test_verify_against_missing_expected_value():
    v = Verifier({})
    res = v.verify_against(Claim(text="1", value=1.0), Evidence())
    assert res.verdict is Verdict.UNSUPPORTED


def test_find_receipt_direct(aapl_lookup):
    v = Verifier(aapl_lookup)
    res = v.find_receipt(parse_number("$383.3 billion"), fiscal_years={2023})
    assert res.ok
    assert res.evidence.facts[0].concept.startswith("RevenueFromContract")
    assert res.evidence.facts[0].end == date(2023, 9, 30)


def test_find_receipt_growth_and_ratio(aapl_lookup):
    v = Verifier(aapl_lookup)
    growth = v.find_receipt(parse_number("-2.8%"), fiscal_years={2023})
    assert growth.ok and "FY2022" in growth.evidence.formula
    margin = v.find_receipt(parse_number("25.3%"), fiscal_years={2023})  # 96,995 / 383,285
    assert margin.ok and "/" in margin.evidence.formula


def test_find_receipt_unsupported(aapl_lookup):
    res = Verifier(aapl_lookup).find_receipt(parse_number("$123.4 billion"), fiscal_years={2023})
    assert res.verdict is Verdict.UNSUPPORTED


def test_find_receipt_respects_fiscal_year_scope(aapl_lookup):
    v = Verifier(aapl_lookup)
    assert not v.find_receipt(parse_number("$383.3 billion"), fiscal_years={2021}).ok


def test_audit_text(aapl_lookup):
    text = "FY2023 revenue was $383.3 billion; net income $97.0 billion; 161,000 employees."
    audit = Verifier(aapl_lookup).audit_text(text, fiscal_years={2023})
    assert [r.ok for r in audit] == [True, True, False]
