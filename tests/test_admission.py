"""Overload tests use blocked real execution paths, never a paid provider."""

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from finreceipts.agent.graph import AgentDeps, build_graph
from finreceipts.app.admission import AdmissionControl, ExecutionGate, RequestClosed
from finreceipts.app.diagnostics import RequestDiagnostics
from finreceipts.app.main import create_app
from finreceipts.config import Settings
from finreceipts.llm.client import FakeLLM

BODY = {"question": "Apple revenue in 2023?", "ticker": "AAPL", "fiscal_years": [2023]}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FINRECEIPTS_MAX_INFLIGHT", "1")
    monkeypatch.setenv("FINRECEIPTS_RUN_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("FINRECEIPTS_LLM_PROVIDER", "fake")
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    monkeypatch.setenv("FINRECEIPTS_CACHE_DIR", str(Path(__file__).parent / "fixtures/sec"))
    with TestClient(create_app(), base_url="http://127.0.0.1") as http:
        yield http


class BlockingModel:
    def __init__(self):
        self.entered, self.release = Event(), Event()
        self.lock = Lock()
        self.calls = 0

    def __call__(self, *_):
        with self.lock:
            self.calls += 1
            first = self.calls == 1
        if first:
            self.entered.set()
            assert self.release.wait(10), "test did not release the model"
        return "Apple revenue in 2023 was $383.3 billion."


def block_model(client):
    state = client.app.state.fin
    blocked = BlockingModel()
    llm = FakeLLM(blocked)
    state.graph = build_graph(AgentDeps(llm, state.provider))
    state.runs.llm = llm
    return blocked


@pytest.mark.parametrize(
    "target", ["ask", "advance", "stream", "get_stream", "create", "audit", "counter_audit"]
)
def test_overload_is_rejected_before_dispatch_or_durable_mutation(client, target, caplog):
    doc = client.post("/api/runs", json=BODY, headers={"Idempotency-Key": "admission-test"}).json()
    url = "/api/runs/" + doc["run_id"]
    before = client.get(url + "/export").json()
    blocked = block_model(client)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, "/api/ask", json=BODY | {"mode": "baseline"})
        try:
            assert blocked.entered.wait(5)
            requests = {
                "ask": lambda: client.post("/api/ask", json=BODY | {"mode": "baseline"}),
                "advance": lambda: client.post(
                    url + "/advance", json={"expected_revision": doc["revision"]}
                ),
                "stream": lambda: client.post("/api/ask/stream", json=BODY),
                "get_stream": lambda: client.get(
                    "/api/ask/stream", params=BODY | {"fiscal_years": "2023"}
                ),
                "create": lambda: client.post(
                    "/api/runs", json=BODY, headers={"Idempotency-Key": "rejected-create"}
                ),
                "audit": lambda: client.post(
                    "/api/audit", json={"ticker": "AAPL", "text": "revenue 2023"}
                ),
                "counter_audit": lambda: client.get(url + "/counter-audit"),
            }
            with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
                response = requests[target]()
            assert response.status_code == 503
            assert response.json()["code"] == "capacity_exhausted"
            assert response.headers["Retry-After"] == "1"
            assert response.json()["request_id"] == response.headers["X-Request-ID"]
            assert blocked.calls == 1
            assert client.get(url + "/export").json() == before
            metrics = client.get("/health").json()["execution"]
            assert metrics["active"] == metrics["limit"] == metrics["peak_active"] == 1
            assert metrics["rejected"] == 1
            record = next(
                json.loads(r.getMessage())
                for r in caplog.records
                if r.name == "finreceipts.requests"
            )
            assert record["route"] != "unmatched"
            assert record["error_code"] == "capacity_exhausted" and record["outcome"] == "error"
        finally:
            blocked.release.set()
        assert first.result(timeout=10).status_code == 200
    assert client.get("/health").json()["execution"]["active"] == 0
    assert (
        client.post(url + "/advance", json={"expected_revision": doc["revision"]}).status_code
        == 200
    )
    assert blocked.calls == 2


def test_cancel_and_read_remain_available_while_capacity_is_full(client):
    doc = client.post("/api/runs", json=BODY, headers={"Idempotency-Key": "cancel-inflight"}).json()
    url = "/api/runs/" + doc["run_id"]
    blocked = block_model(client)
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(
            client.post, url + "/advance", json={"expected_revision": doc["revision"]}
        )
        try:
            assert blocked.entered.wait(5)
            current = client.get(url).json()
            assert current["status"] == "running" and current["dispatches"] == 1
            assert client.get(url + "/events").status_code == 200
            assert client.get(url + "/export").status_code == 200
            cancelled = client.post(
                url + "/cancel", json={"expected_revision": current["revision"]}
            )
            assert cancelled.status_code == 200
            assert client.get("/health").json()["execution"]["active"] == 1
            assert client.post("/api/ask", json=BODY).status_code == 503
        finally:
            blocked.release.set()
        assert first.result(timeout=10).status_code == 409
    final = client.get(url).json()
    assert final["status"] == "cancelled" and final["dispatches"] == 1
    assert not final["state"].get("answer")
    assert client.get("/health").json()["execution"]["active"] == 0


