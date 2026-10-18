"""Read-only task discovery, bounded pages and stable insertion cursors."""

import base64
import json
import sqlite3

import pytest

from finreceipts.runtime.store import RunStore
from tests.test_run_api import BODY
from tests.test_run_api import client as _isolated_client

client = _isolated_client


def create(client, number):
    response = client.post(
        "/api/runs",
        json=BODY | {"question": f"Task {number}"},
        headers={"Idempotency-Key": f"history-{number:03}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_empty_history_is_available_without_execution(client):
    before = client.get("/health").json()["execution"]
    response = client.get("/api/runs")
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
    assert client.get("/health").json()["execution"] == before


def test_new_inserts_and_status_updates_do_not_shift_pagination(client):
    docs = [create(client, i) for i in range(5)]
    first = client.get("/api/runs?limit=2").json()
    assert [r["run_id"] for r in first["items"]] == [d["run_id"] for d in docs[:2:-1]]
    newest = create(client, 5)
    current = docs[2]
    assert (
        client.post(
            f"/api/runs/{current['run_id']}/cancel", json={"expected_revision": current["revision"]}
        ).status_code
        == 200
    )
    second = client.get("/api/runs", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [r["run_id"] for r in second["items"]] == [docs[2]["run_id"], docs[1]["run_id"]]
    assert second["items"][0]["status"] == "cancelled"
    third = client.get("/api/runs", params={"limit": 2, "cursor": second["next_cursor"]}).json()
    assert [r["run_id"] for r in third["items"]] == [docs[0]["run_id"]]
    assert third["next_cursor"] is None
    refreshed = client.get("/api/runs?limit=1").json()
    assert refreshed["items"][0]["run_id"] == newest["run_id"]


def test_metadata_is_small_and_listing_never_dispatches_or_changes_exports(client):
    state = client.app.state.fin
    response = client.post(
        "/api/runs",
        json=BODY | {"question": "x" * 12000},
        headers={"Idempotency-Key": "history-long-question"},
    )
    assert response.status_code == 201
    doc = response.json()
    url = "/api/runs/" + doc["run_id"]
    before = client.get(url + "/export").json()
    permits = [state.admission.acquire() for _ in range(state.admission.limit)]
    try:
        response = client.get("/api/runs")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["question_preview"] == "x" * 160 and item["question_truncated"]
        assert set(item) == {
            "run_id",
            "availability",
            "revision",
            "question_preview",
            "question_truncated",
            "ticker",
            "fiscal_years",
            "status",
            "dispatches",
            "max_dispatches",
            "created_at",
            "updated_at",
        }
        assert len(response.content) < 1000
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-request-id"]
        assert client.get(url + "/export").json() == before
        assert client.get("/health").json()["execution"]["active"] == state.admission.limit
    finally:
        for permit in permits:
            permit.close()


@pytest.mark.parametrize("damage", ["checkpoint", "event", "bad_json", "row_identity"])
def test_one_unreadable_record_does_not_hide_other_tasks_or_expose_tainted_text(client, damage):
    good, bad = create(client, 1), create(client, 2)
    store = client.app.state.fin.runs.store
    with store.connection() as db:
        if damage == "checkpoint":
            bad["request"]["question"] = "TAINTED QUESTION"
            db.execute(
                "UPDATE runs SET document=? WHERE run_id=?", (json.dumps(bad), bad["run_id"])
            )
        elif damage == "bad_json":
            db.execute("UPDATE runs SET document=? WHERE run_id=?", ("{TAINTED", bad["run_id"]))
        elif damage == "event":
            db.execute("UPDATE events SET document=? WHERE run_id=?", ("{TAINTED", bad["run_id"]))
        else:
            # A coherent document under the wrong table key must not be presented as valid.
            db.execute(
                "UPDATE runs SET document=(SELECT document FROM runs WHERE run_id=?) "
                "WHERE run_id=?",
                (good["run_id"], bad["run_id"]),
            )
            db.execute(
                "UPDATE events SET document=(SELECT document FROM events WHERE run_id=?) "
                "WHERE run_id=?",
                (good["run_id"], bad["run_id"]),
            )
    response = client.get("/api/runs")
    assert response.status_code == 200
    assert response.json()["items"][0] == {
        "run_id": bad["run_id"],
        "availability": "integrity_failed",
    }
    assert response.json()["items"][1]["run_id"] == good["run_id"]
    assert response.json()["items"][1]["availability"] == "available"
    assert "TAINTED" not in response.text


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 51},
        {"limit": -1},
        {"limit": "1.2"},
        {"cursor": ""},
        {"cursor": "not-a-cursor"},
        {"cursor": "x" * 257},
        {"cursor": base64.urlsafe_b64encode(b"[true,false]").decode()},
        {
            "cursor": base64.urlsafe_b64encode(
                json.dumps([[2**63, "a" * 64], [1, "b" * 64]]).encode()
            ).decode()
        },
        {
            "cursor": base64.urlsafe_b64encode(
                json.dumps([[1, "a" * 64], [2, "b" * 64]]).encode()
            ).decode()
        },
    ],
)
def test_invalid_pagination_is_rejected_without_storage_changes(client, query):
    doc = create(client, 1)
    before = client.get("/api/runs/" + doc["run_id"] + "/export").json()
    response = client.get("/api/runs", params=query)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert client.get("/api/runs/" + doc["run_id"] + "/export").json() == before


