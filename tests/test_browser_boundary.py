"""Browser-origin defenses are not authentication; use isolated fake runtime data."""

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from finreceipts.app.access import AccessPolicy, BrowserBoundary, SecurityHeaders
from finreceipts.app.diagnostics import RequestDiagnostics
from finreceipts.app.main import create_app

BODY = {"question": "Apple revenue in 2023?", "ticker": "AAPL", "fiscal_years": [2023]}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("FINRECEIPTS_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("FINRECEIPTS_RUN_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("FINRECEIPTS_LLM_PROVIDER", "fake")
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    monkeypatch.setenv("FINRECEIPTS_CACHE_DIR", str(Path(__file__).parent / "fixtures/sec"))
    with TestClient(create_app(), base_url="http://127.0.0.1") as http:
        yield http


def test_untrusted_host_cannot_read_runtime_or_enter_model(client):
    response = client.get("/health", headers={"Host": "attacker.example"})
    assert response.status_code == 400
    assert response.json()["code"] == "untrusted_host"


def test_cross_site_get_sse_is_rejected_before_model_execution(client):
    before = client.app.state.fin.admission.snapshot()
    response = client.get(
        "/api/ask/stream",
        params=BODY | {"fiscal_years": "2023"},
        headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "cross_origin_denied"
    assert client.app.state.fin.admission.snapshot() == before
    assert len(client.app.state.fin.llm.calls) == 0


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "localhost.evil.example",
        "127.0.0.1.evil.example",
        "127.1",
        "localhost@evil.example",
        "localhost:bad",
        "localhost:",
        "localhost:0",
        "localhost:65536",
        "localhost/path",
        "localhost%00.evil",
        "[::2]",
        "",
        "localhost.",
    ],
)
def test_host_allowlist_rejects_suffixes_aliases_and_malformed_authorities(client, host):
    response = client.get("/health", headers={"Host": host})
    assert response.status_code == 400
    assert response.json()["code"] == "untrusted_host"


