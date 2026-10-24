import copy
import json
from pathlib import Path

import httpx
import pytest
from scripts.check_agent_backend_evidence import check

from finreceipts.evals.agent_backend import (
    FixtureResponder,
    HTTPBudget,
    Trace,
    evaluate_case,
    freeze,
    load_suite,
    prepare,
    run,
)
from finreceipts.llm.client import FatalAPIError

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "evals/experiments/20261016-workflow"


@pytest.fixture
def suite(tmp_path):
    path = tmp_path / "suite"
    prepare(path, HISTORY)
    return path


def test_partition_is_grouped_and_hash_bound(suite):
    cases, _, manifest = load_suite(suite)
    assert len(cases) == 40
    assert all(len(manifest["splits"][s]) == 20 for s in ("dev", "frozen"))
    groups = [{c["group"] for c in cases if c["split"] == s} for s in ("dev", "frozen")]
    assert groups[0].isdisjoint(groups[1])
    assert set.union(*groups).isdisjoint(manifest["excluded_historical_ids"])
    (suite / "cases.json").write_text("[]")
    with pytest.raises(ValueError, match="hash"):
        load_suite(suite)


def test_paired_development_records_calls_failures_and_never_uses_frozen(suite, tmp_path):
    out = tmp_path / "dev"
    summary = run(suite, out)
    assert summary["complete"] and summary["evidence_type"] == "scripted_engineering_acceptance"
    assert summary["new_live_evaluation"] == "UNRUN" and summary["http_attempts"] == 0
    assert summary["strategies"]["bounded_revision"]["success"] == 20
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    direct = {r["id"]: r["answer"] for r in results if r["strategy"] == "direct"}
    assert all(r["answer"] == direct[r["id"]] for r in results if r["strategy"] == "audit_only")
    assert all(
        r["incremental_model_calls"] == 0 for r in results if r["strategy"] != "bounded_revision"
    )
    trace = [
        json.loads(line) for line in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert summary["actual_model_dispatches"] == sum(
        r["kind"] == "model" and r["status"] == "started" for r in trace
    )
    assert any('"code": "tool_timeout"' in (r.get("raw_result") or "") for r in trace)
    cases, _, _ = load_suite(suite)
    assert set(direct) == {c["id"] for c in cases if c["split"] == "dev"}
    assert check(suite, out)["status"] == "PASS"
    freeze(suite, out)
    assert (suite / "policy-lock.json").exists()
    with pytest.raises(FileExistsError):
        run(suite, out)
    (out / "results.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        check(suite, out)


def budget_config():
    return {
        "model": "test-model",
        "max_http_attempts": 10,
        "budget_cny": 1,
        "prices_cny_per_million": {"input": 2, "output": 8, "cache_read": 1, "cache_write": 2},
    }


def test_unknown_http_usage_retains_reservation_and_each_retry_is_counted(tmp_path):
    trace = Trace(tmp_path)
    config = budget_config()
    budget = HTTPBudget(config, trace)
    first = httpx.Request(
        "POST", "https://example.test", json={"model": "test-model", "max_tokens": 1024}
    )
    budget.request(first)
    held = budget.holds[1]
    budget.response(httpx.Response(503, json={"error": "unknown"}, request=first))
    assert budget.holds[1] == held
    second = httpx.Request(
        "POST", "https://example.test", json={"model": "test-model", "max_tokens": 1024}
    )
    budget.request(second)
    assert len(budget.holds) == 2
    config["budget_cny"] = 0
    with pytest.raises(FatalAPIError):
        budget.request(second)
    assert len(budget.holds) == 2
    trace.stream.close()


def test_protocol_failure_keeps_raw_usage_before_stopping(tmp_path):
    trace = Trace(tmp_path)
    budget = HTTPBudget(budget_config(), trace)
    req = httpx.Request("POST", "https://example.test", json={"model": "test-model"})
    budget.request(req)
    with pytest.raises(FatalAPIError):
        budget.response(
            httpx.Response(
                200,
                request=req,
                json={
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                    "content": [{"type": "thinking"}],
                },
            )
        )
    assert trace.rows[-1]["raw_response"]["usage"]["output_tokens"] == 20
    trace.stream.close()


def test_failed_dispatch_is_not_reported_as_zero_calls(suite, tmp_path):
    cases, facts, _ = load_suite(suite)
    case = copy.deepcopy(
        next(c for c in cases if c["split"] == "dev" and c["scenario"] == "normal")
    )
    trace = Trace(tmp_path)
    trace.context = {"case_id": case["id"], "phase": "initial"}

    class Broken:
        def complete(self, *args, **kwargs):
            raise RuntimeError("private provider error")

    with pytest.raises(RuntimeError):
        evaluate_case(case, facts, trace, live=Broken(), model="fake-live-for-test")
    assert len([r for r in trace.rows if r["status"] == "started"]) == 1
    assert trace.rows[-1]["usage"] is None
    assert "private provider error" not in str(trace.rows)
    trace.stream.close()


def test_repair_failure_preserves_initial_strategy_results(suite, tmp_path):
    cases, facts, _ = load_suite(suite)
    case = next(c for c in cases if c["split"] == "dev" and c["scenario"] == "wrong_unit")
    fixture = FixtureResponder(case)
    trace = Trace(tmp_path)
    trace.context = {"case_id": case["id"], "phase": "initial"}

    class FailRepair:
        def complete(self, messages, **kwargs):
            if any(m["role"] == "user" and "A verifier checked" in m["content"] for m in messages):
                raise FatalAPIError("controlled failure")
            return fixture.complete(messages, **kwargs)

    rows = evaluate_case(case, facts, trace, live=FailRepair(), model="test-model")
    assert [r["status"] for r in rows] == ["completed", "completed", "error"]
    assert rows[0]["answer"] == rows[1]["answer"]
    assert rows[2]["shared_model_calls"] == 2 and rows[2]["incremental_model_calls"] == 1
    assert trace.context["fatal"] is True and rows[2]["usage"] is None
    trace.stream.close()


def test_growth_tool_can_read_the_frozen_previous_year(suite, tmp_path):
    cases, facts, _ = load_suite(suite)
    case = next(
        c
        for c in cases
        if c["split"] == "dev" and c["scenario"] == "normal" and c["target"]["kind"] == "yoy_growth"
    )
    fixture = FixtureResponder(case)
    trace = Trace(tmp_path)
    trace.context = {"case_id": case["id"], "phase": "initial"}

    class PreviousYear:
        def complete(self, messages, **kwargs):
            response = fixture.complete(messages, **kwargs)
            if response.tool_calls:
                response.tool_calls[0].arguments["fiscal_year"] -= 1
            return response

    evaluate_case(case, facts, trace, live=PreviousYear(), model="test-model")
    outputs = [
        json.loads(r["raw_result"])
        for r in trace.rows
        if r["kind"] == "tool" and r["status"] == "completed"
    ]
    assert outputs and all("error" not in r for r in outputs)
    assert outputs[0]["fiscal_year"] == case["target"]["fiscal_year"] - 1
    trace.stream.close()