def test_cursor_after_database_replacement_is_explicitly_stale(client):
    docs = [create(client, i) for i in range(3)]
    page = client.get("/api/runs?limit=1").json()
    # Only the isolated test database: simulate an external maintenance operation.
    with client.app.state.fin.runs.store.connection() as db:
        db.execute("DELETE FROM events WHERE run_id=?", (docs[-1]["run_id"],))
        db.execute("DELETE FROM runs WHERE run_id=?", (docs[-1]["run_id"],))
    create(client, 9)  # SQLite may reuse the largest rowid; the old run ID must not match.
    response = client.get("/api/runs", params={"cursor": page["next_cursor"]})
    assert response.status_code == 409
    assert response.json()["code"] == "history_cursor_expired"


def test_listing_reads_one_snapshot_while_a_writer_commits(client, monkeypatch):
    first, second = create(client, 1), create(client, 2)
    store = client.app.state.fin.runs.store
    original = store._load
    written = False

    def load(db, run_id):
        nonlocal written
        if not written:
            written = True
            # Independent connection, committed after the page's snapshot started.
            writer = RunStore(store.path)
            writer.cancel(first["run_id"], first["revision"])
        return original(db, run_id)

    monkeypatch.setattr(store, "_load", load)
    page = client.get("/api/runs").json()
    assert [r["run_id"] for r in page["items"]] == [second["run_id"], first["run_id"]]
    assert all(r["status"] == "ready" for r in page["items"])
    assert client.get("/api/runs/" + first["run_id"]).json()["status"] == "cancelled"


def test_history_survives_store_reopen_without_a_schema_migration(client):
    create(client, 1)
    create(client, 2)
    store = client.app.state.fin.runs.store
    with sqlite3.connect(store.path) as db:
        before = db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
    expected = store.list_runs()
    assert RunStore(store.path).list_runs() == expected
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall() == before
        )


def test_listing_verifies_only_the_requested_page(client, monkeypatch):
    docs = [create(client, i) for i in range(4)]
    store = client.app.state.fin.runs.store
    original, loaded = store._load, []

    def load(db, run_id):
        loaded.append(run_id)
        return original(db, run_id)

    monkeypatch.setattr(store, "_load", load)
    page = client.get("/api/runs?limit=2").json()
    assert loaded == [docs[-1]["run_id"], docs[-2]["run_id"]]
    assert page["next_cursor"]


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"Origin": "https://untrusted.example"}, 403),
        ({"Host": "untrusted.example"}, 400),
    ],
)
def test_history_remains_inside_the_host_and_origin_boundary(client, headers, status):
    create(client, 1)
    response = client.get("/api/runs", headers=headers)
    assert response.status_code == status
    assert "Task 1" not in response.text and "items" not in response.json()


def test_damaged_task_identifier_does_not_break_the_next_page(client):
    docs = [create(client, i) for i in range(3)]
    with client.app.state.fin.runs.store.connection() as db:
        db.execute(
            "UPDATE runs SET run_id=? WHERE run_id=?", ("not-a-valid-run-id", docs[1]["run_id"])
        )
        db.execute(
            "UPDATE events SET run_id=? WHERE run_id=?", ("not-a-valid-run-id", docs[1]["run_id"])
        )
    first = client.get("/api/runs?limit=2").json()
    assert first["items"][1]["availability"] == "integrity_failed"
    response = client.get("/api/runs", params={"cursor": first["next_cursor"]})
    assert response.status_code == 200
    assert response.json()["items"][0]["run_id"] == docs[0]["run_id"]
