import json

import pytest

from finreceipts.evals.dataset import read_jsonl, write_jsonl
from finreceipts.evals.generate import generate, question_text
from finreceipts.evals.runner import (
    ItemResult,
    RunConfig,
    run_eval,
    score_answer,
    summarize,
)
from finreceipts.llm.client import FakeLLM
from finreceipts.xbrl.metrics import CONCEPTS, MetricKind, MetricSpec


@pytest.fixture
def items(edgar):
    return generate(
        edgar,
        tickers=["AAPL", "NVDA"],
        fiscal_years=[2023, 2024],
        n_direct=6,
        n_growth=2,
        n_ratio=2,
        seed=1,
    )


def test_generate_is_deterministic_and_grounded(edgar, items):
    again = generate(
        edgar,
        tickers=["AAPL", "NVDA"],
        fiscal_years=[2023, 2024],
        n_direct=6,
        n_growth=2,
        n_ratio=2,
        seed=1,
    )
    assert [i.id for i in items] == [i.id for i in again]
    assert len(items) == 10
    for it in items:
        assert it.evidence and it.as_of >= it.evidence[0].filed
        assert "XBRL:" in it.question
        if it.kind == "direct":
            assert it.expected_value == it.evidence[0].value


def test_jsonl_roundtrip(tmp_path, items):
    p = tmp_path / "d.jsonl"
    write_jsonl(items, p)
    assert read_jsonl(p) == items


def test_question_templates():
    q = question_text(MetricSpec(MetricKind.YOY_GROWTH, CONCEPTS["revenue"]), "X", "May 1, 2024")
    assert "negative for a decline" in q
    q = question_text(MetricSpec(MetricKind.DIRECT, CONCEPTS["diluted EPS"]), "X", "May 1, 2024")
    assert "per share" in q


def test_score_answer(items):
    direct = next(i for i in items if i.kind == "direct" and i.expected_unit == "USD")
    good = f"ANSWER: ${direct.expected_value / 1e6:,.0f} million"
    assert score_answer(direct, good)[0]
    assert not score_answer(direct, "ANSWER: $1 million")[0]
    assert not score_answer(direct, "I don't know")[0]


def test_run_eval_with_oracle_and_liar(tmp_path, items, provider):
    by_q = {i.question: i for i in items}

    def oracle(messages, model, tools):
        it = by_q[messages[0]["content"]]
        if it.expected_unit == "percent":
            return f"ANSWER: {it.expected_value:.2f}%"
        if it.expected_unit == "USD/shares":
            return f"ANSWER: ${it.expected_value:.2f}"
        return f"ANSWER: ${it.expected_value / 1e6:,.0f} million"

    s = run_eval(items, FakeLLM(oracle), provider, RunConfig(model="fake"), out_dir=tmp_path)
    assert s["accuracy"] == 1.0
    assert s["unsupported_number_rate"] == 0.0
    assert (tmp_path / "summary.json").exists()
    lines = (tmp_path / "items.jsonl").read_text().splitlines()
    assert len(lines) == len(items) and json.loads(lines[0])["correct"]

    liar = run_eval(
        items, FakeLLM(lambda *_: "ANSWER: $1.23 billion"), provider, RunConfig(model="fake")
    )
    assert liar["accuracy"] == 0.0 and liar["unsupported_number_rate"] == 1.0


def test_run_eval_records_errors(items, provider):
    def boom(*_):
        raise RuntimeError("upstream 429")

    s = run_eval(items[:2], FakeLLM(boom), provider, RunConfig(model="fake"))
    assert s["n_errors"] == 2 and s["accuracy"] == 0.0


def test_summarize_percentiles():
    rs = [
        ItemResult(f"i{k}", "direct", k % 2 == 0, "", 2, k % 2, 0, 10, 5, 1, float(k), "")
        for k in range(1, 11)
    ]
    s = summarize(rs)
    assert s["accuracy"] == 0.5
    assert s["unsupported_number_rate"] == pytest.approx(5 / 20)
    assert s["latency_p50_s"] in (5.0, 6.0) and s["latency_p95_s"] == 10.0
    assert summarize([])["accuracy"] == 0.0