@pytest.mark.parametrize("path", ["/api/ask", "/api/ask/stream", "advance"])
def test_cancelled_http_task_cannot_free_a_still_running_model_slot(client, path):
    blocked = block_model(client)
    payload = BODY | {"mode": "baseline"}
    doc = None
    if path == "advance":
        doc = client.post(
            "/api/runs", json=BODY, headers={"Idempotency-Key": "disconnect-durable"}
        ).json()
        path = "/api/runs/" + doc["run_id"] + "/advance"
        payload = {"expected_revision": doc["revision"]}

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="http://127.0.0.1"
        ) as http:
            task = asyncio.create_task(http.post(path, json=payload))
            try:
                assert await asyncio.to_thread(blocked.entered.wait, 5)
                task.cancel()
                await asyncio.sleep(0)  # Deliver cancellation; the synchronous call stays blocked.
                assert not blocked.release.is_set()
                assert client.app.state.fin.admission.snapshot()["active"] == 1
                overloaded = await http.post("/api/ask", json=BODY)
                assert overloaded.status_code == 503 and blocked.calls == 1
            finally:
                blocked.release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            deadline = time.monotonic() + 5
            while client.app.state.fin.admission.snapshot()["active"]:
                assert time.monotonic() < deadline, "slot leaked after the worker exited"
                await asyncio.sleep(0.01)
            assert (
                await http.post("/api/ask", json=BODY | {"mode": "baseline"})
            ).status_code == 200

    asyncio.run(exercise())
    if doc:
        final = client.get("/api/runs/" + doc["run_id"]).json()
        assert final["status"] == "ready" and final["next_node"] == "verify"
        assert final["state"]["answer"] and final["dispatches"] == 1


def test_request_closing_during_worker_does_not_release_or_admit_late_work():
    gate = ExecutionGate(1)
    permit = gate.acquire()
    entered, release = Event(), Event()

    def worker():
        with permit.work():
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(worker)
        try:
            assert entered.wait(5)
            permit.close()
            permit.close()  # Closing is idempotent even while a worker is running.
            assert gate.acquire() is None
            with pytest.raises(RequestClosed), permit.work():
                pytest.fail("late work must not run")
        finally:
            release.set()
        future.result(timeout=5)
    assert gate.snapshot()["active"] == 0
    permit.close()
    next_permit = gate.acquire()
    assert next_permit is not None
    next_permit.close()
    assert gate.snapshot()["active"] == 0


@pytest.mark.parametrize(
    "path, body, expected",
    [
        ("/api/ask", {}, 422),
        ("/api/ask", BODY | {"mode": "cascade"}, 400),
        ("/api/ask/stream", BODY | {"mode": "cascade"}, 200),
    ],
)
def test_validation_and_stream_errors_release_capacity(client, monkeypatch, path, body, expected):
    # Ensure the error is configuration-related, independent of the host's model settings.
    monkeypatch.setattr(client.app.state.fin, "settings", Settings())
    for _ in range(3):
        response = client.post(path, json=body)
        assert response.status_code == expected
        assert client.app.state.fin.admission.snapshot()["active"] == 0


def test_root_path_mount_does_not_bypass_admission(client):
    permit = client.app.state.fin.admission.acquire()

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app, root_path="/prefix"),
            base_url="http://127.0.0.1",
        ) as http:
            response = await http.post("/prefix/api/ask", json=BODY)
            assert response.status_code == 503
            assert response.json()["code"] == "capacity_exhausted"
            assert (await http.get("/prefix/health")).status_code == 200

    try:
        asyncio.run(exercise())
    finally:
        permit.close()


@pytest.mark.parametrize("value", ["0", "33", "-1", "abc", "1.5"])
def test_invalid_capacity_configuration_fails_at_startup(monkeypatch, value):
    monkeypatch.setenv("FINRECEIPTS_MAX_INFLIGHT", value)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_capacity_environment_is_used_by_the_running_server(client):
    assert client.app.state.fin.settings.max_inflight == 1
    assert client.get("/health").json()["execution"]["limit"] == 1


def test_overload_does_not_read_or_buffer_the_request_body():
    gate = ExecutionGate(1)
    permit = gate.acquire()
    sent = []

    async def app(*_):
        pytest.fail("overload must reject before route execution")

    async def receive():
        pytest.fail("overload must not consume request body")

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/ask", "headers": []}
    try:
        asyncio.run(
            RequestDiagnostics(AdmissionControl(app, state=SimpleNamespace(admission=gate)))(
                scope, receive, send
            )
        )
    finally:
        permit.close()
    assert sent[0]["status"] == 503
    assert json.loads(sent[1]["body"])["code"] == "capacity_exhausted"
    assert gate.snapshot()["active"] == 0
