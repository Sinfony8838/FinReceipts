"""Restore drills with real SQLite WAL, concurrent commits and failure controls."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from finreceipts.llm.client import FakeLLM
from finreceipts.runtime import backup
from finreceipts.runtime.service import RunService, counter_audit
from finreceipts.runtime.store import IntegrityError, RunConflict, RunStore


@pytest.fixture
def runtime(tmp_path, provider):
    llm = FakeLLM(lambda *_: "Apple revenue in 2023 was $383.3 billion.")
    service = RunService(RunStore(tmp_path / "live.sqlite3"), llm, provider)
    doc = service.create(
        "original-key",
        dict(
            question="Apple revenue in 2023?",
            ticker="AAPL",
            fiscal_years=[2023],
            model="demo",
            mode="verify",
            feedback="receipts",
            use_tools=False,
            as_of=None,
            max_revisions=2,
            escalate_model=None,
            max_dispatches=6,
            max_tool_calls=12,
        ),
    )
    return service, doc


def test_wal_commit_is_backed_up_even_when_raw_file_copy_misses_it(tmp_path):
    store = RunStore(tmp_path / "source #1.sqlite3")
    with store.connection() as keeper:
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("BEGIN")
        keeper.execute("SELECT count(*) FROM runs").fetchone()
        original_main = backup.file_hash(store.path)
        doc = store.create("key", {}, {}, {})
        assert backup.file_hash(store.path) == original_main
        raw = tmp_path / "raw.sqlite3"
        shutil.copyfile(store.path, raw)
        assert backup.inspect_database(raw)["run_count"] == 0  # Negative control.
        result = backup.create_backup(store.path, tmp_path / "bundle")
        assert result["summary"]["run_count"] == 1
        assert result["summary"]["anchors"][0]["run_id"] == doc["run_id"]
        assert backup.file_hash(store.path) == original_main
        assert store.export(doc["run_id"])["run"] == doc
    assert backup.verify_backup(tmp_path / "bundle") == result


def test_pinned_snapshot_stays_consistent_while_another_thread_commits(tmp_path, monkeypatch):
    store = RunStore(tmp_path / "live.sqlite3")
    doc = store.create("key", {}, {}, {})
    original = backup._readonly

    class Connection:
        def __init__(self, db):
            self.db = db

        def __getattr__(self, name):
            return getattr(self.db, name)

        def backup(self, dst, **kwargs):
            # The backup's real read transaction has already pinned revision 1.
            with ThreadPoolExecutor(1) as pool:
                changed = pool.submit(store.cancel, doc["run_id"], doc["revision"]).result(5)
            assert changed["revision"] == 2
            return self.db.backup(dst, **kwargs)

    monkeypatch.setattr(
        backup,
        "_readonly",
        lambda path, **kw: (
            original(path, **kw) if kw.get("immutable") else Connection(original(path, **kw))
        ),
    )
    result = backup.create_backup(store.path, tmp_path / "bundle")
    assert result["summary"]["anchors"][0]["revision"] == 1
    assert store.get(doc["run_id"])["revision"] == 2
    backup.verify_backup(tmp_path / "bundle")


def test_restored_answer_can_finish_without_model_or_evidence_fetch(tmp_path, runtime):
    service, doc = runtime
    doc = service.advance(doc["run_id"], doc["revision"])
    before = service.store.export(doc["run_id"])
    bundle, restored = tmp_path / "bundle", tmp_path / "restored"
    backup.create_backup(service.store.path, bundle)
    backup.restore_backup(bundle, restored)

    def forbidden(*_):
        pytest.fail("restore/verification must not redispatch or fetch evidence")

    restarted = RunService(RunStore(restored / backup.DATABASE), FakeLLM(forbidden), forbidden)
    assert restarted.store.export(doc["run_id"]) == before
    assert restarted.store.find("original-key", doc["request"]) == doc
    out = restarted.advance(doc["run_id"], doc["revision"])
    assert out["status"] == "completed"
    assert out["dispatches"] == 1
    assert counter_audit(out)["bounded_gate_passed"]
    assert service.store.export(doc["run_id"]) == before
    assert len(service.llm.calls) == 1


def test_running_backup_retains_budget_and_requires_explicit_expired_resume(tmp_path, runtime):
    service, doc = runtime
    store = service.store
    store.clock = lambda: 100
    doc, token = store.claim(doc["run_id"], doc["revision"])
    doc = store.reserve(doc["run_id"], token, "dispatches", 6)
    backup.create_backup(store.path, tmp_path / "bundle")
    backup.restore_backup(tmp_path / "bundle", tmp_path / "restored")
    restored = RunStore(tmp_path / "restored" / backup.DATABASE, clock=lambda: 101)
    assert restored.get(doc["run_id"]) == doc
    with pytest.raises(RunConflict, match="expired"):
        restored.resume(doc["run_id"], doc["revision"])
    restored.clock = lambda: 401
    resumed = restored.resume(doc["run_id"], doc["revision"])
    assert resumed["dispatches"] == 1
    assert resumed["status"] == "ready"
    claimed, new_token = restored.claim(doc["run_id"], resumed["revision"])
    assert new_token != token
    with pytest.raises(RunConflict, match="revoked"):
        restored.reserve(doc["run_id"], token, "dispatches", 6)
    assert claimed["dispatches"] == 1 and len(service.llm.calls) == 0


@pytest.mark.parametrize("operation", ["backup", "restore"])
@pytest.mark.parametrize("existing", ["empty", "populated", "file"])
def test_never_overwrites_existing_destination(tmp_path, runtime, operation, existing):
    service, _ = runtime
    bundle = tmp_path / "bundle"
    backup.create_backup(service.store.path, bundle)
    target = tmp_path / "existing"
    if existing == "file":
        target.write_bytes(b"protected")
    else:
        target.mkdir()
        if existing == "populated":
            (target / backup.DATABASE).write_bytes(b"protected")
    with pytest.raises(FileExistsError):
        if operation == "backup":
            backup.create_backup(service.store.path, target)
        else:
            backup.restore_backup(bundle, target)
    if existing != "empty":
        assert (
            target if existing == "file" else target / backup.DATABASE
        ).read_bytes() == b"protected"


def test_empty_valid_runtime_can_be_backed_up_and_restored(tmp_path):
    store = RunStore(tmp_path / "empty.sqlite3")
    result = backup.create_backup(store.path, tmp_path / "bundle")
    assert result["summary"] == {"run_count": 0, "event_count": 0, "anchors": []}
    backup.restore_backup(tmp_path / "bundle", tmp_path / "restore")


@pytest.mark.parametrize("mutation", ["checkpoint", "orphan", "event_key", "trigger", "version"])
def test_rejects_logical_corruption_even_if_sqlite_integrity_is_ok(tmp_path, runtime, mutation):
    service, _ = runtime
    with service.store.connection() as db:
        if mutation == "checkpoint":
            db.execute("UPDATE runs SET document=json_set(document,'$.dispatches',0.5)")
        elif mutation == "orphan":
            db.execute("INSERT INTO events VALUES ('orphan',1,'{}')")
        elif mutation == "event_key":
            db.execute("UPDATE events SET seq=2")
        elif mutation == "version":
            db.execute("UPDATE runs SET document=json_set(document,'$.workflow_version','future')")
        else:
            db.execute("CREATE TRIGGER extra AFTER UPDATE ON runs BEGIN SELECT 1; END")
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    with pytest.raises(IntegrityError):
        backup.create_backup(service.store.path, tmp_path / "bundle")
    assert not (tmp_path / "bundle" / backup.MANIFEST).exists()


@pytest.mark.parametrize("mutation", ["bytes", "summary", "format", "sidecar"])
def test_restore_rejects_invalid_bundle_before_creating_destination(tmp_path, runtime, mutation):
    service, _ = runtime
    bundle = tmp_path / "bundle"
    result = backup.create_backup(service.store.path, bundle)
    if mutation == "bytes":
        with (bundle / backup.DATABASE).open("ab") as stream:
            stream.write(b"tampered")
    elif mutation == "sidecar":
        (bundle / (backup.DATABASE + "-wal")).write_bytes(b"unexpected")
    else:
        if mutation == "summary":
            result["summary"]["run_count"] += 1
        else:
            result["database"] = "../../live.sqlite3"
        (bundle / backup.MANIFEST).write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(IntegrityError):
        backup.restore_backup(bundle, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_rechecks_copied_bytes_after_source_verification(tmp_path, runtime, monkeypatch):
    service, _ = runtime
    backup.create_backup(service.store.path, tmp_path / "bundle")
    original = shutil.copyfileobj

    def corrupt(src, dst):
        original(src, dst)
        dst.write(b"changed-during-copy")

    monkeypatch.setattr(shutil, "copyfileobj", corrupt)
    with pytest.raises(IntegrityError):
        backup.restore_backup(tmp_path / "bundle", tmp_path / "restored")
    assert not (tmp_path / "restored" / backup.MANIFEST).exists()


def test_copy_deadline_leaves_no_ready_manifest(tmp_path, runtime, monkeypatch):
    service, _ = runtime
    times = iter([0, 2])
    monkeypatch.setattr(backup.time, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError):
        backup.create_backup(service.store.path, tmp_path / "bundle", timeout=1)
    assert not (tmp_path / "bundle" / backup.MANIFEST).exists()


def test_crash_before_manifest_is_never_accepted_as_ready(tmp_path, runtime):
    service, doc = runtime
    code = """