@pytest.mark.parametrize(
    "host,origin",
    [
        ("localhost:8000", "http://localhost:8000"),
        ("LOCALHOST:80", "http://localhost"),
        ("127.0.0.1", "http://127.0.0.1:80"),
        ("[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_exact_same_origin_and_default_ports_are_accepted(client, host, origin):
    response = client.get(
        "/health", headers={"Host": host, "Origin": origin, "Sec-Fetch-Site": "same-origin"}
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Origin": "file://"},
        {"Origin": "http://127.0.0.1:8001"},
        {"Origin": "https://127.0.0.1"},
        {"Origin": "http://127.0.0.1/"},
        {"Origin": "http://127.0.0.1?"},
        {"Origin": "http://127.0.0.1#"},
        {"Origin": "http://evil.example http://127.0.0.1"},
        {"Origin": "http://127.0.0.1", "Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "invalid"},
        {"Referer": "http://evil.example/question?private=secret"},
    ],
)
def test_browser_origin_failures_cannot_admit_or_create_a_run(client, headers):
    before = client.app.state.fin.admission.snapshot()
    response = client.post(
        "/api/runs", json=BODY, headers=headers | {"Idempotency-Key": "blocked-run"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "cross_origin_denied"
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert client.app.state.fin.admission.snapshot() == before
    with client.app.state.fin.runs.store.connection() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_cross_origin_cannot_read_export_events_or_mutate_existing_run(client):
    doc = client.post("/api/runs", json=BODY, headers={"Idempotency-Key": "existing-run"}).json()
    url = "/api/runs/" + doc["run_id"]
    original = client.get(url + "/export").json()
    for route in ["", "/export", "/events", "/counter-audit"]:
        response = client.get(url + route, headers={"Origin": "http://evil.example"})
        assert response.status_code == 403
        assert doc["run_id"] not in response.text
    for action in ["advance", "cancel", "resume"]:
        response = client.post(
            url + "/" + action,
            json={"expected_revision": doc["revision"], "acknowledge_possible_duplicate": True},
            headers={"Sec-Fetch-Site": "same-site"},
        )
        assert response.status_code == 403
    assert client.get(url + "/export").json() == original
    assert len(client.app.state.fin.llm.calls) == 0


def test_non_browser_and_same_origin_requests_still_work(client):
    # Deliberate compatibility boundary: native clients without browser headers
    # remain trusted. This control is not a bearer-token or tenant boundary.
    assert client.get("/health").status_code == 200
    assert (
        client.get("/health", headers={"Referer": "http://127.0.0.1/workbench"}).status_code == 200
    )
    assert client.get("/health", headers={"Sec-Fetch-Site": "none"}).status_code == 200
    response = client.post(
        "/api/ask",
        json=BODY | {"mode": "baseline"},
        headers={"Origin": "http://127.0.0.1", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200
    assert len(client.app.state.fin.llm.calls) == 1


def test_headers_prevent_framing_and_caching_private_success_and_error(client):
    for route in ["/", "/workbench", "/health", "/api/runs/missing"]:
        response = client.get(route)
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        if route.startswith("/api") or route == "/health":
            assert response.headers["cache-control"] == "no-store"
    assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_unexpected_exception_keeps_no_store_and_frame_policy(client):
    @client.app.get("/api/unexpected")
    def unexpected():
        raise RuntimeError("sensitive failure")

    response = client.get("/api/unexpected")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "sensitive failure" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "*.example.com",
        "https://app.example.com",
        "",
        ",",
        "localhost:8000",
        "localhost,",
        "bad host",
    ],
)
def test_invalid_host_configuration_fails_closed(monkeypatch, value):
    monkeypatch.setenv("FINRECEIPTS_ALLOWED_HOSTS", value)
    with pytest.raises(ValueError):
        AccessPolicy.from_env()


def test_configured_exact_hostname_is_accepted_without_wildcard_expansion(client, monkeypatch):
    monkeypatch.setenv("FINRECEIPTS_ALLOWED_HOSTS", "app.example.com,[::1]")
    client.app.state.fin.access_policy = AccessPolicy.from_env()
    assert client.get("/health", headers={"Host": "app.example.com"}).status_code == 200
    assert client.get("/health", headers={"Host": "sub.app.example.com"}).status_code == 400
    assert client.get("/health").status_code == 400


@pytest.mark.parametrize(
    "name,values,code",
    [
        (b"host", [b"127.0.0.1", b"evil.example"], "untrusted_host"),
        (b"origin", [b"http://127.0.0.1", b"http://evil.example"], "cross_origin_denied"),
        (b"sec-fetch-site", [b"same-origin", b"cross-site"], "cross_origin_denied"),
    ],
)
def test_ambiguous_headers_are_rejected_before_body_read_under_root_path(
    name, values, code, caplog
):
    async def forbidden(*_):
        pytest.fail("rejected request reached body read or downstream application")

    state = SimpleNamespace(access_policy=AccessPolicy(frozenset({"127.0.0.1"})))
    app = SecurityHeaders(RequestDiagnostics(BrowserBoundary(forbidden, state=state)))
    headers = [(b"host", b"127.0.0.1")] if name != b"host" else []
    headers += [(name, value) for value in values]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/prefix/api/ask",
        "root_path": "/prefix",
        "scheme": "http",
        "headers": headers,
        "query_string": b"",
    }
    messages = []

    async def send(message):
        messages.append(message)

    with caplog.at_level(logging.INFO, logger="finreceipts.requests"):
        asyncio.run(app(scope, forbidden, send))
    content = json.loads(messages[1]["body"])
    assert content["code"] == code
    assert "evil.example" not in caplog.text
    assert "evil.example" not in json.dumps(content)
    assert dict(messages[0]["headers"])[b"cache-control"] == b"no-store"
