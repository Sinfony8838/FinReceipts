"""Authenticated run ownership, scoped idempotency and non-mutating refusals."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finreceipts.app.auth import AuthPolicy
from finreceipts.app.main import create_app
from tests.test_run_api import BODY
from tests.test_target import TARGET


@pytest.fixture
def authenticated(tmp_path, monkeypatch):
    monkeypatch.setenv("FINRECEIPTS_AUTH_MODE", "token")
    monkeypatch.setenv("FINRECEIPTS_AUTH_TOKENS_ENV", "TEST_FIN_AUTH_HASHES")
    monkeypatch.setenv(
        "TEST_FIN_AUTH_HASHES",
        json.dumps(
            {u: hashlib.sha256((u + "-token").encode()).hexdigest() for u in ("alice", "bob")}
        ),
    )
    monkeypatch.setenv("FINRECEIPTS_RUN_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("FINRECEIPTS_LLM_PROVIDER", "fake")
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    monkeypatch.setenv("FINRECEIPTS_CACHE_DIR", str(Path(__file__).parent / "fixtures/sec"))
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        yield client


def headers(user="alice", key="same-key-123"):
    return {"Authorization": "Bearer " + user + "-token", "Idempotency-Key": key}


def test_same_nonce_is_scoped_to_authenticated_user(authenticated):
    c = authenticated
    a = c.post("/api/runs", json=BODY, headers=headers()).json()
    b = c.post("/api/runs", json=BODY, headers=headers("bob")).json()
    assert a["run_id"] != b["run_id"]
    before = c.get("/api/runs/" + a["run_id"] + "/export", headers=headers()).content
    assert c.post("/api/runs", json=BODY, headers=headers()).json() == a
    assert c.get("/api/runs/" + a["run_id"] + "/export", headers=headers()).content == before
    assert (
        c.post("/api/runs", json=BODY | {"question": "changed"}, headers=headers()).status_code
        == 409
    )
    assert c.get("/api/session", headers=headers()).json()["principal_id"] == "alice"
    assert c.get("/api/runs", headers=headers()).json()["items"][0]["run_id"] == a["run_id"]
    assert c.get("/api/runs", headers=headers("bob")).json()["items"][0]["run_id"] == b["run_id"]
    assert not c.app.state.fin.llm.calls


def test_every_private_endpoint_refuses_foreign_owner_before_dispatch(authenticated):
    c = authenticated
    a = c.post("/api/runs", json=BODY, headers=headers()).json()
    url = "/api/runs/" + a["run_id"]
    for suffix in ("", "/export", "/events", "/counter-audit"):
        assert c.get(url + suffix, headers=headers("bob")).status_code == 404
    for action in ("advance", "cancel", "resume"):
        body = {"expected_revision": 1}
        if action == "resume":
            body["acknowledge_possible_duplicate"] = True
        assert c.post(url + "/" + action, json=body, headers=headers("bob")).status_code == 404
    assert c.get(url, headers=headers()).json() == a
    assert not c.app.state.fin.llm.calls


def test_authentication_precedes_execution_and_cursor_is_bound(authenticated):
    c = authenticated
    for path in ("/api/session", "/api/runs", "/api/ask/stream"):
        assert c.get(path).status_code == 401
    assert c.post("/api/ask", json=BODY).status_code == 401
    assert c.post("/api/audit", json={}).status_code == 401
    for key in ("request-1", "request-2"):
        c.post("/api/runs", json=BODY, headers=headers(key=key))
    cursor = c.get("/api/runs?limit=1", headers=headers()).json()["next_cursor"]
    assert c.get("/api/runs", params={"cursor": cursor}, headers=headers("bob")).status_code == 409
    assert c.get("/api/runs", headers=headers()).headers["Cache-Control"] == "no-store"


def test_target_api_rejects_missing_evidence_and_roundtrip(authenticated):
    c = authenticated
    assert (
        c.post(
            "/api/runs", json=BODY | {"target": TARGET, "fiscal_years": [2022]}, headers=headers()
        ).status_code
        == 422
    )
    missing = BODY | {"target": TARGET, "as_of": "2000-01-01"}
    assert (
        c.post("/api/runs", json=missing, headers=headers()).json()["code"]
        == "evidence_unavailable"
    )
    doc = c.post("/api/runs", json=BODY | {"target": TARGET}, headers=headers()).json()
    for _ in range(10):
        if doc["status"] != "ready":
            break
        doc = c.post(
            "/api/runs/" + doc["run_id"] + "/advance",
            json={"expected_revision": doc["revision"]},
            headers=headers(),
        ).json()
    assert doc["status"] == "completed"
    assert doc["quality"]["target_gate_passed"]
    assert not doc["quality"]["whole_answer_verified"]
    assert doc["state"]["attempts"][0]["target_audit"]["verdict"] != "supported"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "{}",
        '{"local":"' + "a" * 64 + '"}',
        '{"alice":"plaintext"}',
        '{"a":"' + "a" * 64 + '","b":"' + "a" * 64 + '"}',
    ],
)
def test_invalid_auth_configuration_fails_closed(monkeypatch, value):
    monkeypatch.setenv("FINRECEIPTS_AUTH_MODE", "token")
    monkeypatch.setenv("FINRECEIPTS_AUTH_TOKENS_ENV", "TEST_FIN_HASHES")
    monkeypatch.setenv("TEST_FIN_HASHES", value)
    with pytest.raises(ValueError):
        AuthPolicy.from_env()
