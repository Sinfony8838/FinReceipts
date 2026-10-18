"""SQLite state + event commits, revision CAS and expiring worker fencing.

The hash chain detects accidental edits against an exported anchor; it is not
a signature or protection from an administrator who can rewrite the database.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from finreceipts.runtime.codec import canonical, digest

WORKFLOW_VERSION = "evidence-runtime-v1"
TERMINAL = {"completed", "budget_exhausted", "cancelled", "failed"}
PERMANENT_FAILURES = frozenset({"audit_input_limit", "numeric_out_of_range"})


class RunConflict(ValueError):
    """A stale revision, duplicate request or active worker prevents this action."""


class RunNotFound(KeyError):
    """The requested run does not exist, distinct from a provider/schema KeyError."""


class IntegrityError(ValueError):
    """A checkpoint/evidence/event no longer matches its content digest."""


class StoreBusy(RuntimeError):
    """SQLite contention exceeded its bounded wait; reconcile state before retry."""


class HistoryCursorInvalid(ValueError):
    """Pagination input is malformed; it is never used as SQL text."""


class HistoryCursorExpired(RunConflict):
    """The database no longer has the cursor's row identities; restart listing."""


def _history_cursor(anchor, last) -> str:
    # Hash row identities so even a damaged textual run ID cannot poison paging.
    points = [[row, digest(run_id)] for row, run_id in (anchor, last)]
    return base64.urlsafe_b64encode(canonical(points).encode()).decode()


def _read_history_cursor(cursor: str):
    try:
        if not isinstance(cursor, str) or len(cursor) > 256:
            raise ValueError
        decoded = json.loads(
            base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True)
        )
        if not isinstance(decoded, list) or len(decoded) != 2:
            raise ValueError
        for item in decoded:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or type(item[0]) is not int
                or not 1 <= item[0] <= 2**63 - 1
                or not isinstance(item[1], str)
                or not re.fullmatch(r"[a-f0-9]{64}", item[1])
            ):
                raise ValueError
        if decoded[1][0] > decoded[0][0]:
            raise ValueError
        return decoded
    except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
        raise HistoryCursorInvalid("invalid history cursor") from exc


def verify_export(bundle: dict) -> None:
    doc, events = bundle["run"], bundle["events"]
    previous = "0" * 64
    for seq, event in enumerate(events, 1):
        body = {k: v for k, v in event.items() if k != "hash"}
        if (
            event["seq"] != seq
            or event["previous_hash"] != previous
            or event["run_id"] != doc["run_id"]
            or digest(body) != event["hash"]
        ):
            raise IntegrityError("event chain mismatch")
        previous = event["hash"]
    if not events or events[-1]["state_digest"] != digest(doc):
        raise IntegrityError("checkpoint digest mismatch")
    if events[-1]["seq"] != doc["revision"]:
        raise IntegrityError("checkpoint revision mismatch")
    if digest(doc["evidence"]) != doc["evidence_digest"]:
        raise IntegrityError("evidence digest mismatch")
    if digest(doc["request"]) != doc["request_digest"]:
        raise IntegrityError("request digest mismatch")


