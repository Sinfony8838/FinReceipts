"""Offline backup bundles and non-overwriting restore; never replay a model call."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from finreceipts.runtime.codec import canonical
from finreceipts.runtime.store import WORKFLOW_VERSION, IntegrityError, scoped_key, verify_export

FORMAT = "finreceipts-backup-v2"
LEGACY_FORMAT = "finreceipts-backup-v1"
DATABASE = "runs.sqlite3"
MANIFEST = "manifest.json"
# A closed schema contract: do not restore unknown tables, triggers or views into
# the application. A schema change requires an explicit migration/format review.
_TABLES = {
    "runs": """CREATE TABLE runs (
        run_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
        document TEXT NOT NULL
    )""",
    "events": """CREATE TABLE events (
        run_id TEXT NOT NULL, seq INTEGER NOT NULL, document TEXT NOT NULL,
        PRIMARY KEY (run_id, seq)
    )""",
}
_TABLES_V2 = {
    **_TABLES,
    "runs": """CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, document TEXT NOT NULL,
    owner_id TEXT NOT NULL DEFAULT 'local', operation TEXT NOT NULL DEFAULT 'create_run'
)""",
}


def _schema_version(db):
    columns = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
    return 2 if {"owner_id", "operation"} <= columns else 1


def _normalize(sql: str) -> str:
    return "".join(sql.lower().split())


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _readonly(path: Path, *, immutable=False):
    # URI quoting matters for Windows drive letters and paths containing #/?.
    uri = path.resolve(strict=True).as_uri() + "?mode=ro"
    if immutable:
        uri += "&immutable=1"
    db = sqlite3.connect(uri, uri=True, timeout=0)
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def _no_sidecars(path: Path) -> None:
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise IntegrityError("backup must be standalone, without SQLite sidecars")


def inspect_database(path: Path) -> dict:
    """Validate a closed, standalone snapshot, including row keys and orphans."""
    _no_sidecars(path)
    with closing(_readonly(path, immutable=True)) as db:
        schema = db.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall()
        tables = _TABLES_V2 if _schema_version(db) == 2 else _TABLES
        expected = {("table", name, name, _normalize(sql)) for name, sql in tables.items()} | {
            ("index", "sqlite_autoindex_runs_1", "runs", None),
            ("index", "sqlite_autoindex_runs_2", "runs", None),
            ("index", "sqlite_autoindex_events_1", "events", None),
        }
        actual = {
            (kind, name, table, _normalize(sql) if sql else None)
            for kind, name, table, sql in schema
        }
        if actual != expected:
            raise IntegrityError("unsupported runtime database schema")
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise IntegrityError("SQLite integrity check failed")
        orphan = db.execute(
            "SELECT 1 FROM events e LEFT JOIN runs r ON e.run_id=r.run_id "
            "WHERE r.run_id IS NULL LIMIT 1"
        ).fetchone()
        if orphan:
            raise IntegrityError("orphan event")
        anchors = []
        for run_id, raw in db.execute("SELECT run_id, document FROM runs ORDER BY run_id"):
            doc = json.loads(raw)
            events = []
            for seq, event_raw in db.execute(
                "SELECT seq, document FROM events WHERE run_id=? ORDER BY seq", (run_id,)
            ):
                event = json.loads(event_raw)
                if event["seq"] != seq:
                    raise IntegrityError("event row key mismatch")
                events.append(event)
            if doc["run_id"] != run_id or doc["workflow_version"] != WORKFLOW_VERSION:
                raise IntegrityError("run identity or workflow version mismatch")
            verify_export({"run": doc, "events": events})
            if tables is _TABLES_V2:
                owner, operation = db.execute(
                    "SELECT owner_id,operation FROM runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if doc.get("scope", {"principal_id": "local", "operation": "create_run"}) != {
                    "principal_id": owner,
                    "operation": operation,
                }:
                    raise IntegrityError("run ownership binding mismatch")
            anchors.append(
                {
                    "run_id": run_id,
                    "revision": doc["revision"],
                    "event_hash": events[-1]["hash"],
                    "state_digest": events[-1]["state_digest"],
                    "status": doc["status"],
                    "dispatches": doc["dispatches"],
                    "tool_calls": doc["tool_calls"],
                    "implementation_id": doc["request"].get("implementation_id"),
                }
            )
        return {
            "run_count": len(anchors),
            "event_count": sum(a["revision"] for a in anchors),
            "anchors": anchors,
        }


def _copy_snapshot(source: Path, target: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout

    def progress(_status, _remaining, _total):
        if time.monotonic() >= deadline:
            raise TimeoutError("SQLite backup copy deadline exceeded")

    with closing(_readonly(source)) as src, closing(sqlite3.connect(target)) as dst:
        # Pin the snapshot so concurrent writers cannot restart the copy forever.
        # WAL writers can continue; the read transaction can delay WAL truncation.
        src.execute("BEGIN")
        src.execute("SELECT count(*) FROM sqlite_master").fetchone()
        src.backup(dst, pages=128, progress=progress, sleep=0.01)
        dst.execute("PRAGMA journal_mode=DELETE")


def _sync(path: Path) -> None:
    # Windows FlushFileBuffers requires a writable handle.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _manifest(directory: Path, payload: dict) -> None:
    # A crash before rename leaves an incomplete directory, never a ready bundle.
    pending = directory / "manifest.pending"
    with pending.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical(payload) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    pending.rename(directory / MANIFEST)


def create_backup(source: Path, destination: Path, *, timeout: float = 30) -> dict:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("copy timeout must be positive and finite")
    source = Path(source).resolve(strict=True)
    if not source.is_file():
        raise ValueError("source must be an existing database file")
    destination = Path(destination)
    destination.mkdir()  # Exclusive claim; even an existing empty directory is refused.
    target = destination / DATABASE
    _copy_snapshot(source, target, timeout)
    summary = inspect_database(target)
    _sync(target)
    payload = {
        "format": _database_format(target),
        "created_at": datetime.now(UTC).isoformat(),
        "database": DATABASE,
        "sha256": file_hash(target),
        "bytes": target.stat().st_size,
        "workflow_version": WORKFLOW_VERSION,
        "sqlite_version": sqlite3.sqlite_version,
        "summary": summary,
    }
    _manifest(destination, payload)
    return payload


def _read_manifest(directory: Path) -> dict:
    payload = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise IntegrityError("backup manifest must be an object")
    if (
        payload.get("format") not in {FORMAT, LEGACY_FORMAT}
        or payload.get("database") != DATABASE
        or payload.get("workflow_version") != WORKFLOW_VERSION
    ):
        raise IntegrityError("unsupported backup format")
    return payload


def _check_copy(path: Path, payload: dict) -> dict:
    if path.stat().st_size != payload["bytes"] or file_hash(path) != payload["sha256"]:
        raise IntegrityError("backup size or SHA256 mismatch")
    summary = inspect_database(path)
    if payload["format"] != _database_format(path):
        raise IntegrityError("backup format does not match schema")
    if summary != payload["summary"]:
        raise IntegrityError("backup summary mismatch")
    return summary


def _database_format(path):
    with closing(_readonly(path, immutable=True)) as db:
        return FORMAT if _schema_version(db) == 2 else LEGACY_FORMAT


def migrate_database(source: Path, destination: Path, *, timeout: float = 30) -> dict:
    """Copy a verified v1 snapshot, then migrate only the copy. Keep all event bytes."""
    payload = create_backup(source, destination, timeout=timeout)
    if payload["format"] != LEGACY_FORMAT:
        raise ValueError("source is already scoped; the copied backup is unchanged")
    destination = Path(destination)
    target = destination / DATABASE
    before = payload["summary"]
    (destination / MANIFEST).rename(destination / "source-manifest.json")
    with closing(sqlite3.connect(target)) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("ALTER TABLE runs ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'local'")
        db.execute("ALTER TABLE runs ADD COLUMN operation TEXT NOT NULL DEFAULT 'create_run'")
        for run_id, key in db.execute("SELECT run_id,request_key FROM runs").fetchall():
            db.execute("UPDATE runs SET request_key=? WHERE run_id=?", (scoped_key(key), run_id))
        db.commit()
    after = inspect_database(target)
    if before != after:
        raise IntegrityError("migration changed an event or checkpoint anchor")
    _sync(target)
    migrated = {
        **payload,
        "format": FORMAT,
        "sha256": file_hash(target),
        "bytes": target.stat().st_size,
        "migration": {
            "from": LEGACY_FORMAT,
            "legacy_owner": "local",
            "source_snapshot_sha256": payload["sha256"],
        },
    }
    _manifest(destination, migrated)
    return migrated


def verify_backup(directory: Path) -> dict:
    directory = Path(directory)
    payload = _read_manifest(directory)
    _check_copy(directory / DATABASE, payload)
    return payload


def restore_backup(source: Path, destination: Path) -> dict:
    source, destination = Path(source), Path(destination)
    payload = verify_backup(source)
    destination.mkdir()
    target = destination / DATABASE
    with (source / DATABASE).open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())
    # Verify the actual restored bytes, not just the source inspected earlier.
    _check_copy(target, payload)
    _manifest(destination, payload | {"restored_at": datetime.now(UTC).isoformat()})
    return payload
