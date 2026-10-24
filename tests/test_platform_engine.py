"""V2 numeric correctness, native history, tool scope and unknown-dispatch boundaries."""

import copy

import httpx
import pytest

from finreceipts.platform import engine
from finreceipts.platform.models import (
    Adapter,
    DispatchUnknown,
    ProviderRejected,
    Response,
    cost_micros,
    request_body,
    reservation_micros,
    tool_result,
)
from finreceipts.runtime.codec import decode_lookup


def request(task_kind="metric", **target):
    return {
        "kind": task_kind,
        "ticker": "AAPL",
        "question": "Explain annual revenue.",
        "target": {"kind": "direct", "metric": "revenue", "fiscal_year": 2023, **target},
    }


def profile(provider="minimax"):
    return {
        "provider": provider,
        "model": "MiniMax-M3.1-Flash" if provider == "minimax" else "deepseek-flash",
        "effort": "medium",
        "max_output_tokens": 4096,
        "max_revisions": 2,
    }


@pytest.mark.parametrize(
    "target", [{}, {"kind": "yoy_growth"}, {"kind": "ratio", "denominator": "net income"}]
)
def test_rule_path_has_evidence_and_no_model(aapl_lookup, target):
    delta = engine.prepare(request(**target), aapl_lookup)
    assert delta["next_step"] == "DONE"
    assert delta["machine_verdict"] == "SUPPORTED"
    assert delta["checkpoint"]["messages"] == []
    assert all(int(y) in {2022, 2023} for s in delta["evidence"].values() for y in s)


def test_missing_evidence_abstains_without_model(aapl_lookup):
    delta = engine.prepare(request("research", fiscal_year=1999), aapl_lookup)
    assert delta["next_step"] == "DONE"
    assert delta["machine_verdict"] == "UNSUPPORTED"
    assert delta["result"]["answer"] == "ANSWER: N/A"


def test_wrong_value_drives_bounded_revision(aapl_lookup):
    delta = engine.prepare(request("research"), aapl_lookup)
    task = {**delta, "request": request("research"), "profile": profile()}
    bad = Response(
        "ANSWER: 1 USD",
        {"role": "assistant", "content": [{"type": "text", "text": "ANSWER: 1 USD"}]},
        [],
        {},
        "m",
        None,
        "end_turn",
    )
    model = engine.consume(task, bad)
    task["checkpoint"] = model["checkpoint"]
    for _ in range(2):
        checked = engine.verify(task)
        assert checked["next_step"] == "MODEL"
        task["checkpoint"] = checked["checkpoint"]
    checked = engine.verify(task)
    assert checked["next_step"] == "DONE"
    assert checked["machine_verdict"] != "SUPPORTED"


def test_out_of_scope_tool_is_refused(aapl_lookup):
    delta = engine.prepare(request("research"), aapl_lookup)
    task = {**delta, "request": request("research"), "profile": profile()}
    resp = Response(
        "",
        {"role": "assistant", "content": []},
        [
            {
                "id": "c1",
                "name": "get_financial_fact",
                "arguments": {"ticker": "MSFT", "metric": "revenue", "fiscal_year": 2023},
            }
        ],
        {},
        "m",
        None,
        "tool_use",
    )
    output = engine.consume(task, resp)
    assert output["next_step"] == "MODEL"
    assert "tool_scope_denied" in str(output["checkpoint"]["messages"][-1])


def test_native_minimax_thinking_roundtrip(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-not-a-real-key")
    blocks = [
        {"type": "thinking", "thinking": "opaque", "signature": "opaque-signature"},
        {"type": "tool_use", "id": "c1", "name": "get_financial_fact", "input": {"ticker": "AAPL"}},
    ]
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "content": blocks,
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "model": "MiniMax-M3.1-Flash",
                "stop_reason": "tool_use",
            },
        )
    )
    adapter = Adapter("minimax", transport=transport)
    response = adapter.complete(profile(), [{"role": "user", "content": "q"}], "sys", [])
    history = [response.assistant, tool_result("minimax", "c1", "{}")]
    body = request_body(profile(), history, "sys", [])
    assert body["messages"][0]["content"] == blocks
    assert body["output_config"] == {"effort": "medium"}
    assert "thinking" not in body and body["max_tokens"] == 4096
    adapter.close()


def test_deepseek_reasoning_and_tool_identity_are_preserved():
    msg = {
        "role": "assistant",
        "content": None,
        "reasoning_content": "opaque",
        "tool_calls": [
            {"id": "abc", "type": "function", "function": {"name": "fact", "arguments": "{}"}}
        ],
    }
    body = request_body(profile("deepseek"), [msg, tool_result("deepseek", "abc", "{}")], "sys", [])
    assert body["messages"][1] == msg
    assert body["messages"][2]["tool_call_id"] == "abc"


@pytest.mark.parametrize(
    "status,exception", [(429, ProviderRejected), (401, ProviderRejected), (500, DispatchUnknown)]
)
def test_no_hidden_http_retries(monkeypatch, status, exception):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(status, json={"error": "test"})

    adapter = Adapter("minimax", transport=httpx.MockTransport(respond))
    with pytest.raises(exception):
        adapter.complete(profile(), [], "sys", [])
    assert len(calls) == 1
    adapter.close()


def test_context_and_cost_limits():
    with pytest.raises(ValueError, match="context_budget"):
        request_body(profile(), [{"role": "user", "content": "x" * 33000}], "sys", [])
    p = profile()
    assert cost_micros(p, {"input_tokens": 32000, "output_tokens": 4096}) == reservation_micros(p)


def test_network_timeout_is_unknown_and_is_not_retried(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "local-test-key")
    attempts = []

    def timeout(req):
        attempts.append(req)
        raise httpx.ReadTimeout("response was lost", request=req)

    adapter = Adapter("minimax", transport=httpx.MockTransport(timeout))
    try:
        with pytest.raises(DispatchUnknown, match="provider_transport_unknown"):
            adapter.complete(profile(), [], "sys", [])
        assert len(attempts) == 1
    finally:
        adapter.close()


def test_checkpoint_revision_does_not_mutate_old_evidence(aapl_lookup):
    delta = engine.prepare(request("research"), aapl_lookup)
    saved = copy.deepcopy(delta)
    task = {**delta, "request": request("research"), "profile": profile()}
    answer = engine.expected_answer(
        engine.VerificationTarget.model_validate(request()["target"]),
        decode_lookup(delta["evidence"]),
    )
    response = Response(
        answer, {"role": "assistant", "content": answer}, [], {}, "fake", None, "end_turn"
    )
    output = engine.consume(task, response)
    task["checkpoint"] = output["checkpoint"]
    assert engine.verify(task)["machine_verdict"] == "SUPPORTED"
    assert delta == saved


def test_missing_offline_company_is_terminal_preparation_failure(monkeypatch):
    from finreceipts.platform import worker
    from finreceipts.tools.edgar import EdgarError

    class MissingCompany:
        def __init__(self, _coordinator):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def ticker_to_cik(self, _ticker):
            raise EdgarError("not found")

        def companyfacts(self, _cik):
            pytest.fail("unknown ticker must fail before fetching financial statements")

    monkeypatch.setattr(worker, "CoordinatedEdgar", MissingCompany)
    with pytest.raises(ValueError, match="evidence_unavailable"):
        worker.execute({"request": request(), "checkpoint": {}, "step": "PREPARE"}, None)
