"""FastAPI application: ask / audit with SSE agent-step streaming.

Default ``FINRECEIPTS_LLM_PROVIDER=fake`` so demos run without an API key.
Real MiniMax (or other) providers are selected only via environment variables;
compose/Docker files never embed secrets.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from finreceipts import __version__
from finreceipts.agent.graph import (
    AgentDeps,
    build_graph,
    initial_state,
    summarize_audit,
)
from finreceipts.app.access import AccessPolicy, BrowserBoundary, SecurityHeaders
from finreceipts.app.admission import (
    AdmissionControl,
    ExecutionGate,
    GuardedIterator,
    execution_work,
)
from finreceipts.app.demo_llm import demo_responder
from finreceipts.app.diagnostics import (
    APIError,
    RequestDiagnostics,
    error_payload,
    http_error,
    request_logging_config,
    validation_error,
)
from finreceipts.app.limits import RequestLimits, ResponseDeadline
from finreceipts.app.runs import run_router
from finreceipts.config import Settings
from finreceipts.evals.runner import edgar_lookup_provider
from finreceipts.llm.client import FakeLLM, make_client
from finreceipts.models import VerificationResult
from finreceipts.report.html import render_report
from finreceipts.runtime.codec import digest
from finreceipts.runtime.service import RunService
from finreceipts.runtime.store import RunStore
from finreceipts.tools.edgar import EdgarClient
from finreceipts.verify.numbers import NumericInputError
from finreceipts.verify.verifier import Verifier

Mode = Literal["baseline", "verify", "cascade"]
Feedback = Literal["flags", "receipts"]

PKG_DIR = Path(__file__).resolve().parent
STATIC_DIR = PKG_DIR / "static"
# Repo root candidates (src layout vs installed / Docker WORKDIR=/app)
_REPO_CANDIDATES = [
    PKG_DIR.parents[2],  # .../src/finreceipts/app -> repo
    Path("/app"),
    Path.cwd(),
]


def _repo_root() -> Path:
    for p in _REPO_CANDIDATES:
        if (p / "examples").is_dir():
            return p
    return Path.cwd()


def _examples_dirs() -> list[Path]:
    root = _repo_root()
    return [root / "examples"]


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    ticker: str = Field(..., min_length=1, max_length=16)
    fiscal_years: list[int] = Field(default_factory=lambda: [2023, 2024, 2025])
    mode: Mode = "verify"
    feedback: Feedback = "flags"
    model: str | None = None
    escalate_model: str | None = None
    max_revisions: int = Field(default=2, ge=0, le=5)
    as_of: date | None = None
    use_tools: bool = False


class AuditRequest(BaseModel):
    text: str = Field(..., min_length=1)
    ticker: str = Field(..., min_length=1, max_length=16)
    fiscal_years: list[int] = Field(default_factory=list)
    question: str = ""
    as_of: date | None = None
    title: str | None = None


def _audit_to_json(audit: list[VerificationResult]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in audit]


def _make_llm(settings: Settings):
    if settings.llm_provider == "fake":
        return FakeLLM(demo_responder)
    return make_client(settings)


class AppState:
    def __init__(self) -> None:
        self.settings = Settings.from_env()
        self.access_policy = AccessPolicy.from_env()
        self.edgar: EdgarClient | None = None
        self.provider = None
        self.graph = None
        self.llm = None
        self.runs = None
        self.admission = ExecutionGate(self.settings.max_inflight)

    def start(self) -> None:
        self.settings = Settings.from_env()
        self.access_policy = AccessPolicy.from_env()
        self.admission = ExecutionGate(self.settings.max_inflight)
        self.edgar = EdgarClient(self.settings)
        self.provider = edgar_lookup_provider(self.edgar)
        self.llm = _make_llm(self.settings)
        self.graph = build_graph(AgentDeps(llm=self.llm, lookup_provider=self.provider))
        self.runs = RunService(
            RunStore(Path(os.environ.get("FINRECEIPTS_RUN_DB", ".cache/runs.sqlite3"))),
            self.llm,
            self.provider,
            provider_id=digest(
                {"provider": self.settings.llm_provider, "endpoint": self.settings.llm_base_url}
            ),
        )

    def stop(self) -> None:
        if self.edgar is not None:
            self.edgar.close()
            self.edgar = None


def create_app() -> FastAPI:
    state = AppState()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.start()
        yield
        state.stop()

    app = FastAPI(
        title="FinReceipts",
        description="Verifiable finance research agent — every number comes with a receipt.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.fin = state
    app.add_middleware(ResponseDeadline, state=state)
    app.add_middleware(RequestLimits, state=state)
    app.add_middleware(AdmissionControl, state=state)
    app.add_middleware(BrowserBoundary, state=state)
    app.add_middleware(RequestDiagnostics)
    app.add_middleware(SecurityHeaders)
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.include_router(run_router(state))

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/health")
    def health() -> dict[str, Any]:
        s = state.settings
        return {
            "ok": True,
            "provider": s.llm_provider,
            "offline": s.offline,
            "small_model": s.small_model or None,
            "large_model": s.large_model or None,
            "execution": state.admission.snapshot(),
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            raise HTTPException(404, "demo UI missing")
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @app.get("/workbench", response_class=HTMLResponse)
    def workbench() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "runs.html").read_text(encoding="utf-8"))

    @app.get("/reports/{name}", response_class=HTMLResponse)
    def serve_report(name: str) -> HTMLResponse:
        if "/" in name or ".." in name or not name.endswith(".html"):
            raise HTTPException(400, "invalid report name")
        for d in _examples_dirs():
            path = d / name
            if path.is_file():
                return HTMLResponse(path.read_text(encoding="utf-8"))
        raise HTTPException(404, "report not found")

    def _resolve_models(req: AskRequest) -> tuple[str, str | None]:
        s = state.settings
        model = req.model or s.small_model or "demo"
        esc = req.escalate_model or s.large_model or None
        if req.mode == "cascade" and not esc:
            raise APIError(400, "cascade_unconfigured")
        return model, esc

    def _run_ask(req: AskRequest) -> dict[str, Any]:
        assert state.graph is not None
        model, esc = _resolve_models(req)
        init = initial_state(
            req.question,
            ticker=req.ticker.upper(),
            fiscal_years=list(req.fiscal_years),
            model=model,
            mode=req.mode,
            feedback=req.feedback,
            use_tools=req.use_tools,
            as_of=req.as_of,
            max_revisions=req.max_revisions,
            escalate_model=esc,
        )
        out = state.graph.invoke(init)
        audit: list[VerificationResult] = list(out.get("audit") or [])
        answer = out.get("answer") or ""
        html = render_report(
            title=f"FinReceipts · {req.ticker.upper()}",
            question=req.question,
            answer=answer,
            audit=audit,
            verification_mode="numeric_only",
        )
        usage = out.get("usage")
        return {
            "ticker": req.ticker.upper(),
            "audit_mode": "numeric_only",
            "verification_level": "numeric_match_only",
            "semantic_verification_passed": False,
            "question": req.question,
            "answer": answer,
            "mode": req.mode,
            "feedback": req.feedback,
            "model": out.get("model", model),
            "revisions": out.get("revisions", 0),
            "escalated": bool(out.get("escalated")),
            "attempts": out.get("attempts") or [],
            "audit_summary": summarize_audit(audit),
            "audit": _audit_to_json(audit),
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "calls": getattr(usage, "calls", 0),
            },
            "html": html,
        }

    @app.post("/api/ask")
    def ask(req: AskRequest, request: Request) -> JSONResponse:
        try:
            with execution_work(request):
                return JSONResponse(_run_ask(req))
        except APIError:
            raise
        except Exception as exc:
            raise APIError(500, "ask_failed") from exc

    @app.post("/api/ask/stream")
    def ask_stream(req: AskRequest, request: Request) -> StreamingResponse:
        """SSE: one event per agent node (answer / verify / revise / escalate), then ``done``."""

        def gen():
            assert state.graph is not None
            try:
                model, esc = _resolve_models(req)
            except HTTPException as exc:
                code = exc.code if isinstance(exc, APIError) else "invalid_request"
                payload = {"event": "error", **error_payload(request, code)}
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                return
            init = initial_state(
                req.question,
                ticker=req.ticker.upper(),
                fiscal_years=list(req.fiscal_years),
                model=model,
                mode=req.mode,
                feedback=req.feedback,
                use_tools=req.use_tools,
                as_of=req.as_of,
                max_revisions=req.max_revisions,
                escalate_model=esc,
            )
            start = {
                "event": "start",
                "ticker": req.ticker.upper(),
                "mode": req.mode,
                "feedback": req.feedback,
                "audit_mode": "numeric_only",
                "verification_level": "numeric_match_only",
                "semantic_verification_passed": False,
            }
            yield f"event: start\ndata: {json.dumps(start)}\n\n"
            final: dict[str, Any] = {}
            try:
                for update in state.graph.stream(init, stream_mode="updates"):
                    for node, delta in update.items():
                        payload: dict[str, Any] = {
                            "event": "step",
                            "node": node,
                            "audit_mode": "numeric_only",
                            "verification_level": "numeric_match_only",
                            "semantic_verification_passed": False,
                        }
                        if isinstance(delta, dict):
                            if "answer" in delta:
                                payload["answer"] = delta["answer"]
                            if "audit" in delta:
                                audit = list(delta["audit"] or [])
                                payload["audit_summary"] = summarize_audit(audit)
                                payload["audit"] = _audit_to_json(audit)
                            if "revisions" in delta:
                                payload["revisions"] = delta["revisions"]
                            if "escalated" in delta:
                                payload["escalated"] = delta["escalated"]
                            if "model" in delta:
                                payload["model"] = delta["model"]
                            if "attempts" in delta:
                                payload["attempts"] = delta["attempts"]
                            final.update({k: v for k, v in delta.items() if k != "messages"})
                        yield f"event: step\ndata: {json.dumps(payload, default=str)}\n\n"
                audit = list(final.get("audit") or [])
                answer = final.get("answer") or ""
                html = render_report(
                    title=f"FinReceipts · {req.ticker.upper()}",
                    question=req.question,
                    answer=answer,
                    audit=audit,
                    verification_mode="numeric_only",
                )
                done = {
                    "event": "done",
                    "answer": answer,
                    "revisions": final.get("revisions", 0),
                    "escalated": bool(final.get("escalated")),
                    "audit_summary": summarize_audit(audit),
                    "html": html,
                    "audit_mode": "numeric_only",
                    "verification_level": "numeric_match_only",
                    "semantic_verification_passed": False,
                }
                yield f"event: done\ndata: {json.dumps(done, default=str)}\n\n"
            except Exception:
                err = {"event": "error", **error_payload(request, "ask_failed")}
                yield f"event: error\ndata: {json.dumps(err)}\n\n"

        return StreamingResponse(
            GuardedIterator(gen(), request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Convenience GET stream for curl / EventSource demos
    @app.get("/api/ask/stream")
    def ask_stream_get(
        request: Request,
        question: Annotated[str, Query()],
        ticker: Annotated[str, Query()],
        fiscal_years: Annotated[str, Query()] = "2023,2024,2025",
        mode: Annotated[Mode, Query()] = "verify",
        feedback: Annotated[Feedback, Query()] = "flags",
        model: str | None = None,
        max_revisions: Annotated[int, Query(ge=0, le=5)] = 2,
        as_of: date | None = None,
    ) -> StreamingResponse:
        try:
            years = [int(x) for x in fiscal_years.split(",") if x.strip()]
            req = AskRequest(
                question=question,
                ticker=ticker,
                fiscal_years=years or [2023],
                mode=mode,
                feedback=feedback,
                model=model,
                max_revisions=max_revisions,
                as_of=as_of,
            )
        except ValueError as exc:
            raise APIError(422, "invalid_request") from exc
        return ask_stream(req, request)

    def _run_audit(req: AuditRequest) -> JSONResponse:
        assert state.provider is not None
        try:
            lookup = state.provider(req.ticker.upper(), req.as_of)
        except Exception as exc:
            raise APIError(503, "audit_failed") from exc
        years = set(req.fiscal_years) or None
        try:
            results = Verifier(lookup).audit_text(req.text, fiscal_years=years)
        except NumericInputError as exc:
            raise APIError(422, exc.code) from exc
        title = req.title or f"FinReceipts audit · {req.ticker.upper()}"
        html = render_report(
            title=title,
            question=req.question,
            answer=req.text,
            audit=results,
            verification_mode="text_scope",
        )
        return JSONResponse(
            {
                "ticker": req.ticker.upper(),
                "audit_mode": "text_scope",
                "verification_level": "bounded_annual_clause",
                "all_numbers_supported": bool(results) and all(r.ok for r in results),
                "empty_extraction": not results,
                "audit_summary": summarize_audit(results),
                "audit": _audit_to_json(results),
                "html": html,
            }
        )

    @app.post("/api/audit")
    def audit(req: AuditRequest, request: Request) -> JSONResponse:
        with execution_work(request):
            return _run_audit(req)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("FINRECEIPTS_HOST", "127.0.0.1")
    port = int(os.environ.get("FINRECEIPTS_PORT", "8000"))
    uvicorn.run(
        "finreceipts.app.main:app",
        host=host,
        port=port,
        access_log=False,  # Raw access logs contain GET question/query values.
        proxy_headers=False,  # Do not trust client-supplied forwarded scheme/IP headers.
        log_config=request_logging_config(),
        reload=os.environ.get("FINRECEIPTS_RELOAD") in {"1", "true", "yes"},
    )


if __name__ == "__main__":  # pragma: no cover
    main()
