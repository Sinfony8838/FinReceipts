import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from finreceipts.runtime.backup import _TABLES, inspect_database, migrate_database, verify_backup
from finreceipts.runtime.codec import canonical, digest
from finreceipts.runtime.store import RunConflict, RunNotFound, RunStore


def test_scoped_concurrent_creation_and_ownership_integrity(tmp_path):
    store = RunStore(tmp_path / "runs.db", busy_timeout=5)

    def create(_):
        return store.create("nonce", {}, {}, {}, principal_id="alice")

    with ThreadPoolExecutor(4) as workers:
        docs = list(workers.map(create, range(8)))
    assert len({d["run_id"] for d in docs}) == 1
    with pytest.raises(RunNotFound):
        store.get(docs[0]["run_id"])
    assert len(store.export(docs[0]["run_id"], principal_id="alice")["events"]) == 1
    assert store.create("nonce", {}, {}, {}, principal_id="bob")["run_id"] != docs[0]["run_id"]


def test_explicit_migration_preserves_original_and_all_export_bytes(tmp_path):
    seed = RunStore(tmp_path / "seed.db")
    doc = seed.create("nonce", {}, {}, {})
    bundle = seed.export(doc["run_id"])
    del bundle["run"]["scope"]
    event = bundle["events"][0]
    event["state_digest"] = digest(bundle["run"])
    event["hash"] = digest({k: v for k, v in event.items() if k != "hash"})
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as db:
        for sql in _TABLES.values():
            db.execute(sql)
        db.execute(
            "INSERT INTO runs VALUES (?,?,?)", (doc["run_id"], "nonce", canonical(bundle["run"]))
        )
        db.execute("INSERT INTO events VALUES (?,?,?)", (doc["run_id"], 1, canonical(event)))
    before = legacy.read_bytes()
    inspect_database(legacy)
    with pytest.raises(RunConflict, match="runtime-migrate"):
        RunStore(legacy)
    result = migrate_database(legacy, tmp_path / "migrated")
    assert result == verify_backup(tmp_path / "migrated")
    assert legacy.read_bytes() == before
    migrated = RunStore(tmp_path / "migrated/runs.sqlite3")
    assert migrated.export(doc["run_id"]) == bundle
    assert migrated.find("nonce", {}) == bundle["run"]
    with pytest.raises(RunNotFound):
        migrated.get(doc["run_id"], principal_id="alice")
    assert json.loads((tmp_path / "migrated/source-manifest.json").read_text())["format"].endswith(
        "v1"
    )
