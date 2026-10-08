"""Collect ``*/summary.json`` under a results directory into a Markdown table."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SHORT = {
    "doubao-seed-2.0-mini": "mini",
    "doubao-seed-2.1-pro": "pro",
    "deepseek-v4-flash": "ds-flash",
    "deepseek-v4-pro": "ds-pro",
}
HEAD = [
    "run",
    "models",
    "mode",
    "n",
    "err",
    "accuracy",
    "unsupported-number rate",
    "answers w/ unsupported",
    "mean revisions",
    "escalation rate",
    "LLM calls",
    "avg tokens/item in/out (by model)",
    "item latency p50 / p95 s",
    "mean call latency s",
    "cost",
]


def short(m: str) -> str:
    return SHORT.get(m, m)


def row(name: str, s: dict) -> list[str]:
    cfg = s.get("config", {})
    n = s.get("n_items") or 0
    models = short(cfg.get("model", ""))
    if cfg.get("escalate_model"):
        models += " -> " + short(cfg["escalate_model"])
    tbm = s.get("tokens_by_model", {})
    tok = (
        "; ".join(
            f"{short(m)} {u['input_tokens'] / n:.0f}/{u['output_tokens'] / n:.0f}"
            for m, u in tbm.items()
        )
        if n
        else "-"
    )
    lat = "; ".join(f"{short(m)} {u.get('mean_call_latency_s', 0):.2f}" for m, u in tbm.items())
    cost = s.get("cost")
    return [
        name,
        models,
        cfg.get("mode", "") + (f" ({cfg['feedback']})" if cfg.get("mode") == "verify" else ""),
        str(n),
        str(s.get("n_errors")),
        f"{s['accuracy']:.2f}",
        f"{s['unsupported_number_rate']:.3f}",
        f"{s['answers_with_unsupported']:.2f}",
        f"{s['mean_revisions']:.2f}",
        f"{s.get('escalation_rate', 0):.2f}",
        str(s.get("llm_calls")),
        tok,
        f"{s['latency_p50_s']:.1f} / {s['latency_p95_s']:.1f}",
        lat or "-",
        "-" if cost is None else f"{cost:.4f}",
    ]


def main(root: str) -> None:
    print("| " + " | ".join(HEAD) + " |")
    print("|" + "---|" * len(HEAD))
    for p in sorted(Path(root).glob("*/summary.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        print("| " + " | ".join(row(p.parent.name, s)) + " |")
        if s.get("aborted"):
            print(f"\n> {p.parent.name} ABORTED: {s['aborted']}\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results")
