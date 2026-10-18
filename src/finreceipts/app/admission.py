"""Process-local, fail-fast execution admission with worker lifetime tracking."""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from finreceipts.app.diagnostics import error_payload


class RequestClosed(RuntimeError):
    """Work must not start after its owning request has ended."""


class ExecutionGate:
    def __init__(self, limit: int):
        if type(limit) is not int or limit < 1:
            raise ValueError("execution limit must be a positive integer")
        self.limit = limit
        self._lock = Lock()
        self._active = self._admitted = self._rejected = self._peak = 0

    def acquire(self) -> Permit | None:
        with self._lock:
            if self._active >= self.limit:
                self._rejected += 1
                return None
            self._active += 1
            self._admitted += 1
            self._peak = max(self._peak, self._active)
        return Permit(self)

    def _release(self) -> None:
        with self._lock:
            self._active -= 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "limit": self.limit,
                "active": self._active,
                "peak_active": self._peak,
                "admitted": self._admitted,
                "rejected": self._rejected,
            }


class Permit:
    def __init__(self, gate: ExecutionGate):
        self._gate, self._lock = gate, Lock()
        self._closed = self._released = False
        self._workers = 0

    def _release_if_finished(self) -> None:
        # Called under our lock; the gate never acquires a permit's lock.
        if self._closed and not self._workers and not self._released:
            self._released = True
            self._gate._release()

    @contextmanager
    def work(self):
        with self._lock:
            if self._closed:
                raise RequestClosed("request has ended; no new work may start")
            self._workers += 1
        try:
            yield
        finally:
            with self._lock:
                self._workers -= 1
                self._release_if_finished()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._release_if_finished()


def execution_work(request: Request):
    return request.scope["state"]["execution_permit"].work()


class GuardedIterator:
    """Count the actual blocking next(), not the idle time between SSE chunks."""

    def __init__(self, content, request: Request):
        self.iterator = iter(content)
        self.permit = request.scope["state"]["execution_permit"]

    def __iter__(self):
        return self

    def __next__(self):
        with self.permit.work():
            return next(self.iterator)


def protected_route(method: str, path: str) -> str | None:
    path = path.rstrip("/")
    if method == "POST" and path in {"/api/ask", "/api/ask/stream", "/api/audit", "/api/runs"}:
        return path
    if method == "GET" and path == "/api/ask/stream":
        return path
    if path.startswith("/api/runs/") and path.count("/") == 4:
        if method == "POST" and path.endswith("/advance"):
            return "/api/runs/{run_id}/advance"
        if method == "GET" and path.endswith("/counter-audit"):
            return "/api/runs/{run_id}/counter-audit"
    return None


class AdmissionControl:
    def __init__(self, app: ASGIApp, *, state):
        self.app, self.state = app, state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path, root = scope.get("path", ""), scope.get("root_path", "")
        if root and (path == root or path.startswith(root + "/")):
            path = path[len(root) :]
        route = protected_route(scope.get("method", ""), path)
        if scope["type"] != "http" or route is None:
            await self.app(scope, receive, send)
            return
        scope["state"]["admission_route"] = route
        permit = self.state.admission.acquire()
        if permit is None:
            payload = error_payload(Request(scope), "capacity_exhausted")
            await JSONResponse(payload, status_code=503, headers={"Retry-After": "1"})(
                scope, receive, send
            )
            return
        scope["state"]["execution_permit"] = permit
        try:
            await self.app(scope, receive, send)
        finally:
            # Cancelled HTTP tasks may leave synchronous SDK calls running.
            # The last actual worker releases the slot after it exits.
            permit.close()
