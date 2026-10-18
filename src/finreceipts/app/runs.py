"""Bounded local run API: idempotent create, CAS steps, replayable SSE, export."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from finreceipts.app.admission import execution_work
from finreceipts.app.diagnostics import APIError
from finreceipts.runtime.codec import canonical
from finreceipts.runtime.service import EvidenceUnavailable, counter_audit
from finreceipts.runtime.store import (
    HistoryCursorExpired,
    HistoryCursorInvalid,
    IntegrityError,
    RunConflict,
    RunNotFound,
    StoreBusy,
)


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=12000)
    ticker: str = Field(pattern=r"^[A-Za-z0-9.\-]{1,16}$")
    fiscal_years: list[int] = Field(default_factory=lambda: [2023], min_length=1, max_length=10)
    mode: Literal["baseline", "verify", "cascade"] = "verify"
    feedback: Literal["flags", "receipts"] = "receipts"
    use_tools: bool = False
    as_of: date | None = None
    max_revisions: int = Field(default=2, ge=0, le=5)
    max_dispatches: int = Field(default=6, ge=1, le=50)
    max_tool_calls: int = Field(default=12, ge=0, le=100)

    @field_validator("fiscal_years")
    @classmethod
    def valid_years(cls, value):
        if any(y < 1990 or y > 2100 for y in value):
            raise ValueError("fiscal years must be in [1990, 2100]")
        return sorted(set(value))


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class Resume(Revision):
    acknowledge_possible_duplicate: Literal[True]


def run_router(state) -> APIRouter:
    router = APIRouter(prefix="/api/runs", tags=["durable runs"])

    def call(fn, *args):
        try:
            return fn(*args)
        except RunNotFound as exc:
            raise APIError(404, "run_not_found") from exc
        except HistoryCursorInvalid as exc:
            raise APIError(422, "invalid_request") from exc
        except HistoryCursorExpired as exc:
            raise APIError(409, "history_cursor_expired") from exc
        except RunConflict as exc:
            raise APIError(409, "run_conflict") from exc
        except IntegrityError as exc:
            raise APIError(409, "integrity_failed") from exc
        except StoreBusy as exc:
            raise APIError(503, "storage_busy", headers={"Retry-After": "1"}) from exc
        except EvidenceUnavailable as exc:
            raise APIError(422, "evidence_unavailable") from exc
        except Exception as exc:
            raise APIError(503, "run_failed") from exc

    @router.post("", status_code=201)
    def create_run(
        req: CreateRun,
        request: Request,
        idempotency_key: Annotated[
            str, Header(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_\-]+$")
        ],
    ):
        payload = req.model_dump()
        payload["ticker"] = req.ticker.upper()
        payload["model"] = state.settings.small_model or "demo"
        payload["escalate_model"] = state.settings.large_model or None
        if req.mode == "cascade" and not payload["escalate_model"]:
            raise APIError(422, "cascade_unconfigured")
        with execution_work(request):
            return call(state.runs.create, idempotency_key, payload)

    @router.get("")
    def list_runs(
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Query(max_length=256)] = None,
    ):
        return call(state.runs.store.list_runs, limit, cursor)

    @router.get("/{run_id}")
    def get_run(run_id: str):
        return call(state.runs.store.get, run_id)

    @router.post("/{run_id}/advance")
    def advance(run_id: str, req: Revision, request: Request):
        with execution_work(request):
            return call(state.runs.advance, run_id, req.expected_revision)

    @router.post("/{run_id}/cancel")
    def cancel(run_id: str, req: Revision):
        return call(state.runs.store.cancel, run_id, req.expected_revision)

    @router.post("/{run_id}/resume")
    def resume(run_id: str, req: Resume):
        return call(state.runs.store.resume, run_id, req.expected_revision)

    @router.get("/{run_id}/export")
    def export(run_id: str):
        return call(state.runs.store.export, run_id)

    @router.get("/{run_id}/counter-audit")
    def replay(run_id: str, request: Request):
        with execution_work(request):
            return call(counter_audit, call(state.runs.store.get, run_id))

    @router.get("/{run_id}/events")
    def events(
        run_id: str,
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[str | None, Header()] = None,
    ):
        # A finite catch-up stream. Reconnect from the cursor; reading never starts work.
        cursor = after
        if last_event_id is not None:
            try:
                cursor = int(last_event_id)
            except ValueError as exc:
                raise HTTPException(422, "Last-Event-ID must be a non-negative integer") from exc
            if cursor < 0:
                raise HTTPException(422, "Last-Event-ID must be a non-negative integer")
        bundle = call(state.runs.store.export, run_id)
        if cursor > bundle["run"]["revision"]:
            raise HTTPException(409, "event cursor is ahead of this run")

        def stream():
            for event in bundle["events"]:
                if event["seq"] > cursor:
                    yield (
                        f"id: {event['seq']}\nevent: {event['kind']}\ndata: {canonical(event)}\n\n"
                    )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
