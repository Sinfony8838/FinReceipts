"""Real file locks, process races and mid-transaction faults; no paid services."""

import multiprocessing
import sqlite3
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

import pytest

from finreceipts.llm.client import FakeLLM
from finreceipts.runtime.service import RunService
from finreceipts.runtime.store import RunConflict, RunStore, StoreBusy, verify_export
from tests.test_runtime import RIGHT, request, setup_run


def _init_race(gate):
    global _race_gate
    _race_gate = gate


def _race(path, run_id, revision, action, index):
    store = RunStore(path, busy_timeout=5)
    _race_gate.wait(timeout=20)
    try:
        if action == "claim":
            return store.claim(run_id, revision)[0]["run_id"]
        body = {"question": str(index) if action == "different_request" else "same"}
        return store.create("shared-key", body, {}, {})["run_id"]
    except RunConflict:
        return "conflict"


@pytest.mark.parametrize("action", ["claim", "same_request", "different_request"])
def test_process_races_have_one_committed_winner(tmp_path, provider, action):
    service, doc = setup_run(tmp_path, provider)
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=4,
        mp_context=context,
        initializer=_init_race,
        initargs=(context.Barrier(4),),
    ) as pool:
        futures = [
            pool.submit(_race, service.store.path, doc["run_id"], doc["revision"], action, i)
            for i in range(4)
        ]
        results = [future.result(timeout=40) for future in futures]
    winners = [result for result in results if result != "conflict"]
    assert len(winners) == (4 if action == "same_request" else 1)
    assert len(set(winners)) == 1
    bundle = service.store.export(winners[0])
    verify_export(bundle)
    assert len(bundle["events"]) == (2 if action == "claim" else 1)
    assert bundle["run"]["dispatches"] == 0
    with service.store.connection() as db:
        count = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert count == (1 if action == "claim" else 2)


def test_event_insert_failure_rolls_back_already_written_checkpoint(tmp_path, provider):
    service, doc = setup_run(tmp_path, provider)
    before = service.store.export(doc["run_id"])
    with service.store.connection() as db:
        db.execute("""
            CREATE TRIGGER reject_event BEFORE INSERT ON events
            BEGIN SELECT RAISE(ABORT, 'injected event insert failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        service.store.claim(doc["run_id"], doc["revision"])
    assert service.store.export(doc["run_id"]) == before
    with service.store.connection() as db:
        db.execute("DROP TRIGGER reject_event")
    claimed, _ = service.store.claim(doc["run_id"], doc["revision"])
    assert claimed["revision"] == doc["revision"] + 1


def test_process_death_between_checkpoint_update_and_event_insert_rolls_back(tmp_path, provider):
    service, doc = setup_run(tmp_path, provider)
    before = service.store.export(doc["run_id"])
    program = """
import os, sys
from contextlib import contextmanager
from finreceipts.runtime.store import RunStore
class CrashingStore(RunStore):
    @contextmanager
    def connection(self):
        with super().connection() as db:
            def trace(sql):
                if sql.startswith("INSERT INTO events"):
                    os._exit(29)
            db.set_trace_callback(trace)
            yield db
store = CrashingStore(sys.argv[1])
store.claim(sys.argv[2], int(sys.argv[3]))
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
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 29, result.stderr.decode(errors="replace")
    restarted = RunStore(service.store.path)
    assert restarted.export(doc["run_id"]) == before
    claimed, _ = restarted.claim(doc["run_id"], doc["revision"])
    assert claimed["status"] == "running"
    verify_export(restarted.export(doc["run_id"]))


def test_export_reads_one_snapshot_while_another_connection_commits(
    tmp_path, provider, monkeypatch
):
    service, doc = setup_run(tmp_path, provider)
    before = service.store.export(doc["run_id"])
    writer = RunStore(service.store.path)
    original_load = service.store._load

    def interleaved_load(db, run_id):
        # Establish the read snapshot before a competing committed mutation.
        db.execute("SELECT document FROM runs WHERE run_id=?", (run_id,)).fetchone()
        writer.cancel(run_id, doc["revision"])
        return original_load(db, run_id)

    monkeypatch.setattr(service.store, "_load", interleaved_load)
    assert service.store.export(doc["run_id"]) == before
    latest = writer.export(doc["run_id"])
    assert latest["run"]["status"] == "cancelled"
    verify_export(latest)


def test_lock_after_model_response_preserves_budget_until_explicit_recovery(tmp_path, provider):
    now = [100.0]
    store = RunStore(tmp_path / "runs.db", clock=lambda: now[0], lease_seconds=10, busy_timeout=0)
    with sqlite3.connect(store.path, isolation_level=None) as blocker:

        def respond(*_):
            blocker.execute("BEGIN IMMEDIATE")
            return RIGHT

        llm = FakeLLM(respond)
        service = RunService(store, llm, provider)
        doc = service.create("key", request(max_dispatches=1))
        try:
            with pytest.raises(StoreBusy):
                service.advance(doc["run_id"], doc["revision"])
        finally:
            blocker.rollback()
    pending = store.get(doc["run_id"])
    assert pending["status"] == "running"
    assert pending["dispatches"] == len(llm.calls) == 1
    assert not pending["state"].get("answer")
    with pytest.raises(RunConflict):
        store.resume(doc["run_id"], pending["revision"])
    now[0] += 11
    ready = store.resume(doc["run_id"], pending["revision"])
    recovered_llm = FakeLLM(lambda *_: pytest.fail("spent budget must not be replayed"))
    result = RunService(store, recovered_llm, provider).advance(doc["run_id"], ready["revision"])
    assert result["status"] == "budget_exhausted"
    assert result["dispatches"] == 1
    verify_export(store.export(doc["run_id"]))


@pytest.mark.parametrize(
    "code", [sqlite3.SQLITE_FULL, sqlite3.SQLITE_IOERR, sqlite3.SQLITE_CORRUPT]
)
def test_non_contention_storage_errors_are_not_marked_retryable(tmp_path, code):
    store = RunStore(tmp_path / "runs.db")
    error = sqlite3.OperationalError("private-path provider-secret")
    error.sqlite_errorcode = code
    with pytest.raises(sqlite3.OperationalError) as raised, store.connection():
        raise error
    assert raised.value is error


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan")])
def test_invalid_busy_timeout_is_rejected_before_creating_database(tmp_path, timeout):
    path = tmp_path / "runs.db"
    with pytest.raises(ValueError, match="busy_timeout"):
        RunStore(path, busy_timeout=timeout)
    assert not path.exists()
