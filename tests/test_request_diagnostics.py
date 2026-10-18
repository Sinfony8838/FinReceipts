"""Request correlation and sensitive failure tests against the actual API."""

import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from finreceipts.app.diagnostics import RequestDiagnostics
from finreceipts.app.main import create_app
from finreceipts.runtime.store import StoreBusy

SECRET = "private-prompt api-key-123 https://provider.invalid/?token=secret"
BODY = {"question": "Apple revenue?", "ticker": "AAPL", "fiscal_years": [2023]}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FINRECEIPTS_RUN_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("FINRECEIPTS_LLM_PROVIDER", "fake")
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    monkeypatch.setenv("FINRECEIPTS_CACHE_DIR", str(Path(__file__).parent / "fixtures/sec"))
    with TestClient(create_app()) as test_client:
        yield test_client


class BrokenGraph:
    def invoke(self, *_args, **_kwargs):
        raise RuntimeError(SECRET)

    def stream(self, *_args, **_kwargs):
        raise RuntimeError(SECRET)
        yield  # make failure happen during iteration after HTTP headers


@pytest.mark.parametrize("path", ["/api/ask", "/api/ask/stream"])
def test_provider_error_is_safe_and_correlated(client, caplog, path):
    client.app.state.fin.graph = BrokenGraph()
    with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
        response = client.post(path, json=BODY)
    assert SECRET not in response.text
    request_id = response.headers["X-Request-ID"]
    assert re.fullmatch(r"[a-f0-9]{32}", request_id)
    if path.endswith("stream"):
        payloads = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        error = payloads[-1]
        assert response.status_code == 200
        assert error["event"] == "error"
        assert not any(item["event"] == "done" for item in payloads)
    else:
        assert response.status_code == 500
        error = response.json()
    assert error["request_id"] == request_id
    assert error["code"] == "ask_failed"
    records = [
        json.loads(r.getMessage()) for r in caplog.records if r.name == "finreceipts.requests"
    ]
    assert len(records) == 1
    assert records[0]["request_id"] == request_id
    assert records[0]["outcome"] == "error"
    assert records[0]["error_code"] == "ask_failed"
    assert SECRET not in caplog.text


def records_from(caplog):
    records = [r for r in caplog.records if r.name == "finreceipts.requests"]
    assert all(r.exc_info is None for r in records)
    return [json.loads(r.getMessage()) for r in records]


def test_concurrent_ids_are_unique_and_caller_headers_cannot_forge_them(client, caplog):
    with (
        caplog.at_level(logging.INFO, logger="finreceipts.requests"),
        ThreadPoolExecutor(8) as pool,
    ):
        responses = list(
            pool.map(
                lambda _: client.get(
                    "/health", headers={"X-Request-ID": "attacker-id", "Authorization": SECRET}
                ),
                range(16),
            )
        )
    request_ids = {r.headers["X-Request-ID"] for r in responses}
    assert len(request_ids) == 16
    assert all(re.fullmatch(r"[a-f0-9]{32}", value) for value in request_ids)
    records = records_from(caplog)
    assert len(records) == 16
    assert {r["request_id"] for r in records} == request_ids
    assert all(r["route"] == "/health" and r["outcome"] == "ok" for r in records)
    assert SECRET not in caplog.text and "attacker-id" not in caplog.text


def test_logs_only_route_template_and_never_path_query_or_request_content(client, caplog):
    sentinel = "private_path_prompt_token"
    with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
        missing = client.get(f"/api/runs/{sentinel}?token={sentinel}")
        unknown = client.get(f"/not-a-route/{sentinel}")
        valid = client.post("/api/ask", json=BODY | {"question": sentinel, "mode": "baseline"})
    assert missing.status_code == unknown.status_code == 404
    assert valid.status_code == 200
    records = records_from(caplog)
    assert [r["route"] for r in records] == ["/api/runs/{run_id}", "unmatched", "/api/ask"]
    assert sentinel not in caplog.text
    expected_keys = {
        "event",
        "time",
        "request_id",
        "method",
        "route",
        "status",
        "outcome",
        "error_code",
        "duration_ms",
    }
    assert all(set(r) == expected_keys and r["duration_ms"] >= 0 for r in records)


@pytest.mark.parametrize("endpoint", ["/api/ask", "/api/runs", "/api/audit"])
def test_validation_error_does_not_echo_submitted_values(client, caplog, endpoint):
    with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
        response = client.post(
            endpoint, json={"ticker": {"secret": SECRET}, "question": SECRET, "text": SECRET}
        )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert SECRET not in response.text and SECRET not in caplog.text
    assert records_from(caplog)[0]["error_code"] == "invalid_request"


def test_get_stream_bad_year_is_a_safe_client_error_not_a_server_crash(client, caplog):
    with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
        response = client.get("/api/ask/stream", params=BODY | {"fiscal_years": SECRET})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert SECRET not in response.text and SECRET not in caplog.text


def test_audit_provider_error_is_sanitized_and_classified(client, caplog):
    def fail(*_):
        raise RuntimeError(SECRET)

    client.app.state.fin.provider = fail
    with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
        response = client.post("/api/audit", json={"ticker": "AAPL", "text": SECRET})
    assert response.status_code == 503
    assert response.json()["code"] == "audit_failed"
    assert SECRET not in response.text and SECRET not in caplog.text
    assert records_from(caplog)[0]["error_code"] == "audit_failed"


