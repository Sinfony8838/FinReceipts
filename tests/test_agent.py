import json
from datetime import date

from finreceipts.agent.graph import (
    AgentDeps,
    build_graph,
    feedback_message,
    final_answer_text,
    initial_state,
)
from finreceipts.agent.tools import run_get_fact
from finreceipts.llm.client import FakeLLM, LLMResponse, ToolCall

Q = "What was Apple's revenue for the fiscal year ended September 30, 2023?"
RIGHT_ANSWER = "Revenue was $383.3 billion.\nANSWER: $383.3 billion"


def scripted(*replies):
    it = iter(replies)
    return FakeLLM(lambda *_: next(it))


def test_final_answer_text():
    assert final_answer_text("blah\nANSWER: $1 billion") == "$1 billion"
    assert final_answer_text("ANSWER: 1\nANSWER: 2") == "2"
    assert final_answer_text("just text") == "just text"


def test_baseline_answers_once_and_audits(provider):
    llm = scripted("Revenue was $390.0 billion.\nANSWER: $390.0 billion")
    g = build_graph(AgentDeps(llm=llm, lookup_provider=provider))
    out = g.invoke(initial_state(Q, ticker="AAPL", fiscal_years=[2022, 2023], model="m"))
    assert len(llm.calls) == 1
    assert out["revisions"] == 0
    assert all(not r.ok for r in out["audit"])
    assert out["attempts"][0]["unsupported"] == 2


def test_verify_loop_revises_until_supported(provider):
    llm = scripted(
        "Revenue was $390.0 billion.\nANSWER: $390.0 billion",
        "Corrected: revenue was $383.3 billion.\nANSWER: $383.3 billion",
    )
    g = build_graph(AgentDeps(llm=llm, lookup_provider=provider))
    out = g.invoke(
        initial_state(Q, ticker="AAPL", fiscal_years=[2022, 2023], model="m", mode="verify")
    )
    assert out["revisions"] == 1
    assert all(r.ok for r in out["audit"])
    assert final_answer_text(out["answer"]) == "$383.3 billion"
    feedback = llm.calls[1]["messages"][-1]["content"]
    assert "'$390.0 billion': unsupported" in feedback
    assert out["usage"].calls == 2


def test_verify_loop_respects_budget(provider):
    llm = FakeLLM(lambda *_: "ANSWER: $1.0 billion")
    g = build_graph(AgentDeps(llm=llm, lookup_provider=provider))
    out = g.invoke(
        initial_state(
            Q, ticker="AAPL", fiscal_years=[2023], model="m", mode="verify", max_revisions=2
        )
    )
    assert out["revisions"] == 2 and len(llm.calls) == 3


def test_receipts_feedback_lists_reported_figures(provider):
    state = initial_state(Q, ticker="AAPL", fiscal_years=[2023], model="m", feedback="receipts")
    state["audit"] = []
    msg = feedback_message(state, AgentDeps(llm=FakeLLM(lambda *_: ""), lookup_provider=provider))
    assert "revenue FY2023 (period ending 2023-09-30): 383,285,000,000.00 USD" in msg


def test_tool_use_path(provider):
    replies = iter(
        [
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        "c1",
                        "get_financial_fact",
                        {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2023},
                    )
                ],
            ),
            "Per the 10-K, revenue was $383,285 million.\nANSWER: $383,285 million",
        ]
    )
    llm = FakeLLM(lambda *_: next(replies))
    g = build_graph(AgentDeps(llm=llm, lookup_provider=provider))
    out = g.invoke(initial_state(Q, ticker="AAPL", fiscal_years=[2023], model="m", use_tools=True))
    tool_msg = next(m for m in out["messages"] if m["role"] == "tool")
    assert json.loads(tool_msg["content"])["value"] == 383_285_000_000
    assert all(r.ok for r in out["audit"])


def test_get_fact_tool_errors_and_point_in_time(provider):
    assert "bad arguments" in run_get_fact(provider, {}, None)
    assert "unknown metric" in run_get_fact(
        provider, {"ticker": "AAPL", "metric": "vibes", "fiscal_year": 2023}, None
    )
    # FY2024 10-K was filed 2024-11-01: invisible before that date
    hidden = run_get_fact(
        provider, {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2024}, date(2024, 6, 1)
    )
    assert "no revenue" in hidden
    assert "lookup failed" in run_get_fact(
        provider, {"ticker": "ZZZZ", "metric": "revenue", "fiscal_year": 2024}, None
    )


def test_unknown_tool_is_reported(provider):
    replies = iter([LLMResponse(text="", tool_calls=[ToolCall("c1", "nope", {})]), "ANSWER: n/a"])
    g = build_graph(AgentDeps(llm=FakeLLM(lambda *_: next(replies)), lookup_provider=provider))
    out = g.invoke(initial_state(Q, ticker="AAPL", fiscal_years=[2023], model="m", use_tools=True))
    assert "unknown tool" in next(m for m in out["messages"] if m["role"] == "tool")["content"]


def test_verify_mode_revises_numberless_answer(provider):
    llm = scripted("I don't have that figure.", RIGHT_ANSWER)
    g = build_graph(AgentDeps(llm=llm, lookup_provider=provider))
    out = g.invoke(
        initial_state(Q, ticker="AAPL", fiscal_years=[2022, 2023], model="m", mode="verify")
    )
    assert out["revisions"] == 1 and len(llm.calls) == 2
    feedback = llm.calls[1]["messages"][-1]["content"]
    assert "no final 'ANSWER: <number with unit>' line" in feedback
    assert final_answer_text(out["answer"]) == "$383.3 billion"


def test_verify_mode_numberless_answer_respects_budget(provider):
    llm = FakeLLM(lambda *_: "I cannot say.")
    g = build_graph(AgentDeps(llm=llm, lookup_provider=provider))
    out = g.invoke(
        initial_state(
            Q, ticker="AAPL", fiscal_years=[2023], model="m", mode="verify", max_revisions=1
        )
    )
    assert out["revisions"] == 1 and len(llm.calls) == 2


def test_verification_passed_gate():
    from finreceipts.agent.graph import verification_passed

    assert not verification_passed({"answer": "no idea", "audit": []})
    assert verification_passed({"answer": "ANSWER: $1 billion", "audit": []})
