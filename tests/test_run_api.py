import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finreceipts.app.main import create_app
from finreceipts.runtime.store import verify_export


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINRECEIPTS_RUN_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("FINRECEIPTS_LLM_PROVIDER", "fake")
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    monkeypatch.setenv("FINRECEIPTS_CACHE_DIR", str(Path(__file__).parent / "fixtures/sec"))
    with TestClient(create_app()) as c:
        yield c


BODY = {"question": "Apple revenue for 2023?", "ticker": "AAPL", "fiscal_years": [2023]}
HEADERS = {"Idempotency-Key": "api-test-key"}


def test_api_create_advance_reconnect_export_and_counter_audit(client):
    assert client.get("/workbench").status_code == 200
    assert "研究工作台" in client.get("/workbench").text
    r = client.post("/api/runs", json=BODY, headers=HEADERS)
    assert r.status_code == 201
    doc = r.json()
    url = "/api/runs/" + doc["run_id"]
    assert client.post("/api/runs", json=BODY, headers=HEADERS).json() == doc
    assert client.get(url + "/events").text.startswith("id: 1\n")
    assert client.get(url + "/events", headers={"Last-Event-ID": "1"}).text == ""
    for _ in range(15):
        if doc["status"] != "ready":
            break
        previous = doc["revision"]
        r = client.post(url + "/advance", json={"expected_revision": previous})
        assert r.status_code == 200, r.text
        doc = r.json()
        assert (
            client.post(url + "/advance", json={"expected_revision": previous}).status_code == 409
        )
    assert doc["status"] == "completed"
    bundle = client.get(url + "/export").json()
    verify_export(bundle)
    raw = client.get(url + "/events", headers={"Last-Event-ID": "1"}).text
    payloads = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
    assert [p["seq"] for p in payloads] == list(range(2, doc["revision"] + 1))
    replay = client.get(url + "/counter-audit").json()
    assert replay["model_calls"] == 0
    assert client.get(url).json() == doc  # all read endpoints have no execution side effect
    assert client.get(url + "/events", headers={"Last-Event-ID": "bad"}).status_code == 422
    assert client.get(url + "/events?after=9999").status_code == 409


def test_api_validation_and_idempotency_conflict(client):
    assert client.post("/api/runs", json=BODY).status_code == 422
    assert (
        client.post("/api/runs", json=BODY | {"model": "expensive"}, headers=HEADERS).status_code
        == 422
    )
    assert (
        client.post("/api/runs", json=BODY | {"fiscal_years": []}, headers=HEADERS).status_code
        == 422
    )
    doc = client.post("/api/runs", json=BODY, headers=HEADERS).json()
    assert (
        client.post("/api/runs", json=BODY | {"question": "changed"}, headers=HEADERS).status_code
        == 409
    )
    url = "/api/runs/" + doc["run_id"]
    assert (
        client.post(url + "/resume", json={"expected_revision": doc["revision"]}).status_code == 422
    )
    cancelled = client.post(url + "/cancel", json={"expected_revision": doc["revision"]}).json()
    assert cancelled["status"] == "cancelled"
    assert client.get("/api/runs/missing").status_code == 404


def test_database_contention_is_retryable_without_dispatch_or_state_change(client):
    doc = client.post("/api/runs", json=BODY, headers=HEADERS).json()
    url = "/api/runs/" + doc["run_id"]
    state = client.app.state.fin
    state.runs.store.busy_timeout = 0
    before = client.get(url + "/export").json()
    with sqlite3.connect(state.runs.store.path, isolation_level=None) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        try:
            response = client.post(url + "/advance", json={"expected_revision": doc["revision"]})
            assert response.status_code == 503
            assert response.headers.get("Retry-After") == "1"
            assert "refresh" in response.json()["detail"]
            assert client.get(url + "/export").json() == before
            assert not state.llm.calls
        finally:
            blocker.rollback()
    response = client.post(url + "/advance", json={"expected_revision": doc["revision"]})
    assert response.status_code == 200
    assert response.json()["dispatches"] == len(state.llm.calls) == 1


def test_storage_io_error_is_sanitized_without_contention_retry_hint(client, monkeypatch):
    def broken(*_):
        error = sqlite3.OperationalError("private-path secret-key")
        error.sqlite_errorcode = sqlite3.SQLITE_IOERR
        raise error

    monkeypatch.setattr(client.app.state.fin.runs.store, "get", broken)
    response = client.get("/api/runs/example")
    assert response.status_code == 503
    assert "Retry-After" not in response.headers
    assert "private-path" not in response.text and "secret-key" not in response.text
