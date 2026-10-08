"""Fake-LLM regression coverage for attempt-specific evidence snapshots."""

import json
from copy import deepcopy

from finreceipts.agent.graph import AgentDeps, build_graph, initial_state
from finreceipts.llm.client import FakeLLM
from finreceipts.verify.serialization import serialize_audit
from tests.conftest import make_fact


def _run(*answers, mode="verify", **options):
    replies = iter(answers)
    llm = FakeLLM(lambda *_: next(replies))
    lookup = {"revenue": {2023: make_fact(120)}}
    graph = build_graph(AgentDeps(llm=llm, lookup_provider=lambda *_: lookup))
    state = initial_state(
        "What was revenue?",
        ticker="TEST",
        fiscal_years=[2023],
        model="small",
        mode=mode,
        **options,
    )
    return graph, state


def test_revision_preserves_exact_answer_and_evidence_for_each_attempt():
    wrong = " 营收 $999。\nANSWER: $999  "
    right = "Revenue was $120.\nANSWER: $120"
    graph, state = _run(wrong, right)

    out = graph.invoke(state)
    attempts = json.loads(json.dumps(out["attempts"], ensure_ascii=False, allow_nan=False))

    assert [attempt["answer"] for attempt in attempts] == [wrong, right]
    assert [attempt["numbers"] for attempt in attempts] == [2, 2]
    assert [attempt["unsupported"] for attempt in attempts] == [2, 0]
    assert [attempt["supported"] for attempt in attempts] == [0, 2]
    assert [attempt["contradicted"] for attempt in attempts] == [0, 0]
    assert all(result["evidence"] is None for result in attempts[0]["audit"])
    assert all(result["verdict"] == "unsupported" for result in attempts[0]["audit"])
    assert all(result["verdict"] == "supported" for result in attempts[1]["audit"])
    assert attempts[-1]["audit"] == serialize_audit(out["audit"])
    for attempt in attempts:
        for result in attempt["audit"]:
            start, end = result["claim"]["span"]
            assert attempt["answer"][start:end] == result["claim"]["text"]


def test_cascade_preserves_small_and_large_evidence():
    graph, state = _run(
        "ANSWER: $999", "ANSWER: $120", mode="cascade", escalate_model="large", max_revisions=0
    )

    out = graph.invoke(state)

    assert [attempt["model"] for attempt in out["attempts"]] == ["small", "large"]
    assert out["attempts"][0]["audit"][0]["verdict"] == "unsupported"
    assert out["attempts"][1]["audit"][0]["verdict"] == "supported"


def test_numberless_attempt_is_recorded_without_fake_evidence():
    graph, state = _run("No figure available.", mode="baseline")

    out = graph.invoke(state)

    assert out["attempts"][0]["answer"] == "No figure available."
    assert out["attempts"][0]["audit"] == []
    assert out["attempts"][0]["numbers"] == 0


def test_historical_attempts_are_not_backfilled_with_new_evidence():
    historical = {
        "answer": "ANSWER: $77",
        "model": "old",
        "numbers": 1,
        "supported": 1,
        "unsupported": 0,
        "contradicted": 0,
    }
    original = deepcopy(historical)
    graph, state = _run("ANSWER: $120", mode="baseline")
    state["attempts"] = [historical]

    out = graph.invoke(state)

    assert out["attempts"][0] == original
    assert "audit" not in out["attempts"][0]
    assert out["attempts"][1]["audit"][0]["claim"]["value"] == 120