import os, sys
from pathlib import Path
from finreceipts.runtime import backup
backup._manifest = lambda *_: os._exit(73)
backup.create_backup(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(service.store.path), str(tmp_path / "bundle")], timeout=15
    )
    assert result.returncode == 73
    with pytest.raises(FileNotFoundError):
        backup.verify_backup(tmp_path / "bundle")
    assert service.store.get(doc["run_id"]) == doc


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_bad_timeout_creates_nothing(tmp_path, runtime, timeout):
    service, _ = runtime
    with pytest.raises(ValueError):
        backup.create_backup(service.store.path, tmp_path / "bundle", timeout=timeout)
    assert not (tmp_path / "bundle").exists()


def test_missing_source_is_not_silently_created(tmp_path):
    source = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        backup.create_backup(source, tmp_path / "bundle")
    assert not source.exists() and not (tmp_path / "bundle").exists()


def test_disk_full_during_restore_cannot_publish_ready_marker(tmp_path, runtime, monkeypatch):
    service, doc = runtime
    backup.create_backup(service.store.path, tmp_path / "bundle")

    def disk_full(src, dst):
        dst.write(src.read(100))
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copyfileobj", disk_full)
    with pytest.raises(OSError, match="No space"):
        backup.restore_backup(tmp_path / "bundle", tmp_path / "restored")
    with pytest.raises(FileNotFoundError):
        backup.verify_backup(tmp_path / "restored")
    assert backup.verify_backup(tmp_path / "bundle")["summary"]["run_count"] == 1
    assert service.store.get(doc["run_id"]) == doc


