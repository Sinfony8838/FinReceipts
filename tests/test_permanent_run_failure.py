"""Deterministic numeric failures stop; transient execution failures can resume."""

import pytest

from finreceipts.llm.client import FakeLLM
from finreceipts.runtime.store import RunConflict, RunStore, verify_export
from tests.test_run_api import BODY, HEADERS
from tests.test_run_api import client as _isolated_client

client = _isolated_client


def test_failure_codes_are_allowlisted_and_stale_workers_cannot_overwrite_cancel(client):
    doc = client.post("/api/runs", json=BODY, headers=HEADERS).json()
    store = client.app.state.fin.runs.store
    claimed, token = store.claim(doc["run_id"], doc["revision"])
    before = store.export(doc["run_id"])
    for options in [
        {"permanent_reason": "private exception text"},
        {"permanent_reason": "audit_input_limit", "budget": True},
    ]:
        with pytest.raises(ValueError, match="invalid permanent failure classification"):
            store.fail(doc["run_id"], token, **options)
        assert store.export(doc["run_id"]) == before
    store.cancel(doc["run_id"], claimed["revision"])
    cancelled = store.export(doc["run_id"])
    with pytest.raises(RunConflict):
        store.fail(doc["run_id"], token, permanent_reason="audit_input_limit")
    assert store.export(doc["run_id"]) == cancelled


@pytest.mark.parametrize(
    ("answer", "reason"),
    [("$1 " * 257, "audit_input_limit"), ("$" + "9" * 400, "numeric_out_of_range")],
    ids=["numeric-limit", "numeric-overflow"],
)
def test_permanent_failure_is_terminal_readable_and_never_retries(client, answer, reason):
    llm = FakeLLM(lambda *_: answer)
    client.app.state.fin.runs.llm = llm
    doc = client.post("/api/runs", json=BODY, headers=HEADERS).json()
    url = "/api/runs/" + doc["run_id"]
    for _ in range(4):
        if doc["status"] != "ready":
            break
        response = client.post(url + "/advance", json={"expected_revision": doc["revision"]})
        assert response.status_code == 200
        doc = response.json()
    assert doc["status"] == "failed" and doc["stop_reason"] == reason
    assert doc["lease"] is None and doc["dispatches"] == len(llm.calls) == 1
    assert not doc["quality"]["numeric_gate_passed"]
    original = client.get(url + "/export").content
    bundle = client.get(url + "/export").json()
    verify_export(bundle)
    assert bundle["events"][-1]["payload"]["reason"] == reason
    for route in ["advance", "resume", "cancel"]:
        body = {"expected_revision": doc["revision"]}
        if route == "resume":
            body["acknowledge_possible_duplicate"] = True
        response = client.post(url + "/" + route, json=body)
        assert response.status_code == 409
        assert client.get(url + "/export").content == original
    checked = client.get(url + "/counter-audit")
    assert checked.status_code == 422 and checked.json()["code"] == reason
    assert client.get(url + "/export").content == original
    assert client.get("/api/runs").json()["items"][0]["status"] == "failed"
    reopened = RunStore(client.app.state.fin.runs.store.path)
    assert reopened.get(doc["run_id"]) == doc
    verify_export(reopened.export(doc["run_id"]))
    assert len(llm.calls) == 1


def test_provider_failure_still_resumes_with_reserved_budget(client):
    attempts = []

    def respond(*_):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("transient provider failure with private detail")
        return "Apple FY2023 revenue was $383.285 billion."

    client.app.state.fin.runs.llm = FakeLLM(respond)
    doc = client.post("/api/runs", json=BODY, headers=HEADERS).json()
    url = "/api/runs/" + doc["run_id"]
    doc = client.post(url + "/advance", json={"expected_revision": doc["revision"]}).json()
    assert doc["status"] == "interrupted" and doc["stop_reason"] == "step_failed"
    assert doc["dispatches"] == 1
    response = client.post(
        url + "/resume",
        json={
            "expected_revision": doc["revision"],
            "acknowledge_possible_duplicate": True,
        },
    )
    assert response.status_code == 200
    doc = response.json()
    for _ in range(10):
        if doc["status"] != "ready":
            break
        doc = client.post(url + "/advance", json={"expected_revision": doc["revision"]}).json()
    assert doc["status"] == "completed" and doc["dispatches"] == len(attempts) == 2
    assert "private detail" not in client.get(url + "/export").text
