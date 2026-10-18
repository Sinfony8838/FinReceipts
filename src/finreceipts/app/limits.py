"""Bound HTTP intake and response waiting, without claiming to kill SDK threads."""

from __future__ import annotations

import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from finreceipts.app.diagnostics import error_payload

MAX_QUERY_BYTES = 16384


def _api(scope: Scope) -> bool:
    path, root = scope.get("path", ""), scope.get("root_path", "")
    if root and (path == root or path.startswith(root + "/")):
        path = path[len(root) :]
    return scope["type"] == "http" and (path == "/api" or path.startswith("/api/"))


class RequestLimits:
    """Read at most one bounded body before JSON parsing or route dispatch."""

    def __init__(self, app: ASGIApp, *, state):
        self.app, self.state = app, state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _api(scope):
            await self.app(scope, receive, send)
            return

        async def reject(status: int, code: str):
            # A rejected body may remain unread. Do not reuse an HTTP/1 connection.
            headers = (
                {"Connection": "close"} if scope.get("http_version", "1.1").startswith("1") else {}
            )
            await JSONResponse(error_payload(Request(scope), code), status, headers)(
                scope, receive, send
            )

        if len(scope.get("query_string", b"")) > MAX_QUERY_BYTES:
            await reject(414, "query_too_large")
            return
        headers = scope.get("headers", [])
        lengths = [v for k, v in headers if k.lower() == b"content-length"]
        encodings = [v for k, v in headers if k.lower() == b"content-encoding"]
        transfers = [v for k, v in headers if k.lower() == b"transfer-encoding"]
        if len(lengths) > 1 or (lengths and transfers) or (lengths and not lengths[0].isdigit()):
            await reject(400, "invalid_request_framing")
            return
        if encodings and encodings != [b"identity"]:
            await reject(415, "unsupported_content_encoding")
            return
        limit = self.state.settings.max_request_bytes
        # Compare bounded decimal text before int conversion (including very long headers).
        length = (lengths[0].lstrip(b"0") or b"0") if lengths else None
        if length is not None and (
            len(length) > len(str(limit))
            or (len(length) == len(str(limit)) and length > str(limit).encode())
        ):
            await reject(413, "request_too_large")
            return
        expected = int(length) if length is not None else None
        body = bytearray()
        oversized = False
        try:
            async with asyncio.timeout(self.state.settings.request_read_timeout_s):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > limit:
                        oversized = True
                        break
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await reject(408, "request_body_timeout")
            return
        if oversized:
            await reject(413, "request_too_large")
            return
        if expected is not None and len(body) != expected:
            await reject(400, "invalid_request_framing")
            return
        pending = True

        async def replay() -> Message:
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class ResponseDeadline:
    """Fence late output and stop waiting; a running sync worker retains its permit."""

    def __init__(self, app: ASGIApp, *, state):
        self.app, self.state = app, state
        self._pending: set[asyncio.Task] = set()

    def _finished(self, task: asyncio.Task) -> None:
        self._pending.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve late failures without exposing provider data in logs.

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        permit = scope.get("state", {}).get("execution_permit")
        if scope["type"] != "http" or permit is None:
            await self.app(scope, receive, send)
            return
        expired = started = completed = stream = False

        async def fenced_send(message: Message) -> None:
            nonlocal started, completed, stream
            if expired:
                return
            if message["type"] == "http.response.start":
                started = True
                stream = any(
                    k.lower() == b"content-type" and v.startswith(b"text/event-stream")
                    for k, v in message.get("headers", [])
                )
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                completed = True

        task = asyncio.create_task(self.app(scope, receive, fenced_send))
        self._pending.add(task)
        task.add_done_callback(self._finished)
        try:
            done, _ = await asyncio.wait({task}, timeout=self.state.settings.response_timeout_s)
            if done:
                await task
                return
            expired = True
            permit.close()
            task.cancel()
            if completed:
                return
            payload = error_payload(Request(scope), "response_timeout")
            if not started:
                await JSONResponse(payload, status_code=504)(scope, receive, send)
            else:
                body = b""
                if stream:
                    payload = {"event": "error", **payload}
                    body = f"event: error\ndata: {json.dumps(payload)}\n\n".encode()
                await send({"type": "http.response.body", "body": body, "more_body": False})
        except asyncio.CancelledError:
            expired = True
            permit.close()
            task.cancel()
            raise
