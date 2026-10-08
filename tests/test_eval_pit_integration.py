"""Real LangGraph/FakeLLM integration tests; synthetic facts, no model proof."""

import json

from finreceipts.evals.runner import RunConfig, run_eval
from finreceipts.llm.client import FakeLLM, LLMResponse, ToolCall
from tests.test_eval_controls import CONTEXT, item, legacy, raw_facts


def test_runner_persists_final_and_attempt_audits(tmp_path):
    replies = iter(["ANSWER: $999 billion", "ANSWER: $100 billion"])
    llm = FakeLLM(lambda *_: next(replies))
    summary = run_eval(
        [item()],
        llm,
        legacy,
        RunConfig(model="fake", mode="verify", feedback="receipts"),
        out_dir=tmp_path,
        pit_context=CONTEXT,
        facts_provider=raw_facts,
    )
    result = json.loads((tmp_path / "items.jsonl").read_text())
    assert summary["accuracy"] == 1
    assert summary["result_schema_version"] == 2
    assert summary["verification_context"]["fact_selection"] == "explicit_catalog_pit"
    assert result["audit"][0]["verdict"] == "supported"
    assert result["audit"] == result["attempts"][-1]["audit"]
    assert result["attempts"][0]["audit"][0]["verdict"] == "unsupported"
    fact = result["audit"][0]["evidence"]["facts"][0]
    assert fact["value"] == 100e9 and fact["accn"] == "0000000001-23-000001"
    assert fact["source_url"].startswith("https://www.sec.gov/")
    assert result["verification_context"]["policy_fingerprint"] == CONTEXT.policy.fingerprint
    assert not result["verification_context"]["ground_truth_pit_validated"]


def test_runner_all_hooks_reject_scope_and_mark_tools(tmp_path):
    def respond(messages, model, tools):
        if messages[-1]["role"] == "user":
            return LLMResponse(
                "",
                [
                    ToolCall(
                        "t1",
                        "get_financial_fact",
                        {"ticker": "OTHER", "metric": "revenue", "fiscal_year": 2030},
                    )
                ],
            )
        assert "untrusted_tool_output" in messages[-1]["content"]
        assert "tool call refused" in messages[-1]["content"]
        return "ANSWER: $100 billion"

    llm = FakeLLM(respond)
    cfg = RunConfig(
        model="fake",
        use_tools=True,
        guard_tools=True,
        mark_untrusted=True,
        sealed_tool_cache=True,
    )
    summary = run_eval(
        [item()],
        llm,
        legacy,
        cfg,
        pit_context=CONTEXT,
        facts_provider=raw_facts,
        out_dir=tmp_path,
    )
    assert summary["accuracy"] == 1
    assert summary["verification_context"]["tool_cache"]["hits"] == 0
    assert "untrusted" in llm.calls[0]["system"].lower()


def test_runner_cache_replays_only_inside_one_run():
    def respond(messages, model, tools):
        if messages[-1]["role"] == "user":
            return LLMResponse(
                "",
                [
                    ToolCall(
                        "t1",
                        "get_financial_fact",
                        {"ticker": "TST", "metric": "revenue", "fiscal_year": 2023},
                    )
                ],
            )
        return "ANSWER: $100 billion"

    cfg = RunConfig(model="fake", use_tools=True, guard_tools=True, sealed_tool_cache=True)
    first = run_eval(
        [item(), item(id="synthetic-2")],
        FakeLLM(respond),
        legacy,
        cfg,
        pit_context=CONTEXT,
        facts_provider=raw_facts,
    )
    second = run_eval(
        [item()],
        FakeLLM(respond),
        legacy,
        cfg,
        pit_context=CONTEXT,
        facts_provider=raw_facts,
    )
    assert first["verification_context"]["tool_cache"]["hits"] == 1
    assert second["verification_context"]["tool_cache"]["hits"] == 0


def test_runner_errors_have_null_audit_but_numberless_completions_have_empty_audit(tmp_path):
    def fail(*_):
        raise RuntimeError("synthetic model failure")

    for name, llm, expected in [
        ("error", FakeLLM(fail), None),
        ("empty", FakeLLM(lambda *_: "No figure."), []),
    ]:
        path = tmp_path / name
        run_eval([item()], llm, legacy, RunConfig(model="fake"), out_dir=path)
        result = json.loads((path / "items.jsonl").read_text())
        assert result["audit"] == expected
        assert bool(result["error"]) == (name == "error")
