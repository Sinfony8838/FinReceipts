"""Probe an LLM endpoint: one chat call and one tool-use call per model.

Reads the key from the env var named by --key-env; never prints it (errors are redacted).
Usage: python scripts/probe_llm.py --base-url URL --key-env NAME --models M1 M2 ...
"""

from __future__ import annotations

import argparse
import json
import os
import time

from finreceipts.agent.tools import GET_FACT_SPEC
from finreceipts.llm.client import AnthropicCompatClient, OpenAICompatClient, redact


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--key-env", required=True)
    ap.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic")
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--chat-only", action="store_true")
    a = ap.parse_args()
    key = os.environ.get(a.key_env, "")
    if not key:
        raise SystemExit(f"env var {a.key_env} is empty")
    cls = AnthropicCompatClient if a.provider == "anthropic" else OpenAICompatClient
    client = cls(key, a.base_url, timeout=120)
    for m in a.models:
        res: dict = {"base_url": a.base_url, "model": m}
        try:
            t = time.perf_counter()
            r = client.complete(
                [{"role": "user", "content": "Reply with exactly: pong"}], model=m, max_tokens=256
            )
            res["chat_s"] = round(time.perf_counter() - t, 2)
            res["chat_text"] = r.text.strip()[:40]
            res["chat_tokens"] = [r.usage.input_tokens, r.usage.output_tokens]
            res["stop"] = r.stop_reason
            if not a.chat_only:
                time.sleep(1.5)
                t = time.perf_counter()
                r = client.complete(
                    [
                        {
                            "role": "user",
                            "content": "What was Apple's revenue in fiscal 2023? "
                            "Use the tool (ticker AAPL, metric revenue, fiscal_year 2023).",
                        }
                    ],
                    model=m,
                    tools=[GET_FACT_SPEC],
                    max_tokens=1024,
                )
                res["tool_s"] = round(time.perf_counter() - t, 2)
                res["tool_call"] = (
                    {"name": r.tool_calls[0].name, "args": r.tool_calls[0].arguments}
                    if r.tool_calls
                    else None
                )
                res["tool_tokens"] = [r.usage.input_tokens, r.usage.output_tokens]
        except Exception as exc:  # report and continue (probe only)
            res["error"] = redact(f"{type(exc).__name__}: {exc}", [key])[:300]
        print(json.dumps(res, ensure_ascii=False), flush=True)
        time.sleep(1.5)


if __name__ == "__main__":
    main()