class RunStore:
    def __init__(
        self, path: Path, *, clock=time.time, lease_seconds: float = 300, busy_timeout: float = 1
    ):
        if not math.isfinite(busy_timeout) or busy_timeout < 0:
            raise ValueError("busy_timeout must be finite and non-negative")
        self.path, self.clock, self.lease_seconds = Path(path), clock, lease_seconds
        self.busy_timeout = busy_timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL, seq INTEGER NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY (run_id, seq)
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=self.busy_timeout, isolation_level=None)
        try:
            yield db
        except sqlite3.OperationalError as exc:
            # Extended BUSY codes have the same low byte. Do not classify disk
            # errors, corruption or SQL defects as transient write contention.
            if getattr(exc, "sqlite_errorcode", 0) & 0xFF == sqlite3.SQLITE_BUSY:
                raise StoreBusy("run storage is busy; refresh state before retrying") from exc
            raise
        finally:
            db.close()

    def _load(self, db, run_id):
        row = db.execute("SELECT document FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFound(run_id)
        events = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT document FROM events WHERE run_id=? ORDER BY seq", (run_id,)
            )
        ]
        bundle = {"run": json.loads(row[0]), "events": events}
        verify_export(bundle)
        if bundle["run"]["workflow_version"] != WORKFLOW_VERSION:
            raise RunConflict("workflow version mismatch; migration is required")
        return bundle

    def export(self, run_id: str) -> dict:
        with self.connection() as db:
            db.execute("BEGIN")
            return self._load(db, run_id)

    def get(self, run_id: str) -> dict:
        return self.export(run_id)["run"]

    def list_runs(self, limit: int = 20, cursor: str | None = None) -> dict:
        """Bounded metadata, newest inserted first; new inserts cannot shift later pages.

        No schema migration or second metadata index. Cursors bind internal row positions
        to stable run IDs, so deletion/repacking of their boundary rows requires refresh.
        Each page reads one SQLite snapshot; status can change between page requests.
        """
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("history limit must be an integer in [1, 50]")
        decoded = _read_history_cursor(cursor) if cursor is not None else None
        with self.connection() as db:
            db.execute("BEGIN")
            if decoded:
                matched = []
                for position, identity in decoded:
                    row = db.execute(
                        "SELECT run_id FROM runs WHERE rowid=?", (position,)
                    ).fetchone()
                    if row is None or digest(row[0]) != identity:
                        raise HistoryCursorExpired("history changed; refresh the list")
                    matched.append((position, row[0]))
                anchor, last = matched
                bound = last[0] - 1
            else:
                anchor = db.execute(
                    "SELECT rowid, run_id FROM runs ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                if anchor is None:
                    return {"items": [], "next_cursor": None}
                bound = anchor[0]
            rows = db.execute(
                "SELECT rowid, run_id FROM runs WHERE rowid<=? ORDER BY rowid DESC LIMIT ?",
                (bound, limit + 1),
            ).fetchall()
            items = []
            for _, run_id in rows[:limit]:
                try:
                    bundle = self._load(db, run_id)
                    doc = bundle["run"]
                    if doc["run_id"] != run_id:
                        raise IntegrityError("run row identity mismatch")
                    request = doc["request"]
                    items.append(
                        {
                            "run_id": run_id,
                            "availability": "available",
                            "revision": doc["revision"],
                            "question_preview": request["question"][:160],
                            "question_truncated": len(request["question"]) > 160,
                            "ticker": request["ticker"],
                            "fiscal_years": request["fiscal_years"],
                            "status": doc["status"],
                            "dispatches": doc["dispatches"],
                            "max_dispatches": request["max_dispatches"],
                            "created_at": bundle["events"][0]["time"],
                            "updated_at": bundle["events"][-1]["time"],
                        }
                    )
                except RunConflict:
                    items.append({"run_id": run_id, "availability": "workflow_incompatible"})
                except (IntegrityError, ValueError, KeyError, TypeError):
                    # Never show unverified question/state from a damaged record.
                    items.append({"run_id": run_id, "availability": "integrity_failed"})
            return {
                "items": items,
                "next_cursor": _history_cursor(anchor, rows[limit - 1])
                if len(rows) > limit
                else None,
            }

    def find(self, key: str, request: dict) -> dict | None:
        with self.connection() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT run_id FROM runs WHERE request_key=?", (key,)).fetchone()
            if row is None:
                return None
            doc = self._load(db, row[0])["run"]
            if doc["request_digest"] != digest(request):
                raise RunConflict("idempotency key already used for a different request")
            return doc

    def _save(self, db, doc, events, kind, payload):
        doc["revision"] += 1
        event = {
            "run_id": doc["run_id"],
            "seq": doc["revision"],
            "kind": kind,
            "time": self.clock(),
            "payload": payload,
            "state_digest": digest(doc),
            "previous_hash": events[-1]["hash"] if events else "0" * 64,
        }
        event["hash"] = digest(event)
        db.execute("UPDATE runs SET document=? WHERE run_id=?", (canonical(doc), doc["run_id"]))
        db.execute(
            "INSERT INTO events VALUES (?,?,?)", (doc["run_id"], event["seq"], canonical(event))
        )

    def create(self, key: str, request: dict, evidence: dict, state: dict) -> dict:
        doc = {
            "run_id": uuid.uuid4().hex,
            "revision": 0,
            "workflow_version": WORKFLOW_VERSION,
            "request": request,
            "request_digest": digest(request),
            "evidence": evidence,
            "evidence_digest": digest(evidence),
            "state": state,
            "status": "ready",
            "next_node": "answer",
            "lease": None,
            "dispatches": 0,
            "tool_calls": 0,
            "stop_reason": None,
            "quality": None,
        }
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT run_id FROM runs WHERE request_key=?", (key,)).fetchone()
            if row:
                existing = self._load(db, row[0])["run"]
                if existing["request_digest"] != digest(request):
                    raise RunConflict("idempotency key already used for a different request")
                return existing
            db.execute("INSERT INTO runs VALUES (?,?,?)", (doc["run_id"], key, canonical(doc)))
            self._save(db, doc, [], "created", {"evidence_digest": doc["evidence_digest"]})
            db.commit()
        return doc

    def edit(self, run_id, kind, mutate):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            bundle = self._load(db, run_id)
            doc = bundle["run"]
            payload = mutate(doc)
            self._save(db, doc, bundle["events"], kind, payload)
            db.commit()
            return doc

    @staticmethod
    def check_revision(doc, revision):
        if doc["revision"] != revision:
            raise RunConflict("stale revision; refresh the run before changing it")

    def claim(self, run_id, revision):
        token = uuid.uuid4().hex

        def change(doc):
            self.check_revision(doc, revision)
            if doc["status"] != "ready":
                raise RunConflict("run is not ready; explicitly resume interrupted work")
            doc["status"] = "running"
            doc["lease"] = {"token": token, "expires": self.clock() + self.lease_seconds}
            return {"node": doc["next_node"]}

        return self.edit(run_id, "step_started", change), token

    def check_lease(self, doc, token):
        lease = doc["lease"]
        if (
            doc["status"] != "running"
            or not lease
            or lease["token"] != token
            or lease["expires"] <= self.clock()
        ):
            raise RunConflict("worker lease expired or was revoked; result cannot be committed")

    def reserve(self, run_id, token, counter, maximum):
        def change(doc):
            self.check_lease(doc, token)
            if doc[counter] >= maximum:
                raise BudgetExceeded(counter)
            doc[counter] += 1
            return {"counter": counter, "reserved": doc[counter], "limit": maximum}

        return self.edit(run_id, "budget_reserved", change)

    def finish(self, run_id, token, state, next_step, quality):
        def change(doc):
            self.check_lease(doc, token)
            node = doc["next_node"]
            doc.update(state=state, next_node=next_step, lease=None, quality=quality)
            doc["status"] = "completed" if next_step == "__end__" else "ready"
            doc["stop_reason"] = quality["reason"] if doc["status"] == "completed" else None
            return {
                "node": node,
                "next_node": next_step,
                "quality": quality,
                "usage": state["usage"],
                "attempts": len(state["attempts"]),
            }

        return self.edit(run_id, "step_completed", change)

    def fail(self, run_id, token, *, budget=False, permanent_reason: str | None = None):
        if permanent_reason is not None and (budget or permanent_reason not in PERMANENT_FAILURES):
            raise ValueError("invalid permanent failure classification")
        if permanent_reason:
            status, reason = "failed", permanent_reason
        elif budget:
            status, reason = "budget_exhausted", "dispatch_or_tool_budget"
        else:
            status, reason = "interrupted", "step_failed"

        def change(doc):
            self.check_lease(doc, token)
            doc.update(status=status, lease=None, stop_reason=reason)
            if permanent_reason:
                doc["quality"] = {
                    "numeric_gate_passed": False,
                    "whole_answer_verified": False,
                    "reason": permanent_reason,
                }
            return {
                "node": doc["next_node"],
                "reason": doc["stop_reason"],
                "usage_may_be_incomplete": True,
            }

        return self.edit(run_id, "step_stopped", change)

    def cancel(self, run_id, revision):
        def change(doc):
            self.check_revision(doc, revision)
            if doc["status"] in TERMINAL:
                raise RunConflict("run is already terminal")
            doc.update(status="cancelled", lease=None, stop_reason="user_cancelled")
            return {"reason": "user_cancelled"}

        return self.edit(run_id, "cancelled", change)

    def resume(self, run_id, revision):
        def change(doc):
            self.check_revision(doc, revision)
            expired = doc["status"] == "running" and doc["lease"]["expires"] <= self.clock()
            if doc["status"] != "interrupted" and not expired:
                raise RunConflict("only interrupted or expired work can be resumed")
            doc.update(status="ready", lease=None, stop_reason=None)
            return {"node": doc["next_node"], "may_repeat_uncommitted_call": True}

        return self.edit(run_id, "resumed", change)


class BudgetExceeded(RuntimeError):
    """The persisted dispatch/tool budget has been exhausted."""
