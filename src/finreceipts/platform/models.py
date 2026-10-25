"""Single-attempt model adapters. Retry and money policy belong to the coordinator."""

from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


class DispatchUnknown(RuntimeError):
    """The provider may have executed the request; do not silently retry."""


class ProviderRejected(RuntimeError):
    def __init__(self, status: int, retryable: bool = False):
        self.status, self.retryable = status, retryable
        super().__init__(f"provider_rejected_{status}")


@dataclass
class Response:
    text: str
    assistant: dict
    tools: list[dict]
    usage: dict
    model: str
    fingerprint: str | None
    stop_reason: str


def request_body(profile: dict, messages: list[dict], system: str, tools: list[dict]) -> dict:
    """History is provider-native: never discard reasoning or tool-call blocks."""
    provider = profile["provider"]
    body: dict[str, Any] = {
        "model": profile["model"],
        "messages": copy.deepcopy(messages),
        "max_tokens": profile["max_output_tokens"],
    }
    if provider == "minimax":
        if profile["model"] != "MiniMax-M3.1-Flash":
            raise ValueError("unsupported_minimax_model")
        body.update(system=system, output_config={"effort": profile["effort"]}, tools=tools)
    elif provider == "deepseek":
        if profile["model"] != "deepseek-flash":
            raise ValueError("unsupported_deepseek_model")
        body["messages"] = [{"role": "system", "content": system}, *body["messages"]]
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = "high"
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]
    else:
        raise ValueError("unsupported_provider")
    # UTF-8 byte count is a conservative upper bound for these byte-tokenized APIs.
    # This also limits stored history and prevents unbounded context growth.
    if len(json.dumps(body, ensure_ascii=False).encode()) > 32000:
        raise ValueError("context_budget_exceeded")
    return body


class Adapter:
    def __init__(self, provider: str, *, transport=None):
        self.provider = provider
        self.client = httpx.Client(timeout=httpx.Timeout(120, connect=10), transport=transport)

    def close(self):
        self.client.close()

    def complete(self, profile, messages, system, tools, *, fake_answer="ANSWER: N/A") -> Response:
        if self.provider == "fake":
            time.sleep(float(os.getenv("FAKE_DELAY_SECONDS", "0")))
            return Response(
                fake_answer,
                {"role": "assistant", "content": fake_answer},
                [],
                {"input_tokens": 0, "output_tokens": 0},
                "fake",
                None,
                "end_turn",
            )
        body = request_body(profile, messages, system, tools)
        if self.provider == "minimax":
            key = os.environ.get("MINIMAX_API_KEY", "")
            base = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.cn/anthropic").rstrip("/")
            url, headers = (
                base + "/v1/messages",
                {"x-api-key": key, "anthropic-version": "2023-06-01"},
            )
        else:
            key = os.environ.get("DEEPSEEK_API_KEY", "")
            url = "https://api.deepseek.com/chat/completions"
            headers = {"Authorization": "Bearer " + key}
        if not key:
            raise ValueError("provider_credentials_missing")
        try:
            response = self.client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise DispatchUnknown("provider_transport_unknown") from exc
        if response.status_code in {400, 401, 402, 403, 404, 422, 429}:
            raise ProviderRejected(response.status_code, retryable=response.status_code == 429)
        if response.status_code >= 400:
            raise DispatchUnknown("provider_response_unknown")
        try:
            data = response.json()
            if self.provider == "minimax":
                blocks = data["content"]
                assistant = {"role": "assistant", "content": blocks}
                text = "".join(b["text"] for b in blocks if b["type"] == "text")
                calls = [
                    {"id": b["id"], "name": b["name"], "arguments": b["input"]}
                    for b in blocks
                    if b["type"] == "tool_use"
                ]
                usage, reason = data["usage"], data["stop_reason"]
            else:
                choice = data["choices"][0]
                msg = choice["message"]
                assistant = {
                    k: v
                    for k, v in msg.items()
                    if k in {"role", "content", "reasoning_content", "tool_calls"}
                }
                text = msg.get("content") or ""
                calls = [
                    {
                        "id": c["id"],
                        "name": c["function"]["name"],
                        "arguments": json.loads(c["function"]["arguments"]),
                    }
                    for c in msg.get("tool_calls", [])
                ]
                raw = data["usage"]
                usage = {
                    **raw,
                    "input_tokens": raw["prompt_tokens"],
                    "output_tokens": raw["completion_tokens"],
                }
                reason = choice["finish_reason"]
            if not isinstance(usage["input_tokens"], int) or not isinstance(
                usage["output_tokens"], int
            ):
                raise ValueError("invalid_usage")
            return Response(
                text,
                assistant,
                calls,
                usage,
                data.get("model", profile["model"]),
                data.get("system_fingerprint"),
                reason,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise DispatchUnknown("provider_protocol_unknown") from exc


def tool_result(provider: str, call_id: str, result: str) -> dict:
    if provider == "minimax":
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": result}],
        }
    return {"role": "tool", "tool_call_id": call_id, "content": result}


def cost_micros(profile: dict, usage: dict) -> int:
    """Conservative CNY estimate, charging all input as uncached, no discounts.

    Rates are explicit execution policy rather than an invoice: CNY 3/12 per
    million input/output tokens exceeds the inspected Flash list rates using
    a conservative conversion. Live execution must confirm prices first.
    One CNY = 1,000,000 ledger units; unknown usage keeps the full reservation.
    """
    if profile["provider"] == "fake":
        return 0
    return int(usage["input_tokens"]) * 3 + int(usage["output_tokens"]) * 12


def reservation_micros(profile: dict) -> int:
    return 1 if profile["provider"] == "fake" else 32000 * 3 + profile["max_output_tokens"] * 12
