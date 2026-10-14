"""Fault injection at actual durable boundaries; all model responses are synthetic."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from finreceipts.agent.graph import AgentDeps, build_graph, initial_state
from finreceipts.llm.client import FakeLLM, LLMResponse, ToolCall
from finreceipts.runtime.codec import canonical, decode_state, digest
from finreceipts.runtime.service import RunService, counter_audit
from finreceipts.runtime.store import IntegrityError, RunConflict, RunStore, verify_export

RIGHT = "Apple revenue in 2023 was $383.3 billion."
WRONG = "Apple revenue in 2023 was $390.0 billion."


def request(**changes):
    return (
        dict(
            question="Apple revenue in 2023?",
            ticker="AAPL",
            fiscal_years=[2023],
            model="demo",
            mode="verify",
            feedback="receipts",
            use_tools=False,
            as_of=None,
            max_revisions=2,
            escalate_model=None,
            max_dispatches=6,
            max_tool_calls=12,
        )
        | changes
    )


def setup_run(tmp_path, provider, llm=None, **changes):
    llm = llm or FakeLLM(lambda *_: RIGHT)
    store = RunStore(tmp_path / "runs.db")
    service = RunService(store, llm, provider)
    return service, service.create("request-1", request(**changes))


def finish(service, doc):
    for _ in range(50):
        if doc["status"] != "ready":
            return doc
        doc = service.advance(doc["run_id"], doc["revision"])
    pytest.fail("workflow failed to stop within its node bound")


def test_restart_preserves_answer_and_evidence_without_recalling_model(tmp_path, provider):
    llm = FakeLLM(lambda *_: RIGHT)
    service, doc = setup_run(tmp_path, provider, llm)
    doc = service.advance(doc["run_id"], doc["revision"])
    assert doc["next_node"] == "verify"
    assert doc["quality"]["reason"] == "awaiting_verification"

    def forbidden(*_):
        pytest.fail("restart must not fetch fresh evidence or recall the LLM")

    restarted = RunService(RunStore(service.store.path), FakeLLM(forbidden), forbidden)
    out = finish(restarted, doc)
    assert out["status"] == "completed"
    assert out["quality"]["numeric_gate_passed"]
    assert out["evidence_digest"] == doc["evidence_digest"]
    assert out["dispatches"] == len(llm.calls) == 1
    replay = counter_audit(out)
    assert replay["bounded_gate_passed"] and replay["model_calls"] == 0
    verify_export(restarted.store.export(out["run_id"]))


@pytest.mark.parametrize("as_of", [None, date(2024, 1, 1)])
def test_idempotent_create_is_read_only_and_rejects_changed_body(tmp_path, provider, as_of):
    service, doc = setup_run(tmp_path, provider, as_of=as_of)
    service.lookup_provider = lambda *_: pytest.fail("idempotent retry refetched evidence")
    assert service.create("request-1", request(as_of=as_of)) == doc
    with pytest.raises(RunConflict):
        service.create("request-1", request(as_of=as_of, max_dispatches=7))


def test_revision_compare_and_swap_allows_only_one_concurrent_worker(tmp_path, provider):
    service, doc = setup_run(tmp_path, provider)

    def claim(_):
        try:
            return service.store.claim(doc["run_id"], doc["revision"])[1]
        except RunConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sum(r is not None for r in results) == 1
    assert len(service.llm.calls) == 0


def test_cancel_fences_inflight_worker_and_does_not_refund_reservation(tmp_path, provider):
    service, doc = setup_run(tmp_path, provider)

    def cancel_during_call(*_):
        current = service.store.get(doc["run_id"])
        service.store.cancel(doc["run_id"], current["revision"])
        return RIGHT

    service.llm = FakeLLM(cancel_during_call)
    with pytest.raises(RunConflict):
        service.advance(doc["run_id"], doc["revision"])
    out = service.store.get(doc["run_id"])
    assert out["status"] == "cancelled" and out["dispatches"] == 1
    assert not out["state"].get("answer")


def test_expired_worker_cannot_commit_after_explicit_recovery(tmp_path, provider):
    now = [100.0]
    store = RunStore(tmp_path / "runs.db", clock=lambda: now[0], lease_seconds=10)
    service = RunService(store, FakeLLM(lambda *_: RIGHT), provider)
    doc = service.create("key", request())
    running, old_token = store.claim(doc["run_id"], doc["revision"])
    store.reserve(doc["run_id"], old_token, "dispatches", 6)
    current = store.get(doc["run_id"])
    with pytest.raises(RunConflict):
        store.resume(doc["run_id"], current["revision"])
    now[0] += 11
    recovered = store.resume(doc["run_id"], current["revision"])
    with pytest.raises(RunConflict):
        store.finish(doc["run_id"], old_token, running["state"], "verify", {})
    out = finish(service, recovered)
    assert out["dispatches"] == 2  # unknown old dispatch is not erased
    assert out["status"] == "completed"


def test_error_is_sanitized_and_retry_cannot_reset_budget(tmp_path, provider):
    def broken(*_):
        raise RuntimeError("secret-key-123 private prompt")

    service, doc = setup_run(tmp_path, provider, FakeLLM(broken), max_dispatches=1)
    failed = service.advance(doc["run_id"], doc["revision"])
    assert failed["status"] == "interrupted"
    bundle = service.store.export(doc["run_id"])
    assert "secret-key-123" not in canonical(bundle)
    resumed = service.store.resume(doc["run_id"], failed["revision"])
    service.llm = FakeLLM(lambda *_: RIGHT)
    exhausted = finish(service, resumed)
    assert exhausted["status"] == "budget_exhausted"
    assert exhausted["dispatches"] == 1 and not service.llm.calls


def test_tool_loop_is_globally_bounded_even_inside_one_answer_node(tmp_path, provider):
    tool = ToolCall(
        "t", "get_financial_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2023}
    )
    llm = FakeLLM(lambda *_: LLMResponse("", tool_calls=[tool]))
    service, doc = setup_run(tmp_path, provider, llm, use_tools=True, max_dispatches=2)
    out = finish(service, doc)
    assert out["status"] == "budget_exhausted"
    assert out["dispatches"] == 2 and len(llm.calls) == 2


@pytest.mark.parametrize(
    "args",
    [
        {"ticker": "NVDA", "metric": "revenue", "fiscal_year": 2023},
        {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025},
        {"ticker": "AAPL", "metric": "revenue", "fiscal_year": True},
        {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2023, "url": "evil"},
    ],
)
def test_scope_policy_refuses_model_selected_tool_arguments(tmp_path, provider, args):
    replies = iter([LLMResponse("", tool_calls=[ToolCall("t", "get_financial_fact", args)]), RIGHT])
    service, doc = setup_run(tmp_path, provider, FakeLLM(lambda *_: next(replies)), use_tools=True)
    out = finish(service, doc)
    message = next(m for m in out["state"]["messages"] if m["role"] == "tool")
    assert "refused" in message["content"]
    assert out["tool_calls"] == 1


def test_tool_budget_is_reserved_before_dispatch(tmp_path, provider):
    llm = FakeLLM(lambda *_: LLMResponse("", tool_calls=[ToolCall("t", "unknown", {})]))
    service, doc = setup_run(tmp_path, provider, llm, use_tools=True, max_tool_calls=0)
    out = finish(service, doc)
    assert out["status"] == "budget_exhausted" and out["tool_calls"] == 0


@pytest.mark.parametrize("target", ["evidence", "request", "state", "event", "truncate"])
def test_export_integrity_negative_controls(tmp_path, provider, target):
    service, doc = setup_run(tmp_path, provider)
    finish(service, doc)
    bundle = service.store.export(doc["run_id"])
    if target == "event":
        bundle["events"][0]["payload"]["evidence_digest"] = "bad"
    elif target == "truncate":
        bundle["events"].pop()
    else:
        bundle["run"][target]["edited"] = True
    with pytest.raises(IntegrityError):
        verify_export(bundle)


def test_failed_transaction_publishes_neither_state_nor_event(tmp_path, provider):
    service, doc = setup_run(tmp_path, provider)
    before = service.store.export(doc["run_id"])

    def fail(document):
        document["status"] = "cancelled"
        raise RuntimeError("injected transaction fault")

    with pytest.raises(RuntimeError):
        service.store.edit(doc["run_id"], "fault", fail)
    assert service.store.export(doc["run_id"]) == before


def test_corrupt_database_is_refused_before_any_model_dispatch(tmp_path, provider):
    service, doc = setup_run(tmp_path, provider)
    doc["state"]["model"] = "other"
    with service.store.connection() as db:
        db.execute("UPDATE runs SET document=?", (canonical(doc),))
    with pytest.raises(IntegrityError):
        service.advance(doc["run_id"], doc["revision"])
    assert not service.llm.calls


def test_counter_audit_exposes_numeric_collision_without_model_call(tmp_path, provider):
    # Correct Apple amount assigned to an unsupported issuer/segment.
    text = "Regional revenue in 2023 was $383.3 billion."
    service, doc = setup_run(tmp_path, provider, FakeLLM(lambda *_: text))
    out = finish(service, doc)
    before = len(service.llm.calls)
    replay = counter_audit(out)
    assert replay["numeric_gate_passed"] and not replay["bounded_gate_passed"]
    assert len(replay["disagreements"]) == 1
    assert replay["model_calls"] == 0 and len(service.llm.calls) == before


def test_evidence_mutation_after_create_cannot_change_result(tmp_path, aapl_lookup):
    lookup = dict(aapl_lookup)
    service, doc = setup_run(tmp_path, lambda *_: lookup)
    lookup.clear()
    out = finish(service, doc)
    assert out["quality"]["numeric_gate_passed"]
    assert digest(out["evidence"]) == doc["evidence_digest"]


def test_runtime_matches_langgraph_revision_policy(tmp_path, provider):
    def scripted():
        answers = iter([WRONG, RIGHT])
        return FakeLLM(lambda *_: next(answers))

    service, doc = setup_run(tmp_path, provider, scripted())
    actual = decode_state(finish(service, doc)["state"])
    graph = build_graph(AgentDeps(scripted(), provider))
    expected = graph.invoke(
        initial_state(
            **{k: v for k, v in request().items() if k not in {"max_dispatches", "max_tool_calls"}}
        )
    )
    for key in ["answer", "revisions", "escalated", "attempts", "usage"]:
        assert actual[key] == expected[key]


def test_provider_change_refuses_resume_before_claim(tmp_path, provider):
    service, doc = setup_run(tmp_path, provider)
    service.provider_id = "changed"
    with pytest.raises(RunConflict):
        service.advance(doc["run_id"], doc["revision"])
    assert service.store.get(doc["run_id"]) == doc


def test_implementation_change_refuses_advance_before_claim(tmp_path, provider):
    service, doc = setup_run(tmp_path, provider)
    service.implementation_id = "changed"
    with pytest.raises(RunConflict):
        service.advance(doc["run_id"], doc["revision"])
    assert service.store.get(doc["run_id"]) == doc


def test_answer_node_does_not_mutate_previous_usage_checkpoint(provider):
    from finreceipts.agent.graph import step_nodes

    llm = FakeLLM(lambda *_: RIGHT)
    state = initial_state("q", ticker="AAPL", fiscal_years=[2023], model="demo")
    first = step_nodes(AgentDeps(llm, provider))["answer"](state)
    assert state["usage"].calls == 0
    state.update(first)
    before = state["usage_by_model"]["demo"].calls
    step_nodes(AgentDeps(llm, provider))["answer"](state)
    assert state["usage"].calls == state["usage_by_model"]["demo"].calls == before


def test_abrupt_process_exit_retains_reserved_budget(tmp_path, provider):
    service, doc = setup_run(tmp_path, provider, max_dispatches=1)
    program = """
import os, sys
from finreceipts.llm.client import FakeLLM
from finreceipts.runtime.service import RunService
from finreceipts.runtime.store import RunStore
store = RunStore(sys.argv[1], clock=lambda: 100.0, lease_seconds=1)
def crash(*_):
    os._exit(17)
service = RunService(store, FakeLLM(crash), lambda *_: None)
service.advance(sys.argv[2], int(sys.argv[3]))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(service.store.path),
            doc["run_id"],
            str(doc["revision"]),
        ],
        timeout=30,
        check=False,
        capture_output=True,
    )
    assert result.returncode == 17, result.stderr.decode(errors="replace")
    recovered = RunStore(service.store.path, clock=lambda: 102.0)
    interrupted = recovered.get(doc["run_id"])
    assert interrupted["status"] == "running" and interrupted["dispatches"] == 1
    ready = recovered.resume(doc["run_id"], interrupted["revision"])
    llm = FakeLLM(lambda *_: RIGHT)
    out = RunService(recovered, llm, provider).advance(doc["run_id"], ready["revision"])
    assert out["status"] == "budget_exhausted" and not llm.calls
