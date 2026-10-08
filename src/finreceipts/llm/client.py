"""Minimal, provider-agnostic chat client with tool calling and usage accounting.

Two wire protocols are supported (both work with many hosted and self-hosted
backends): Anthropic Messages (``provider="anthropic"``) and OpenAI Chat
Completions (``provider="openai"``, e.g. vLLM/SGLang servers). ``FakeLLM`` gives
deterministic, offline behaviour for tests.

API keys are resolved from an environment variable at construction time, kept
only in the SDK client, and scrubbed from any error text via :func:`redact`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from finreceipts.config import Settings


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    latency_s: float = 0.0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        self.latency_s += other.latency_s


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    stop_reason: str | None = None


Message = dict[str, Any]
"""Provider-neutral message: {"role": "user"|"assistant"|"tool", "content": str, ...}.

Assistant messages may carry ``tool_calls: list[ToolCall]``; tool messages carry
``tool_call_id`` and ``name``.
"""


class LLMClient(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        system: str | None = None,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse: ...


def redact(text: str, secrets: Sequence[str] = ()) -> str:
    """Remove secret values from ``text`` (e.g. before logging an exception)."""
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


class FatalAPIError(RuntimeError):
    """Auth / permission / rate-limit / account-status error: stop the whole run."""


FATAL_STATUS = {401, 402, 403, 429}
_FATAL_WORDS = (
    "suspend",
    "banned",
    "ban ",
    "abuse",
    "violation",
    "forbidden",
    "quota",
    "rate limit",
    "ratelimit",
    "too many requests",
    "封禁",
    "停用",
    "违规",
    "滥用",
    "限流",
    "额度",
)
_TRANSIENT_STATUS = {500, 502, 503, 504, 529}  # 529: overloaded (MiniMax)


def _status_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def classify_error(exc: BaseException) -> str:
    """'fatal' (stop the run), 'transient' (retry once) or 'item' (record and go on)."""
    status = _status_of(exc)
    msg = str(exc).lower()
    if status in FATAL_STATUS or any(w in msg for w in _FATAL_WORDS):
        return "fatal"
    name = type(exc).__name__
    if status in _TRANSIENT_STATUS or name in {"APIConnectionError", "APITimeoutError"}:
        return "transient"
    return "item"


def call_with_policy(fn: Callable[[], Any], secret: str, *, retry_wait_s: float = 5.0) -> Any:
    """Run ``fn``; retry once on transient errors; raise FatalAPIError on fatal ones.

    Error messages are redacted so the key can never leak into logs or results.
    """
    for attempt in (1, 2):
        try:
            return fn()
        except Exception as exc:
            kind = classify_error(exc)
            text = redact(f"{type(exc).__name__}[{_status_of(exc)}]: {exc}", [secret])[:500]
            if kind == "fatal":
                raise FatalAPIError(text) from None
            if kind == "transient" and attempt == 1:
                time.sleep(retry_wait_s)
                continue
            raise RuntimeError(text) from None
    raise AssertionError("unreachable")  # pragma: no cover


class PacedClient:
    """Sequential pacing (>= ``min_interval_s`` between call starts) + an attempt ledger."""

    def __init__(self, inner: LLMClient, min_interval_s: float = 1.0) -> None:
        self.inner = inner
        self.min_interval_s = min_interval_s
        self._last = 0.0
        self.attempts: dict[str, int] = {}
        self.failures: dict[str, int] = {}

    def complete(self, messages: Sequence[Message], *, model: str, **kw: Any) -> LLMResponse:
        wait = self._last + self.min_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.attempts[model] = self.attempts.get(model, 0) + 1
        try:
            return self.inner.complete(messages, model=model, **kw)
        except Exception:
            self.failures[model] = self.failures.get(model, 0) + 1
            raise


THINKING_SUFFIX = "+think"
THINKING_MIN_MAX_TOKENS = 4096


def split_thinking_alias(model: str) -> tuple[str, bool]:
    """``"MiniMax-M3+think"`` -> ``("MiniMax-M3", True)``: same model, thinking enabled.

    Lets one model serve as two cascade tiers (no-thinking small, thinking large) while
    usage stays attributed to the alias.
    """
    if model.endswith(THINKING_SUFFIX):
        return model[: -len(THINKING_SUFFIX)], True
    return model, False


# --------------------------------------------------------------------- Anthropic
class AnthropicCompatClient:
    """Anthropic Messages API (or any Anthropic-compatible endpoint)."""

    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 120.0) -> None:
        import anthropic

        self._secret = api_key
        self._client = anthropic.Anthropic(
            api_key=api_key, base_url=base_url or None, timeout=timeout, max_retries=0
        )

    @staticmethod
    def _to_wire(messages: Sequence[Message]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                }
                if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list):
                    wire[-1]["content"].append(block)
                else:
                    wire.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant" and m.get("tool_calls"):
                content: list[dict[str, Any]] = []
                if m.get("content"):
                    content.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    content.append(
                        {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                    )
                wire.append({"role": "assistant", "content": content})
            else:
                wire.append({"role": m["role"], "content": m["content"]})
        return wire

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        system: str | None = None,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        model, thinking = split_thinking_alias(model)
        body: dict[str, Any] = {"temperature": temperature}
        if thinking:
            body["thinking"] = {"type": "adaptive"}
            max_tokens = max(max_tokens, THINKING_MIN_MAX_TOKENS)  # thinking counts toward it
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": self._to_wire(messages),
            # anthropic>=1.x dropped the ``temperature`` kwarg; send extras in the body
            "extra_body": body,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        t0 = time.perf_counter()
        r = call_with_policy(lambda: self._client.messages.create(**kwargs), self._secret)
        dt = time.perf_counter() - t0
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
            for b in r.content
            if b.type == "tool_use"
        ]
        return LLMResponse(
            text=text,
            tool_calls=calls,
            usage=Usage(r.usage.input_tokens, r.usage.output_tokens, 1, dt),
            model=model,
            stop_reason=r.stop_reason,
        )


# ------------------------------------------------------------------------ OpenAI
class OpenAICompatClient:
    """OpenAI Chat Completions API (vLLM, SGLang and most hosted gateways)."""

    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 120.0) -> None:
        import openai

        self._secret = api_key
        self._client = openai.OpenAI(
            api_key=api_key, base_url=base_url or None, timeout=timeout, max_retries=0
        )

    @staticmethod
    def _to_wire(messages: Sequence[Message], system: str | None) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m["role"] == "tool":
                wire.append(
                    {"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]}
                )
            elif m["role"] == "assistant" and m.get("tool_calls"):
                wire.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }
                            for tc in m["tool_calls"]
                        ],
                    }
                )
            else:
                wire.append({"role": m["role"], "content": m["content"]})
        return wire

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        system: str | None = None,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": self._to_wire(messages, system),
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        t0 = time.perf_counter()
        r = call_with_policy(lambda: self._client.chat.completions.create(**kwargs), self._secret)
        dt = time.perf_counter() - t0
        msg = r.choices[0].message
        calls = [
            ToolCall(
                id=tc.id, name=tc.function.name, arguments=json.loads(tc.function.arguments or "{}")
            )
            for tc in (msg.tool_calls or [])
        ]
        u = r.usage
        return LLMResponse(
            text=msg.content or "",
            tool_calls=calls,
            usage=Usage(u.prompt_tokens if u else 0, u.completion_tokens if u else 0, 1, dt),
            model=model,
            stop_reason=r.choices[0].finish_reason,
        )


# -------------------------------------------------------------------------- Fake
Responder = Callable[[Sequence[Message], str, Sequence[ToolSpec]], LLMResponse | str]


class FakeLLM:
    """Deterministic offline LLM.

    ``responder`` receives ``(messages, model, tools)`` and returns either an
    :class:`LLMResponse` or plain text. Token usage is approximated as
    ``len(text) // 4`` so cost accounting can be tested end to end.
    """

    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        system: str | None = None,
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append({"messages": list(messages), "model": model, "system": system})
        out = self.responder(messages, model, tools)
        resp = out if isinstance(out, LLMResponse) else LLMResponse(text=out)
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages) + len(system or "")
        resp.usage = Usage(prompt_chars // 4, len(resp.text) // 4, 1, 0.0)
        resp.model = model
        return resp


CODING_PLAN_MARKER = "/api/coding"
"""Volcengine Ark Coding Plan endpoints (``.../api/coding`` and ``.../api/coding/v3``).

