"""Explicit JSON checkpoint codec: no pickle or dynamic class imports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date
from importlib.metadata import version
from pathlib import Path

from finreceipts.llm.client import ToolCall, Usage
from finreceipts.models import Fact, VerificationResult


def canonical(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def implementation_id() -> str:
    """Bind recovery to installed Python source and core serialization/runtime versions."""
    root = Path(__file__).resolve().parents[1]
    files = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*.py"))
    }
    return digest(
        {
            "sources": files,
            "dependencies": {name: version(name) for name in ("langgraph", "pydantic")},
        }
    )


def encode_state(state: dict) -> dict:
    data = dict(state)
    data["as_of"] = state["as_of"].isoformat() if state.get("as_of") else None
    data["usage"] = asdict(state.get("usage") or Usage())
    data["usage_by_model"] = {k: asdict(v) for k, v in state.get("usage_by_model", {}).items()}
    data["audit"] = [r.model_dump(mode="json") for r in state.get("audit", [])]
    data["messages"] = [
        {**m, "tool_calls": [asdict(t) for t in m["tool_calls"]]} if "tool_calls" in m else dict(m)
        for m in state.get("messages", [])
    ]
    return data


def decode_state(data: dict) -> dict:
    state = dict(data)
    state["as_of"] = date.fromisoformat(data["as_of"]) if data.get("as_of") else None
    state["usage"] = Usage(**data["usage"])
    state["usage_by_model"] = {k: Usage(**v) for k, v in data["usage_by_model"].items()}
    state["audit"] = [VerificationResult.model_validate(r) for r in data["audit"]]
    state["messages"] = [
        {**m, "tool_calls": [ToolCall(**t) for t in m["tool_calls"]]}
        if "tool_calls" in m
        else dict(m)
        for m in data["messages"]
    ]
    return state


def encode_lookup(lookup: dict) -> dict:
    return {
        metric: {str(year): fact.model_dump(mode="json") for year, fact in series.items()}
        for metric, series in lookup.items()
    }


def decode_lookup(data: dict) -> dict:
    return {
        metric: {int(year): Fact.model_validate(fact) for year, fact in series.items()}
        for metric, series in data.items()
    }
