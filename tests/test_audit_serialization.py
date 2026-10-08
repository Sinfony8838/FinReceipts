"""Offline serialization checks using only synthetic facts and the real verifier."""

import json
import math
from datetime import date

import pytest

from finreceipts.models import Claim, Evidence, Verdict, VerificationResult
from finreceipts.verify.serialization import serialize_audit
from finreceipts.verify.verifier import Verifier
from tests.conftest import make_fact


def test_serialized_audit_keeps_full_receipt_and_exact_claim_span():
    fact = make_fact(120, concept="Revenues")
    fact.label = "Revenue"
    fact.fy = 2023
    fact.fp = "FY"
    fact.frame = "CY2023"
    answer = "营收为 $120。\nANSWER: $120"
    audit = Verifier({"revenue": {2023: fact}}).audit_text(answer, fiscal_years={2023})

    snapshot = json.loads(json.dumps(serialize_audit(audit), allow_nan=False))

    assert len(snapshot) == 2
    for result in snapshot:
        claim = result["claim"]
        start, end = claim["span"]
        assert answer[start:end] == claim["text"] == "$120"
        assert claim["value"] == 120
        assert claim["unit"] == "USD"
        assert claim["metric"] is None
        assert claim["entity"] is None
        assert claim["period_end"] is None
        assert result["verdict"] == "supported"
        assert result["reasons"] == []
        assert result["relative_error"] == 0
        evidence = result["evidence"]
        assert evidence["expected_value"] == 120
        assert evidence["expected_unit"] == "USD"
        assert evidence["formula"] == "Revenues[FY2023]"
        assert evidence["note"] is None
        assert evidence["facts"] == [fact.model_dump(mode="json")]
        saved_fact = evidence["facts"][0]
        assert saved_fact["source_url"] == fact.source_url
        assert saved_fact["start"] == "2022-10-01"
        assert saved_fact["end"] == "2023-09-30"
        assert saved_fact["filed"] == "2023-11-03"
        assert saved_fact["concept"] == "Revenues"
        assert saved_fact["value"] == 120
        assert saved_fact["period_type"] == "duration"
        assert saved_fact["duration_days"] == 365
    assert snapshot[0]["claim"]["span"] != snapshot[1]["claim"]["span"]


def test_serialized_growth_keeps_both_source_facts_and_formula():
    current = make_fact(120)
    previous = make_fact(
        100, start="2021-10-01", end="2022-09-30", filed="2022-11-03", accn="prior"
    )
    previous.filing_url = "https://example.test/reported-prior-year"
    audit = Verifier({"revenue": {2023: current, 2022: previous}}).audit_text(
        "ANSWER: 20%", fiscal_years={2023}
    )

    result = serialize_audit(audit)[0]

    assert result["verdict"] == "supported"
    evidence = result["evidence"]
    assert evidence["expected_value"] == 20
    assert evidence["expected_unit"] == "percent"
    assert evidence["formula"] == "(Revenues[FY2023] - Revenues[FY2022]) / |Revenues[FY2022]| * 100"
    assert [fact["value"] for fact in evidence["facts"]] == [120, 100]
    assert [fact["end"] for fact in evidence["facts"]] == ["2023-09-30", "2022-09-30"]
    assert evidence["facts"][1]["source_url"] == previous.filing_url


def test_serialized_unsupported_number_has_no_invented_receipt():
    audit = Verifier({"revenue": {2023: make_fact(120)}}).audit_text("ANSWER: $999")

    result = serialize_audit(audit)[0]

    assert result["verdict"] == "unsupported"
    assert result["claim"]["text"] == "$999"
    assert result["claim"]["value"] == 999
    assert result["evidence"] is None
    assert result["relative_error"] is None
    assert result["reasons"] == ["no matching fact found"]


def test_serialized_contradiction_retains_reasons_and_bound_claim_metadata():
    claim = Claim(
        text="$130",
        value=130,
        unit="USD",
        metric="revenue",
        entity="Test Co",
        period_end=date(2023, 9, 30),
        span=(8, 12),
    )
    evidence = Evidence(
        facts=[make_fact(120)],
        expected_value=120,
        expected_unit="USD",
        formula="Revenues[FY2023]",
        note="Synthetic filing used for an offline test.",
    )
    audit = [Verifier({}).verify_against(claim, evidence)]

    result = serialize_audit(audit)[0]

    assert result["verdict"] == "contradicted"
    assert result["reasons"] == ["value differs: claimed 130 vs expected 120"]
    assert result["claim"]["period_end"] == "2023-09-30"
    assert result["claim"]["metric"] == "revenue"
    assert result["claim"]["entity"] == "Test Co"
    assert result["evidence"]["note"] == evidence.note


def test_serialized_audit_is_a_detached_snapshot():
    fact = make_fact(120)
    audit = Verifier({"revenue": {2023: fact}}).audit_text("ANSWER: $120")
    snapshot = serialize_audit(audit)
    audit[0].claim.text = "changed later"
    audit[0].reasons.append("changed later")
    audit[0].evidence.facts[0].value = 999

    assert snapshot[0]["claim"]["text"] == "$120"
    assert snapshot[0]["reasons"] == []
    assert snapshot[0]["evidence"]["facts"][0]["value"] == 120


@pytest.mark.parametrize(
    ("value", "expected"), [(math.inf, "Infinity"), (-math.inf, "-Infinity"), (math.nan, "NaN")]
)
def test_nonfinite_values_are_explicit_and_strict_json_safe(value, expected):
    result = VerificationResult(
        claim=Claim(text="number", value=value),
        verdict=Verdict.CONTRADICTED,
        evidence=Evidence(facts=[make_fact(value)], expected_value=value),
        relative_error=value,
        reasons=["synthetic non-finite value"],
    )

    saved = json.loads(json.dumps(serialize_audit([result]), allow_nan=False))[0]

    assert saved["claim"]["value"] == expected
    assert saved["relative_error"] == expected
    assert saved["evidence"]["expected_value"] == expected
    assert saved["evidence"]["facts"][0]["value"] == expected


def test_empty_audit_remains_empty():
    assert serialize_audit([]) == []
