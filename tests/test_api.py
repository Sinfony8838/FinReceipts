"""Smoke tests for the FastAPI + SSE surface (fake LLM, offline SEC fixtures)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from finreceipts.app.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FINRECEIPTS_LLM_PROVIDER", "fake")
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    # Prefer test fixtures as the SEC cache
    fixtures = __import__("pathlib").Path(__file__).parent / "fixtures" / "sec"
    monkeypatch.setenv("FINRECEIPTS_CACHE_DIR", str(fixtures))
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["provider"] == "fake"


def test_index_and_static_report(client: TestClient):
    assert client.get("/").status_code == 200
    assert "FinReceipts" in client.get("/").text
    # Synthetic example is bundled in the source-first release
    r = client.get("/reports/synthetic_receipts.html")
    assert r.status_code == 200
    assert "num ok" in r.text and "num bad" in r.text


def test_audit_endpoint(client: TestClient):
    r = client.post(
        "/api/audit",
        json={
            "ticker": "AAPL",
            "fiscal_years": [2023],
            "question": "Revenue?",
            "text": "Revenue was $383.3 billion and 161,000 employees.",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["audit_summary"]["supported"] >= 1
    assert body["audit_summary"]["unsupported"] >= 1
    assert '<span class="num ok"' in body["html"]
    assert '<span class="num bad"' in body["html"]


def test_ask_once(client: TestClient):
    r = client.post(
        "/api/ask",
        json={
            "question": "What was Apple's revenue for the fiscal year ended September 30, 2023?",
            "ticker": "AAPL",
            "fiscal_years": [2023],
            "mode": "verify",
            "feedback": "flags",
            "max_revisions": 2,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "ANSWER:" in body["answer"] or "383" in body["answer"]
    assert body["audit_summary"]["numbers"] >= 1
    assert "html" in body and "FinReceipts" in body["html"]


def test_ask_stream_sse(client: TestClient):
    with client.stream(
        "POST",
        "/api/ask/stream",
        json={
            "question": "What was Apple's revenue for the fiscal year ended September 30, 2023?",
            "ticker": "AAPL",
            "fiscal_years": [2023],
            "mode": "verify",
            "feedback": "flags",
            "max_revisions": 1,
        },
    ) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        raw = "".join(r.iter_text())
    assert "event: start" in raw
    assert "event: step" in raw
    assert "event: done" in raw
    # parse last done payload
    done_line = [ln for ln in raw.splitlines() if ln.startswith("data: ") and '"done"' in ln][-1]
    payload = json.loads(done_line.removeprefix("data: "))
    assert payload["event"] == "done"
    assert "html" in payload


def test_ask_stream_get(client: TestClient):
    with client.stream(
        "GET",
        "/api/ask/stream",
        params={
            "question": "Summarize NVIDIA FY2025",
            "ticker": "NVDA",
            "fiscal_years": "2024,2025",
            "mode": "baseline",
            "as_of": "2025-06-30",
        },
    ) as r:
        assert r.status_code == 200
        raw = "".join(r.iter_text())
    assert "event: done" in raw or "event: error" in raw
