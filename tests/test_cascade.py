"""Verifier-gated cascade routing: small model first, escalate to large on failure."""

import pytest

from finreceipts.agent.graph import AgentDeps, build_graph, final_answer_text, initial_state
from finreceipts.config import Settings
from finreceipts.evals.runner import ItemResult, RunConfig, cost_of, run_eval, summarize
from finreceipts.llm.client import FakeLLM, is_coding_plan_url, make_client

Q = "What was Apple's revenue for the fiscal year ended September 30, 2023?"
WRONG = "Revenue was $390.0 billion.\nANSWER: $390.0 billion"
RIGHT = "Revenue was $383.3 billion.\nANSWER: $383.3 billion"


def by_model(small_reply, large_reply):
    return FakeLLM(lambda _m, model, _t: small_reply if model == "small" else large_reply)


def _run(llm, provider, **kw):
    g = build_graph(AgentDeps(llm=llm, lookup_provider=provider))
    state = initial_state(
        Q, ticker="AAPL", fiscal_years=[2022, 2023], model="small", mode="cascade", **kw
    )
    return g.invoke(state)


def test_cascade_stays_small_when_verifier_passes(provider):
    llm = by_model(RIGHT, WRONG)
    out = _run(llm, provider, escalate_model="large", max_revisions=0)
    assert not out["escalated"] and out["model"] == "small"
    assert [c["model"] for c in llm.calls] == ["small"]
    assert set(out["usage_by_model"]) == {"small"}


def test_cascade_escalates_with_fresh_context(provider):
    llm = by_model(WRONG, RIGHT)
    out = _run(llm, provider, escalate_model="large", max_revisions=0)
    assert out["escalated"] and out["model"] == "large"
    assert [c["model"] for c in llm.calls] == ["small", "large"]
    # the large model sees only the question, not the small model's failed attempt
    assert llm.calls[1]["messages"] == [{"role": "user", "content": Q}]
    assert final_answer_text(out["answer"]) == "$383.3 billion"
    assert [a["model"] for a in out["attempts"]] == ["small", "large"]
    assert out["usage_by_model"]["small"].calls == 1
    assert out["usage_by_model"]["large"].calls == 1
    assert out["usage"].calls == 2


def test_cascade_revises_on_small_before_escalating_and_large_has_own_budget(provider):
    llm = by_model(WRONG, WRONG)
    out = _run(llm, provider, escalate_model="large", max_revisions=1)
    # small: answer + 1 revision; large: answer + 1 revision; then stop
    assert [c["model"] for c in llm.calls] == ["small", "small", "large", "large"]
    assert out["revisions"] == 2 and out["escalated"]


def test_cascade_requires_escalate_model():
    with pytest.raises(ValueError):
        initial_state(Q, ticker="AAPL", fiscal_years=[2023], model="s", mode="cascade")


def test_run_eval_cascade_summary(tmp_path, provider, edgar):
    from finreceipts.evals.generate import generate

    items = generate(
        edgar, tickers=["AAPL"], fiscal_years=[2023], n_direct=2, n_growth=0, n_ratio=0, seed=1
    )
    llm = by_model("ANSWER: $1.0 billion", "ANSWER: $2.0 billion")
    cfg = RunConfig(model="small", mode="cascade", max_revisions=0, escalate_model="large")
    s = run_eval(
        items,
        llm,
        provider,
        cfg,
        out_dir=tmp_path,
        prices={"small": (1.0, 2.0), "large": (10.0, 20.0)},
    )
    assert s["escalation_rate"] == 1.0
    assert set(s["tokens_by_model"]) == {"small", "large"}
    assert s["cost"] is not None and s["cost"] > 0
    assert (tmp_path / "items.jsonl").read_text().count('"escalated": true') == len(items)


def test_cost_of_requires_all_prices():
    tbm = {"a": {"input_tokens": 1_000_000, "output_tokens": 500_000, "calls": 1}}
    assert cost_of(tbm, {"a": (1.0, 4.0)}) == pytest.approx(3.0)
    assert cost_of(tbm, {"b": (1.0, 1.0)}) is None


def test_summarize_without_prices_has_no_cost():
    r = ItemResult(
        "x",
        "direct",
        True,
        "1",
        1,
        0,
        0,
        10,
        5,
        1,
        0.1,
        "ANSWER: 1",
        usage_by_model={"m": {"input_tokens": 10, "output_tokens": 5, "calls": 1}},
    )
    s = summarize([r])
    assert s["cost"] is None and s["tokens_by_model"]["m"]["calls"] == 1


def test_coding_plan_endpoint_is_refused_by_default(monkeypatch):
    monkeypatch.setenv("TEST_KEY_ENV", "dummy-not-a-key")
    for url in (
        "https://ark.cn-beijing.volces.com/api/coding",
        "https://ark.cn-beijing.volces.com/api/coding/v3",
    ):
        assert is_coding_plan_url(url)
        for provider_name in ("anthropic", "openai"):
            with pytest.raises(RuntimeError, match="Coding Plan"):
                make_client(
                    Settings(
                        llm_provider=provider_name, llm_base_url=url, llm_api_key_env="TEST_KEY_ENV"
                    )
                )
    assert not is_coding_plan_url("https://ark.cn-beijing.volces.com/api/v3")
    assert not is_coding_plan_url("")
    ok = Settings(
        llm_provider="openai",
        llm_api_key_env="TEST_KEY_ENV",
        allow_coding_plan=True,
        llm_base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
    )
    assert make_client(ok) is not None


def test_settings_allow_coding_plan_from_env(monkeypatch):
    monkeypatch.setenv("FINRECEIPTS_ALLOW_CODING_PLAN", "1")
    assert Settings.from_env().allow_coding_plan
    monkeypatch.delenv("FINRECEIPTS_ALLOW_CODING_PLAN")
    assert not Settings.from_env().allow_coding_plan


def test_cascade_escalates_numberless_answer(provider):
    llm = by_model("I am not sure.", RIGHT)
    out = _run(llm, provider, escalate_model="large", max_revisions=0)
    assert [c["model"] for c in llm.calls] == ["small", "large"]
    assert out["escalated"] and out["revisions"] == 0