def test_invalid_database_and_manifest_are_rejected(tmp_path):
    source = tmp_path / "invalid.sqlite3"
    source.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        backup.create_backup(source, tmp_path / "bundle")
    assert not (tmp_path / "bundle" / backup.MANIFEST).exists()
    (tmp_path / "bundle" / backup.MANIFEST).write_text("[]", encoding="utf-8")
    with pytest.raises(IntegrityError, match="object"):
        backup.verify_backup(tmp_path / "bundle")


def test_actual_cli_round_trip_and_failure_exit_code(tmp_path, runtime):
    service, _ = runtime
    env = os.environ | {"PYTHONIOENCODING": "utf-8"}

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "finreceipts.cli", *map(str, args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=20,
        )

    bundle, restored = tmp_path / "bundle", tmp_path / "restored"
    created = cli("runtime-backup", "--database", service.store.path, "--out", bundle)
    assert created.returncode == 0, created.stderr
    verified = cli("runtime-verify-backup", "--backup", bundle)
    assert verified.returncode == 0, verified.stderr
    assert json.loads(created.stdout) == json.loads(verified.stdout)
    recovered = cli("runtime-restore", "--backup", bundle, "--out", restored)
    assert recovered.returncode == 0, recovered.stderr
    refused = cli("runtime-restore", "--backup", bundle, "--out", restored)
    assert refused.returncode == 2 and "FileExistsError" in refused.stderr