@pytest.mark.parametrize("error_type", [KeyError, ValueError])
def test_provider_schema_error_is_not_misreported_as_user_input_or_missing_run(
    client, error_type, caplog
):
    def fail(*_):
        raise error_type(SECRET)

    client.app.state.fin.runs.lookup_provider = fail
    with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
        response = client.post(
            "/api/runs", json=BODY, headers={"Idempotency-Key": "schema-error-key"}
        )
    assert response.status_code == 503
    assert response.json()["code"] == "run_failed"
    assert SECRET not in response.text and SECRET not in caplog.text
    assert records_from(caplog)[0]["error_code"] == "run_failed"


def test_empty_scope_and_missing_run_still_have_specific_client_errors(client):
    client.app.state.fin.runs.lookup_provider = lambda *_: {}
    response = client.post("/api/runs", json=BODY, headers={"Idempotency-Key": "empty-scope-key"})
    assert response.status_code == 422
    assert response.json()["code"] == "evidence_unavailable"
    missing = client.get("/api/runs/missing")
    assert missing.status_code == 404
    assert missing.json()["code"] == "run_not_found"


def test_storage_busy_retains_retry_header_and_log_classification(client, monkeypatch, caplog):
    def busy(*_):
        raise StoreBusy(SECRET)

    monkeypatch.setattr(client.app.state.fin.runs.store, "get", busy)
    with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
        response = client.get("/api/runs/example")
    assert response.status_code == 503 and response.headers["Retry-After"] == "1"
    assert response.json()["code"] == "storage_busy"
    assert SECRET not in response.text and SECRET not in caplog.text
    assert records_from(caplog)[0]["error_code"] == "storage_busy"


def test_unhandled_failure_is_sanitized_before_server_can_log_traceback(client, caplog):
    @client.app.get("/injected-error")
    def unexpected():
        raise RuntimeError(SECRET)

    with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
        response = client.get("/injected-error")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert SECRET not in response.text and SECRET not in caplog.text
    assert records_from(caplog)[0]["outcome"] == "error"


@pytest.mark.parametrize("failure", ["exception", "disconnect", "cancel"])
def test_stream_protocol_failures_emit_one_safe_log_without_second_response_start(caplog, failure):
    sent = []

    async def app(scope, _receive, send):
        scope["route"] = SimpleNamespace(path="/test/stream")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        if failure == "cancel":
            raise asyncio.CancelledError()
        raise RuntimeError(SECRET)

    async def send(message):
        sent.append(message)
        if failure == "disconnect" and message["type"] == "http.response.body":
            raise OSError("client disconnected")

    async def receive():
        return {"type": "http.request", "body": b""}

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/private",
        "headers": [],
        "query_string": b"secret",
    }
    with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(RequestDiagnostics(app)(scope, receive, send))
        else:
            asyncio.run(RequestDiagnostics(app)(scope, receive, send))
    assert sum(m["type"] == "http.response.start" for m in sent) == 1
    records = records_from(caplog)
    assert len(records) == 1
    assert records[0]["outcome"] == ("error" if failure == "exception" else "disconnected")
    assert records[0]["error_code"] == ("stream_failed" if failure == "exception" else None)
    assert SECRET not in caplog.text


def test_stream_logging_waits_for_final_body_and_does_not_buffer_chunks(caplog):
    sent = []

    async def app(scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        assert sent[-1]["body"] == b"first"
        assert records_from(caplog) == []
        await send({"type": "http.response.body", "body": b"last", "more_body": False})

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    scope = {"type": "http", "method": "GET", "path": "/stream", "headers": []}
    with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
        asyncio.run(RequestDiagnostics(app)(scope, receive, send))
    assert len(records_from(caplog)) == 1
    assert records_from(caplog)[0]["outcome"] == "ok"


def test_real_server_cli_replaces_raw_access_log_with_safe_request_record(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ | {
        "FINRECEIPTS_RUN_DB": str(tmp_path / "runs.sqlite3"),
        "FINRECEIPTS_LLM_PROVIDER": "fake",
        "FINRECEIPTS_OFFLINE": "1",
        "FINRECEIPTS_RELOAD": "0",
        "FINRECEIPTS_HOST": "127.0.0.1",
        "FINRECEIPTS_PORT": str(port),
        "FINRECEIPTS_CACHE_DIR": str(Path(__file__).parent / "fixtures/sec"),
    }
    log = tmp_path / "server.log"
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "finreceipts.app"],
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5
            ) as http:
                deadline = time.monotonic() + 15
                while True:
                    assert process.poll() is None, log.read_text(encoding="utf-8")
                    try:
                        if http.get("/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, "local server did not become ready"
                    time.sleep(0.05)
                response = http.get(
                    "/api/ask/stream",
                    params={
                        "question": SECRET,
                        "ticker": "AAPL",
                        "fiscal_years": "2023",
                        "mode": "baseline",
                    },
                )
                assert response.status_code == 200 and "event: done" in response.text
                request_id = response.headers["X-Request-ID"]
                deadline = time.monotonic() + 5
                while request_id not in log.read_text(encoding="utf-8"):
                    assert time.monotonic() < deadline, "request completion record not emitted"
                    time.sleep(0.01)
        finally:
            process.terminate()
            process.wait(timeout=10)
    text = log.read_text(encoding="utf-8")
    records = [json.loads(line) for line in text.splitlines() if line.startswith('{"event":')]
    record = next(r for r in records if r["request_id"] == request_id)
    assert record["route"] == "/api/ask/stream" and record["outcome"] == "ok"
    assert "private-prompt" not in text and "api-key-123" not in text
    assert "token=secret" not in text and "question=" not in text
