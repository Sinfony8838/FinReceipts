"""Bounded request diagnostics: no URL, payload, headers or exception strings."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from copy import deepcopy
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOGGER = logging.getLogger("finreceipts.requests")
DETAILS = {
    "internal_error": "request failed; use the request ID to locate diagnostics",
    "ask_failed": "answer generation failed; no raw provider error is exposed",
    "audit_failed": "audit failed; no raw provider error is exposed",
    "audit_input_limit": (
        "audit accepts at most 65536 characters and 256 numeric mentions; split the text"
    ),
    "numeric_out_of_range": (
        "a numeric quantity exceeds the supported finite range; check its digits and scale"
    ),
    "invalid_request": "request validation failed; check field types and limits",
    "evidence_unavailable": "no financial evidence for the requested scope",
    "cascade_unconfigured": "cascade requires a configured escalation model",
    "run_not_found": "run not found",
    "history_cursor_expired": "stored task positions changed; refresh the task list",
    "run_conflict": "revision or run state conflict; refresh before continuing",
    "integrity_failed": "run integrity check failed",
    "storage_busy": "run storage is busy; refresh state before retrying with the current revision",
    "capacity_exhausted": "execution capacity is full; wait and refresh state before retrying",
    "request_too_large": "request body exceeds the configured byte limit",
    "query_too_large": "request query exceeds the 16384 byte limit",
    "request_body_timeout": "request body was not received within the configured time limit",
    "invalid_request_framing": "invalid or inconsistent request body length",
    "unsupported_content_encoding": "compressed request bodies are not supported",
    "response_timeout": (
        "response wait expired; work may continue; refresh run state before retrying"
    ),
    "untrusted_host": "request host is not configured for this service",
    "cross_origin_denied": "browser requests must come from the same origin as this service",
    "run_failed": "run operation failed; no raw provider error is exposed",
    "http_error": "request could not be completed",
    "stream_failed": "response stream failed",
}


class APIError(HTTPException):
    def __init__(self, status_code: int, code: str, *, headers: dict | None = None):
        self.code = code
        super().__init__(status_code, DETAILS[code], headers=headers)


def error_payload(request: Request, code: str) -> dict:
    context = request.scope["state"]["diagnostics"]
    context["error_code"] = code
    return {"detail": DETAILS[code], "code": code, "request_id": context["request_id"]}


async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    code = exc.code if isinstance(exc, APIError) else "http_error"
    content = error_payload(request, code)
    # Application-authored 4xx messages are useful. Never expose unexpected 5xx
    # detail, exception text, or validation input/context.
    if not isinstance(exc, APIError) and exc.status_code < 500:
        content["detail"] = exc.detail
    return JSONResponse(content, status_code=exc.status_code, headers=exc.headers)


async def validation_error(request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(error_payload(request, "invalid_request"), status_code=422)


class RequestDiagnostics:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        context = {"request_id": uuid.uuid4().hex, "error_code": None}
        scope.setdefault("state", {})["diagnostics"] = context
        started = time.perf_counter()
        status, response_started, completed, disconnected = 500, False, False, False

        async def observed_receive() -> Message:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def observed_send(message: Message) -> None:
            nonlocal status, response_started, completed, disconnected
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", context["request_id"].encode("ascii")))
                message = {**message, "headers": headers}
                response_started = True
            try:
                await send(message)
            except OSError:
                disconnected = True
                raise
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                completed = True

        try:
            await self.app(scope, observed_receive, observed_send)
        except asyncio.CancelledError:
            disconnected = True
            raise
        except Exception:
            # Keep raw exception text/traceback out of the ASGI server logs as
            # well as the response. Do not send a second HTTP start for a stream.
            if not disconnected:
                code = "stream_failed" if response_started else "internal_error"
                content = error_payload(Request(scope), code)
                if not response_started:
                    await JSONResponse(content, status_code=500)(
                        scope, observed_receive, observed_send
                    )
                elif not completed:
                    await observed_send(
                        {"type": "http.response.body", "body": b"", "more_body": False}
                    )
        finally:
            route = getattr(scope.get("route"), "path", None) or scope["state"].get(
                "admission_route", "unmatched"
            )
            method = scope.get("method")
            if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"}:
                method = "OTHER"
            outcome = "ok"
            if disconnected and not completed:
                outcome = "disconnected"
            elif context["error_code"] or status >= 400:
                outcome = "error"
            record = {
                "event": "http_request",
                "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "request_id": context["request_id"],
                "method": method,
                "route": route,
                "status": status,
                "outcome": outcome,
                "error_code": context["error_code"],
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
            LOGGER.info(json.dumps(record, separators=(",", ":")))


def request_logging_config() -> dict:
    """Pass configuration to Uvicorn so spawned reload workers receive it too."""
    from uvicorn.config import LOGGING_CONFIG

    config = deepcopy(LOGGING_CONFIG)
    config["formatters"]["request_json"] = {"format": "%(message)s"}
    config["handlers"]["request_json"] = {
        "class": "logging.StreamHandler",
        "formatter": "request_json",
        "stream": "ext://sys.stderr",
    }
    config["loggers"]["finreceipts.requests"] = {
        "handlers": ["request_json"],
        "level": "INFO",
        "propagate": False,
    }
    return config
