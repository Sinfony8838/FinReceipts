"""Reproducible offline restore drill; creates only a new output directory."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finreceipts.config import Settings  # noqa: E402
from finreceipts.llm.client import FakeLLM  # noqa: E402
from finreceipts.runtime.backup import (  # noqa: E402
    DATABASE,
    MANIFEST,
    create_backup,
    file_hash,
    inspect_database,
    restore_backup,
    verify_backup,
)
from finreceipts.runtime.codec import implementation_id  # noqa: E402
from finreceipts.runtime.service import RunService, counter_audit  # noqa: E402
from finreceipts.runtime.store import IntegrityError, RunConflict, RunStore  # noqa: E402
from finreceipts.tools.edgar import EdgarClient  # noqa: E402
from finreceipts.tools.http_cache import JsonCache  # noqa: E402
from finreceipts.xbrl.metrics import lookup_from_companyfacts  # noqa: E402


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def drill(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    store = RunStore(out / "source.sqlite3", clock=lambda: 100)
    model = FakeLLM(lambda *_: "Apple revenue in 2023 was $383.3 billion.")
    request = dict(
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
    )
    with EdgarClient(
        Settings(), cache=JsonCache(ROOT / "tests/fixtures/sec"), mode="replay"
    ) as sec:

        def provider(ticker, as_of):
            return lookup_from_companyfacts(
                sec.companyfacts(sec.ticker_to_cik(ticker)), as_of=as_of
            )

        service = RunService(store, model, provider)
        with store.connection() as keeper:
            keeper.execute("BEGIN")
            keeper.execute("SELECT count(*) FROM runs").fetchone()
            answer = service.create("answer", request)
            answer = service.advance(answer["run_id"], answer["revision"])
            running = service.create("unknown-external-call", request)
            running, token = store.claim(running["run_id"], running["revision"])
            running = store.reserve(running["run_id"], token, "dispatches", 6)
            cancelled = service.create("cancelled", request)
            cancelled = store.cancel(cancelled["run_id"], cancelled["revision"])
            originals = {
                d["run_id"]: store.export(d["run_id"]) for d in (answer, running, cancelled)
            }
            save(out / "original-exports.json", originals)
            shutil.copyfile(store.path, out / "unsafe-main-only.sqlite3")
            negative = inspect_database(out / "unsafe-main-only.sqlite3")
            assert negative["run_count"] == 0
            started = time.perf_counter()
            manifest = create_backup(store.path, out / "backup")
            backup_seconds = time.perf_counter() - started
        started = time.perf_counter()
        restore_backup(out / "backup", out / "restored")
        restore_seconds = time.perf_counter() - started
        assert verify_backup(out / "restored")["sha256"] == manifest["sha256"]

        def forbidden(*_):
            raise AssertionError("restore must not contact evidence providers or the model")

        restored = RunStore(out / "restored" / DATABASE, clock=lambda: 101)
        restarted = RunService(restored, FakeLLM(forbidden), forbidden)
        exports = {run_id: restored.export(run_id) for run_id in originals}
        assert exports == originals
        save(out / "restored-exports.json", exports)
        completed = restarted.advance(answer["run_id"], answer["revision"])
        assert completed["status"] == "completed" and completed["dispatches"] == 1
        assert counter_audit(completed)["bounded_gate_passed"]
        try:
            restored.resume(running["run_id"], running["revision"])
        except RunConflict:
            pass
        else:
            raise AssertionError("unexpired running lease must block resume")
        restored.clock = lambda: 401
        resumed = restored.resume(running["run_id"], running["revision"])
        assert resumed["dispatches"] == 1
        assert len(model.calls) == 1 and len(restarted.llm.calls) == 0
        assert {run_id: store.export(run_id) for run_id in originals} == originals
        try:
            restore_backup(out / "backup", out / "restored")
        except FileExistsError:
            pass
        else:
            raise AssertionError("restore must reject existing destinations")
        shutil.copytree(out / "backup", out / "corrupted-backup")
        with (out / "corrupted-backup" / DATABASE).open("ab") as stream:
            stream.write(b"injected corruption")
        try:
            restore_backup(out / "corrupted-backup", out / "must-not-exist")
        except IntegrityError:
            pass
        else:
            raise AssertionError("corruption must block restore")
        assert not (out / "must-not-exist").exists()

    result = {
        "status": "PASS",
        "implementation_id": implementation_id(),
        "script_sha256": file_hash(Path(__file__)),
        "model": "FakeLLM; no network calls",
        "runs": manifest["summary"]["run_count"],
        "events": manifest["summary"]["event_count"],
        "raw_main_copy_runs": negative["run_count"],
        "restored_exports_identical": True,
        "original_exports_unchanged": True,
        "restored_model_calls": 0,
        "reserved_dispatches_after_resume": resumed["dispatches"],
        "existing_destination_rejected": True,
        "corrupt_backup_rejected": True,
        "backup_seconds": round(backup_seconds, 6),
        "restore_seconds": round(restore_seconds, 6),
        "timing_scope": "tiny offline fixture; not production RTO/RPO evidence",
        "backup_manifest_sha256": file_hash(out / "backup" / MANIFEST),
    }
    save(out / "summary.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="new output directory")
    print(json.dumps(drill(parser.parse_args().out), ensure_ascii=False, indent=2))
