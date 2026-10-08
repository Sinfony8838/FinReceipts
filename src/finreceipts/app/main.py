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

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from finreceipts.agent.graph import (
    AgentDeps,
    build_graph,
    initial_state,
    summarize_audit,
)
from finreceipts.app.demo_llm import demo_responder
from finreceipts.config import Settings
from finreceipts.evals.runner import edgar_lookup_provider
from finreceipts.llm.client import FakeLLM, make_client
from finreceipts.models import VerificationResult
from finreceipts.report.html import render_report
from finreceipts.tools.edgar import EdgarClient
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
        self.edgar: EdgarClient | None = None
        self.provider = None
        self.graph = None
        self.llm = None

    def start(self) -> None:
        self.settings = Settings.from_env()
        self.edgar = EdgarClient(self.settings)
        self.provider = edgar_lookup_provider(self.edgar)
        self.llm = _make_llm(self.settings)
        self.graph = build_graph(AgentDeps(llm=self.llm, lookup_provider=self.provider))

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
        version="0.0.1",
        lifespan=lifespan,
    )
    app.state.fin = state

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
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            raise HTTPException(404, "demo UI missing")
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @app.get("/reports/{name}", response_class=HTMLResponse)
    def serve_report(name: str) -> HTMLResponse:
        if "/" in name or ".." in name or not name.endswith(".html"):
            raise HTTPException(400, "invalid report name")
        for d in _examples_dirs():
            path = d / name
            if path.is_file():
                return HTMLResponse(path.read_text(encoding="utf-8"))
        raise HTTPException(404, f"report not found: {name}")

    def _resolve_models(req: AskRequest) -> tuple[str, str | None]:
        s = state.settings
        model = req.model or s.small_model or "demo"
        esc = req.escalate_model or s.large_model or None
        if req.mode == "cascade" and not esc:
            raise HTTPException(
                400,
                "cascade mode needs escalate_model or FINRECEIPTS_LARGE_MODEL",
            )
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
        )
        usage = out.get("usage")
        return {
            "ticker": req.ticker.upper(),
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
    def ask(req: AskRequest) -> JSONResponse:
        try:
            return JSONResponse(_run_ask(req))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, str(exc)[:500]) from exc

    @app.post("/api/ask/stream")
    def ask_stream(req: AskRequest) -> StreamingResponse:
        """SSE: one event per agent node (answer / verify / revise / escalate), then ``done``."""

        def gen():
            assert state.graph is not None
            try:
                model, esc = _resolve_models(req)
            except HTTPException as exc:
                payload = {"event": "error", "detail": exc.detail}
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
            }
            yield f"event: start\ndata: {json.dumps(start)}\n\n"
            final: dict[str, Any] = {}
            try:
                for update in state.graph.stream(init, stream_mode="updates"):
                    for node, delta in update.items():
                        payload: dict[str, Any] = {"event": "step", "node": node}
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
                )
                done = {
                    "event": "done",
                    "answer": answer,
                    "revisions": final.get("revisions", 0),
                    "escalated": bool(final.get("escalated")),
                    "audit_summary": summarize_audit(audit),
                    "html": html,
                }
                yield f"event: done\ndata: {json.dumps(done, default=str)}\n\n"
            except Exception as exc:
                err = {"event": "error", "detail": str(exc)[:500]}
                yield f"event: error\ndata: {json.dumps(err)}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Convenience GET stream for curl / EventSource demos
    @app.get("/api/ask/stream")
    def ask_stream_get(
        question: Annotated[str, Query()],
        ticker: Annotated[str, Query()],
        fiscal_years: Annotated[str, Query()] = "2023,2024,2025",
        mode: Annotated[Mode, Query()] = "verify",
        feedback: Annotated[Feedback, Query()] = "flags",
        model: str | None = None,
        max_revisions: Annotated[int, Query(ge=0, le=5)] = 2,
        as_of: date | None = None,
    ) -> StreamingResponse:
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
        return ask_stream(req)

    @app.post("/api/audit")
    def audit(req: AuditRequest) -> JSONResponse:
        assert state.provider is not None
        lookup = state.provider(req.ticker.upper(), req.as_of)
        years = set(req.fiscal_years) or None
        results = Verifier(lookup).audit_text(req.text, fiscal_years=years)
        title = req.title or f"FinReceipts audit · {req.ticker.upper()}"
        html = render_report(
            title=title,
            question=req.question,
            answer=req.text,
            audit=results,
        )
        return JSONResponse(
            {
                "ticker": req.ticker.upper(),
                "audit_summary": summarize_audit(results),
                "audit": _audit_to_json(results),
                "html": html,
            }
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("FINRECEIPTS_HOST", "0.0.0.0")
    port = int(os.environ.get("FINRECEIPTS_PORT", "8000"))
    uvicorn.run(
        "finreceipts.app.main:app",
        host=host,
        port=port,
        reload=os.environ.get("FINRECEIPTS_RELOAD") in {"1", "true", "yes"},
    )


if __name__ == "__main__":  # pragma: no cover
    main()
