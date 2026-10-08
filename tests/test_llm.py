import pytest

from finreceipts.config import Settings
from finreceipts.llm.client import (
    AnthropicCompatClient,
    FakeLLM,
    LLMResponse,
    OpenAICompatClient,
    PacedClient,
    ToolCall,
    make_client,
    redact,
)


def test_redact():
    assert redact("key=abc123 failed", ["abc123"]) == "key=*** failed"
    assert redact("nothing", [""]) == "nothing"


def test_fake_llm_records_calls_and_usage():
    llm = FakeLLM(lambda msgs, model, tools: "hello world!")
    r = llm.complete([{"role": "user", "content": "x" * 40}], model="m", system="s" * 4)
    assert r.text == "hello world!" and r.model == "m"
    assert r.usage.input_tokens == 11 and r.usage.output_tokens == 3 and r.usage.calls == 1
    assert llm.calls[0]["system"] == "ssss"


def test_fake_llm_can_return_tool_calls():
    tc = ToolCall("1", "t", {"a": 1})
    llm = FakeLLM(lambda *_: LLMResponse(text="", tool_calls=[tc]))
    assert llm.complete([], model="m").tool_calls == [tc]


MSGS = [
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": "", "tool_calls": [ToolCall("t1", "get", {"x": 1})]},
    {"role": "tool", "tool_call_id": "t1", "name": "get", "content": "{}"},
    {"role": "tool", "tool_call_id": "t2", "name": "get", "content": "{}"},
]


def test_anthropic_wire_format_groups_tool_results():
    wire = AnthropicCompatClient._to_wire(MSGS)
    assert wire[1]["content"][0] == {
        "type": "tool_use",
        "id": "t1",
        "name": "get",
        "input": {"x": 1},
    }
    assert wire[2]["role"] == "user" and len(wire[2]["content"]) == 2
    assert wire[2]["content"][0]["type"] == "tool_result"


def test_openai_wire_format():
    wire = OpenAICompatClient._to_wire(MSGS, "sys")
    assert wire[0] == {"role": "system", "content": "sys"}
    assert wire[2]["tool_calls"][0]["function"]["arguments"] == '{"x": 1}'
    assert wire[3] == {"role": "tool", "tool_call_id": "t1", "content": "{}"}


def test_make_client_fake_and_errors(monkeypatch):
    assert isinstance(make_client(Settings()), FakeLLM)
    with pytest.raises(RuntimeError):
        make_client(Settings(llm_provider="openai"))  # no key env configured
    monkeypatch.setenv("TEST_KEY_ENV", "")
    with pytest.raises(RuntimeError):
        make_client(Settings(llm_provider="openai", llm_api_key_env="TEST_KEY_ENV"))
    monkeypatch.setenv("TEST_KEY_ENV", "dummy-not-a-real-key")
    with pytest.raises(ValueError):
        make_client(Settings(llm_provider="nope", llm_api_key_env="TEST_KEY_ENV"))
    c = make_client(Settings(llm_provider="openai", llm_api_key_env="TEST_KEY_ENV"))
    assert isinstance(c, PacedClient) and isinstance(c.inner, OpenAICompatClient)
    c = make_client(Settings(llm_provider="anthropic", llm_api_key_env="TEST_KEY_ENV"))
    assert isinstance(c, PacedClient) and isinstance(c.inner, AnthropicCompatClient)
    assert c.min_interval_s == 1.0


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    monkeypatch.setenv("FINRECEIPTS_SMALL_MODEL", "small")
    s = Settings.from_env()
    assert s.offline and s.small_model == "small"
    assert "contact@example.com" in s.sec_user_agent or s.sec_user_agent


class _Err(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status_code = status


def test_classify_error():
    from finreceipts.llm.client import classify_error

    assert classify_error(_Err("nope", 401)) == "fatal"
    assert classify_error(_Err("nope", 403)) == "fatal"
    assert classify_error(_Err("slow down", 429)) == "fatal"
    assert classify_error(_Err("账号已被封禁", 400)) == "fatal"
    assert classify_error(_Err("bad gateway", 502)) == "transient"
    assert classify_error(_Err("bad request", 400)) == "item"


def test_call_with_policy(monkeypatch):
    from finreceipts.llm import client as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    with pytest.raises(mod.FatalAPIError) as ei:
        mod.call_with_policy(lambda: (_ for _ in ()).throw(_Err("key SECRET bad", 401)), "SECRET")
    assert "SECRET" not in str(ei.value) and "***" in str(ei.value)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise _Err("upstream", 503)
        return "ok"

    assert mod.call_with_policy(flaky, "S") == "ok" and len(calls) == 2
    with pytest.raises(RuntimeError):
        mod.call_with_policy(lambda: (_ for _ in ()).throw(_Err("bad", 400)), "S")


def test_paced_client_spaces_calls_and_counts(monkeypatch):
    from finreceipts.llm import client as mod

    clock = {"t": 100.0}
    sleeps = []
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    def fake_sleep(d):
        sleeps.append(d)
        clock["t"] += d

    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    inner = FakeLLM(lambda *_: "hi")
    pc = PacedClient(inner, min_interval_s=1.0)
    pc.complete([{"role": "user", "content": "a"}], model="m")
    pc.complete([{"role": "user", "content": "b"}], model="m")
    assert sleeps == [pytest.approx(1.0)]
    assert pc.attempts == {"m": 2} and pc.failures == {}

    boom = PacedClient(FakeLLM(lambda *_: (_ for _ in ()).throw(RuntimeError("x"))), 0.0)
    with pytest.raises(RuntimeError):
        boom.complete([{"role": "user", "content": "a"}], model="m")
    assert boom.failures == {"m": 1}


def test_run_eval_stops_on_fatal_error(tmp_path, provider, edgar):
    from finreceipts.evals.generate import generate
    from finreceipts.evals.runner import RunConfig, run_eval
    from finreceipts.llm.client import FatalAPIError

    items = generate(
        edgar, tickers=["AAPL"], fiscal_years=[2023], n_direct=3, n_growth=0, n_ratio=0, seed=1
    )
    n = {"calls": 0}

    def responder(*_):
        n["calls"] += 1
        if n["calls"] == 2:
            raise FatalAPIError("HTTP 429")
        return "ANSWER: $1 billion"

    llm = PacedClient(FakeLLM(responder), 0.0)
    with pytest.raises(FatalAPIError):
        run_eval(items, llm, provider, RunConfig(model="m"), out_dir=tmp_path)
    import json

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["aborted"] == "HTTP 429" and summary["n_items"] == 1
    assert summary["api_attempts"] == {"m": 2} and n["calls"] == 2


def test_split_thinking_alias():
    from finreceipts.llm.client import split_thinking_alias

    assert split_thinking_alias("MiniMax-M3+think") == ("MiniMax-M3", True)
    assert split_thinking_alias("MiniMax-M3") == ("MiniMax-M3", False)
