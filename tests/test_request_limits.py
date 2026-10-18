"""Exercise intake and deadlines through actual routes with controlled local workers."""

import asyncio
import json
import logging
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from finreceipts.app.admission import AdmissionControl, ExecutionGate
from finreceipts.app.diagnostics import RequestDiagnostics
from finreceipts.app.limits import ResponseDeadline
from finreceipts.app.main import create_app
from finreceipts.config import Settings
from tests.test_admission import block_model

BODY = {"question": "Apple revenue in 2023?", "ticker": "AAPL", "fiscal_years": [2023]}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FINRECEIPTS_MAX_INFLIGHT", "1")
    monkeypatch.setenv("FINRECEIPTS_MAX_REQUEST_BYTES", "1024")
    monkeypatch.setenv("FINRECEIPTS_REQUEST_READ_TIMEOUT_S", "0.1")
    monkeypatch.setenv("FINRECEIPTS_RESPONSE_TIMEOUT_S", "5")
    monkeypatch.setenv("FINRECEIPTS_RUN_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("FINRECEIPTS_LLM_PROVIDER", "fake")
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    monkeypatch.setenv("FINRECEIPTS_CACHE_DIR", str(Path(__file__).parent / "fixtures/sec"))
    with TestClient(create_app(), base_url="http://127.0.0.1") as http:
        yield http


def test_large_valid_json_never_reaches_model(client):
    class ForbiddenGraph:
        def invoke(self, *_args, **_kwargs):
            pytest.fail("oversized input reached the model graph")

    client.app.state.fin.graph = ForbiddenGraph()
    response = client.post("/api/ask", json=BODY | {"question": "x" * 1024})
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/health").json()["execution"]["active"] == 0


async def raw_request(
    app, *, messages=(), headers=(), query=b"", root="", send=None, path="/api/ask"
):
    scope = {
        "type": "http",
        "method": "POST",
        "path": root + path,
        "root_path": root,
        "headers": [(b"host", b"127.0.0.1"), (b"content-type", b"application/json"), *headers],
        "query_string": query,
        "http_version": "1.1",
        "scheme": "http",
        "server": ("127.0.0.1", 80),
        "client": ("127.0.0.1", 1234),
    }
    chunks = iter(messages)
    sent = []

    async def receive():
        try:
            item = next(chunks)
        except StopIteration:
            await asyncio.Event().wait()
        if isinstance(item, float):
            await asyncio.sleep(item)
            return {"type": "http.request", "body": b"", "more_body": True}
        return item

    async def collect(message):
        sent.append(message)
        if send:
            await send(message)

    await app(scope, receive, collect)
    return sent


def chunk(body, more=False):
    return {"type": "http.request", "body": body, "more_body": more}


@pytest.mark.parametrize(
    "headers,status",
    [
        ([(b"content-length", b"1025")], 413),
        ([(b"content-length", b"9" * 5000)], 413),
        ([(b"content-length", b"-1")], 400),
        ([(b"content-length", b"+1")], 400),
        ([(b"content-length", b"")], 400),
        ([(b"content-length", b"0"), (b"content-length", b"0")], 400),
        ([(b"content-length", b"0"), (b"transfer-encoding", b"chunked")], 400),
        ([(b"content-encoding", b"gzip")], 415),
    ],
)
def test_invalid_headers_rejected_without_waiting_for_body(client, headers, status):
    sent = asyncio.run(raw_request(client.app, headers=headers))
    assert sent[0]["status"] == status  # No body messages; reading would produce 408.
    assert (b"connection", b"close") in sent[0]["headers"]
    assert client.app.state.fin.admission.snapshot()["active"] == 0


@pytest.mark.parametrize(
    "headers", [[], [(b"content-length", b"1")], [(b"transfer-encoding", b"chunked")]]
)
@pytest.mark.parametrize("terminal", [True, False])
def test_actual_chunk_bytes_enforce_cap_even_without_trusted_length(client, headers, terminal):
    sent = asyncio.run(
        raw_request(
            client.app,
            headers=headers,
            messages=[
                chunk(b"x" * 1000, True),
                chunk("汉".encode() * 9, not terminal),
            ],
        )
    )
    assert sent[0]["status"] == 413


def test_exact_utf8_limit_accepted_and_mounted_path_enforced(client):
    payload = json.dumps(
        BODY | {"mode": "baseline", "question": "汉" * 100}, ensure_ascii=False
    ).encode()
    payload += b" " * (1024 - len(payload))
    assert len(payload) == 1024
    sent = asyncio.run(
        raw_request(
            client.app,
            root="/prefix",
            messages=[chunk(payload)],
            headers=[(b"content-length", b"0001024")],
        )
    )
    assert sent[0]["status"] == 200
    sent = asyncio.run(raw_request(client.app, root="/prefix", messages=[chunk(payload + b" ")]))
    assert sent[0]["status"] == 413


def test_mismatched_length_and_upload_timeout_cannot_execute_or_mutate(client):
    created = client.post("/api/runs", json=BODY, headers={"Idempotency-Key": "intake-test"})
    assert created.status_code == 201, created.text
    doc = created.json()
    url = "/api/runs/" + doc["run_id"] + "/export"
    before = client.get(url).json()
    for messages, headers, status in [
        ([chunk(b"{}")], [(b"content-length", b"3")], 400),
        ([chunk(b"{", True), 1.0], [], 408),
        ([{"type": "http.disconnect"}], [], None),
    ]:
        sent = asyncio.run(
            raw_request(client.app, messages=messages, headers=headers, path="/api/runs")
        )
        assert (sent[0]["status"] if sent else None) == status
        assert client.app.state.fin.admission.snapshot()["active"] == 0
        assert client.get(url).json() == before


def test_query_limit_precedes_dispatch_and_overload_precedes_body_read(client):
    sent = asyncio.run(raw_request(client.app, query=b"q=" + b"x" * 16384))
    assert sent[0]["status"] == 414
    permit = client.app.state.fin.admission.acquire()
    try:
        sent = asyncio.run(raw_request(client.app))
        assert sent[0]["status"] == 503  # No body provided: intake would time out.
        assert client.get("/health").status_code == 200
    finally:
        permit.close()


def test_small_progress_chunks_do_not_restart_total_upload_deadline(client):
    sent = asyncio.run(raw_request(client.app, messages=[0.06, 0.06, chunk(b"{}")]))
    assert sent[0]["status"] == 408
    assert client.app.state.fin.admission.snapshot()["active"] == 0


@pytest.mark.parametrize(
    "path,cancel",
    [("/api/ask", False), ("/api/ask/stream", False), ("advance", False), ("advance", True)],
)
def test_timeout_returns_while_real_worker_still_holds_capacity(client, path, cancel, caplog):
    payload = BODY | {"mode": "baseline"}
    doc = None
    if path == "advance":
        doc = client.post("/api/runs", json=BODY, headers={"Idempotency-Key": "deadline"}).json()
        url = "/api/runs/" + doc["run_id"]
        path, payload = url + "/advance", {"expected_revision": doc["revision"]}
    blocked = block_model(client)
    client.app.state.fin.settings = replace(client.app.state.fin.settings, response_timeout_s=0.5)
    try:
        with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
            response = client.post(path, json=payload)
        assert blocked.entered.is_set() and not blocked.release.is_set()
        if path == "/api/ask/stream":
            assert response.status_code == 200
            assert "event: start" in response.text and "event: error" in response.text
            assert "response_timeout" in response.text and "event: done" not in response.text
        else:
            assert response.status_code == 504 and response.json()["code"] == "response_timeout"
        records = [
            json.loads(r.getMessage()) for r in caplog.records if r.name == "finreceipts.requests"
        ]
        assert records[-1]["error_code"] == "response_timeout"
        assert records[-1]["request_id"] == response.headers["x-request-id"]
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/health").json()["execution"]["active"] == 1
        assert client.post("/api/ask", json=BODY).status_code == 503
        if doc:
            current = client.get(url).json()
            assert current["dispatches"] == 1 and current["status"] == "running"
            if cancel:
                assert (
                    client.post(
                        url + "/cancel", json={"expected_revision": current["revision"]}
                    ).status_code
                    == 200
                )
    finally:
        blocked.release.set()
    deadline = time.monotonic() + 5
    while client.get("/health").json()["execution"]["active"]:
        assert time.monotonic() < deadline, "worker permit leaked"
        time.sleep(0.01)
    assert blocked.calls == 1
    if doc:
        current = client.get(url).json()
        assert current["dispatches"] == 1
        assert current["status"] == ("cancelled" if cancel else "ready")
        assert bool(current["state"].get("answer")) is not cancel
        if not cancel:
            assert current["next_node"] == "verify"


@pytest.mark.parametrize("started", [False, True])
def test_cancellation_resistant_task_cannot_send_late_output(started):
    async def exercise():
        release = asyncio.Event()
        state = SimpleNamespace(
            admission=ExecutionGate(1), settings=Settings(response_timeout_s=0.02)
        )

        async def stubborn(scope, receive, send):
            if started:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")],
                    }
                )
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            if not started:
                await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"SECRET LATE RESULT"})
            raise ValueError("SECRET LATE PROVIDER FAILURE")

        deadline = ResponseDeadline(stubborn, state=state)
        app = RequestDiagnostics(AdmissionControl(deadline, state=state))
        sent = await raw_request(app)
        original = list(sent)
        assert sent[0]["status"] == (200 if started else 504)
        assert b"response_timeout" in sent[-1]["body"]
        assert len(deadline._pending) == 1
        release.set()
        for _ in range(10):
            await asyncio.sleep(0)
        assert sent == original and not deadline._pending
        assert state.admission.snapshot()["active"] == 0

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "name,value",
    [
        ("MAX_REQUEST_BYTES", "0"),
        ("MAX_REQUEST_BYTES", "16777217"),
        ("REQUEST_READ_TIMEOUT_S", "nan"),
        ("REQUEST_READ_TIMEOUT_S", "inf"),
        ("REQUEST_READ_TIMEOUT_S", "0"),
        ("REQUEST_READ_TIMEOUT_S", "61"),
        ("RESPONSE_TIMEOUT_S", "-1"),
        ("RESPONSE_TIMEOUT_S", "nan"),
        ("RESPONSE_TIMEOUT_S", "601"),
    ],
)
def test_invalid_limits_fail_startup(monkeypatch, name, value):
    monkeypatch.setenv("FINRECEIPTS_" + name, value)
    with pytest.raises(ValueError):
        Settings.from_env()
