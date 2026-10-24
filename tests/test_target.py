"""New bound gate against retained facts, including legacy numeric collisions."""

import pytest

from finreceipts.agent.graph import AgentDeps, initial_state, step_nodes, verification_passed
from finreceipts.models import Verdict
from finreceipts.verify.target import VerificationTarget, target_evidence, verify_target_answer
from tests.conftest import make_fact

TARGET = {"kind": "direct", "metric": "revenue", "fiscal_year": 2023}


@pytest.mark.parametrize(
    "answer,code",
    [
        (None, "empty_answer"),
        ("", "empty_answer"),
        ("N/A", "missing_final_answer"),
        ("ANSWER: N/A", "no_final_number"),
        ("ANSWER: $1 and $2", "multiple_final_numbers"),
        ("ANSWER: 100", "missing_unit"),
        ("ANSWER: $100%", "unit_conflict"),
        ("ANSWER: 100元", "unit_mismatch"),
        ("ANSWER: $100 million", "scale_error"),
    ],
)
def test_empty_units_and_scale(answer, code):
    result = verify_target_answer(answer, TARGET, {"revenue": {2023: make_fact(100)}})
    assert not result.ok and code in result.reason_codes


def test_zero_is_a_valid_value_and_missing_is_not_zero():
    lookup = {"revenue": {2023: make_fact(0)}}
    assert verify_target_answer("ANSWER: $0", TARGET, lookup).ok
    assert verify_target_answer("ANSWER: $0", TARGET, {}).reason_codes == ["missing_evidence"]


def test_real_units_and_wrong_metric_collision(aapl_lookup):
    assert verify_target_answer("ANSWER: $383.285 billion", TARGET, aapl_lookup).ok
    wrong = aapl_lookup["net income"][2023].value
    result = verify_target_answer(f"ANSWER: ${wrong}", TARGET, aapl_lookup)
    assert result.verdict == Verdict.CONTRADICTED
    state = initial_state(
        "revenue?", ticker="AAPL", fiscal_years=[2023], model="fake", target=TARGET
    )
    state["answer"] = f"ANSWER: ${wrong}"
    state.update(
        step_nodes(AgentDeps(llm=None, lookup_provider=lambda *_: aapl_lookup))["verify"](state)
    )
    assert all(r.ok for r in state["audit"])  # The amount exists, but for another metric.
    assert not verification_passed(state)
    assert state["attempts"][0]["target_audit"] == state["target_audit"]


def test_wrong_year_and_undefined_growth():
    lookup = {"revenue": {2022: make_fact(0), 2023: make_fact(200)}}
    assert not verify_target_answer("ANSWER: $0", TARGET, lookup).ok
    growth = VerificationTarget.model_validate({**TARGET, "kind": "yoy_growth"})
    assert target_evidence(growth, lookup) == (None, "zero_denominator")
    assert verify_target_answer("ANSWER: 0%", growth, lookup).reason_codes == ["zero_denominator"]


def test_target_revision_limit_is_enforced_at_shared_entry():
    with pytest.raises(ValueError, match="at most two"):
        initial_state(
            "revenue?",
            ticker="AAPL",
            fiscal_years=[2023],
            model="fake",
            target=TARGET,
            max_revisions=3,
        )


@pytest.mark.parametrize(
    "updates",
    [{"metric": "unknown"}, {"denominator": "revenue"}, {"kind": "ratio"}, {"expected_value": 100}],
)
def test_target_rejects_invalid_or_answer_injection(updates):
    with pytest.raises(ValueError):
        VerificationTarget.model_validate(TARGET | updates)