The Coding Plan terms restrict its quota to AI coding tools and say it may not be
used for API calls (see docs/llm_access.md), so programmatic use is refused unless
the owner explicitly opts in with ``FINRECEIPTS_ALLOW_CODING_PLAN=1``.
"""


def is_coding_plan_url(base_url: str | None) -> bool:
    return bool(base_url) and CODING_PLAN_MARKER in str(base_url).rstrip("/") + "/"


def make_client(settings: Settings) -> LLMClient:
    """Build a client from settings. Keys come only from the named env var."""
    if settings.llm_provider == "fake":
        return FakeLLM(lambda *_: "I don't know.")
    if is_coding_plan_url(settings.llm_base_url) and not settings.allow_coding_plan:
        raise RuntimeError(
            "refusing to call a Volcengine Ark Coding Plan endpoint: its terms limit the quota "
            "to AI coding tools (see docs/llm_access.md). Use a pay-as-you-go endpoint, or set "
            "FINRECEIPTS_ALLOW_CODING_PLAN=1 if you have confirmed this use is permitted."
        )
    if settings.llm_provider not in ("anthropic", "openai"):
        raise ValueError(f"unknown provider {settings.llm_provider!r}")
    key = settings.resolve_api_key()
    cls = AnthropicCompatClient if settings.llm_provider == "anthropic" else OpenAICompatClient
    return PacedClient(cls(key, settings.llm_base_url or None), settings.min_call_interval_s)


__all__ = [
    "AnthropicCompatClient",
    "FakeLLM",
    "FatalAPIError",
    "LLMClient",
    "LLMResponse",
    "OpenAICompatClient",
    "PacedClient",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "classify_error",
    "is_coding_plan_url",
    "make_client",
    "redact",
    "split_thinking_alias",
]
