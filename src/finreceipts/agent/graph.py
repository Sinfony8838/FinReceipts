"""The FinReceipts agent graph (LangGraph).

    answer ──► verify ──► (all numbers have receipts | budget spent) ──► END
                 ▲            │                          │
                 └── revise ◄─┘  (mode verify/cascade)   └─► escalate ──► answer
                                                          (mode cascade, once:
                                                           small -> large model)

The verifier is *unbound*: it does not know the question's answer key; it only
checks whether each number in the answer is accounted for by some reported fact
(or a simple derived ratio/growth) for the company and fiscal years in scope.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph

from finreceipts.agent import pit_wiring
from finreceipts.agent.state import AgentState
from finreceipts.agent.tools import (
    GET_A_SHARE_FACT_SPEC,
    GET_FACT_SPEC,
    LookupProvider,
    run_get_a_share_fact,
    run_get_fact,
)
from finreceipts.llm.client import LLMClient, Message, Usage
from finreceipts.models import Verdict, VerificationResult
from finreceipts.verify.numbers import extract_numbers
from finreceipts.verify.serialization import serialize_audit
from finreceipts.verify.verifier import Tolerance, Verifier

SYSTEM_PROMPT = (
    "You are a careful financial research assistant. Answer questions about US and China "
    "A-share public companies' reported financials. Be concise. Use exact figures where you "
    "can and state units (USD or CNY). End your reply with a final line of the form "
    "'ANSWER: <number with unit>', e.g. 'ANSWER: $383,285 million', 'ANSWER: 174144069958.25元', "
    "or 'ANSWER: -2.8%'. Never give investment advice."
)
TOOLS_HINT = (
    "You can call get_financial_fact (US SEC XBRL) or get_a_share_financial_fact "
    "(China A-share annuals). Prefer tools to memory."
)
MAX_TOOL_STEPS = 4
_ANSWER_RE = re.compile(r"ANSWER:\s*(.+)", re.IGNORECASE)


def final_answer_text(answer: str) -> str:
    """The text after the last 'ANSWER:' marker (or the whole answer)."""
    found = _ANSWER_RE.findall(answer or "")
    return found[-1].strip() if found else (answer or "").strip()


def summarize_audit(audit: list[VerificationResult]) -> dict[str, int]:
    out = {"numbers": len(audit), "supported": 0, "contradicted": 0, "unsupported": 0}
    for r in audit:
        out[r.verdict.value] += 1
    return out


@dataclass
class AgentDeps:
    llm: LLMClient
    lookup_provider: LookupProvider
    tol: Tolerance = field(default_factory=Tolerance)
    # Optional PIT/red-team hooks (see agent/pit_wiring.py). All default off,
    # so existing runs and recorded fixtures are unchanged.
    tool_guard: Any = None  # Callable[[ToolCall, AgentState], None]; raises to refuse
    tool_cache: Any = None  # pit_wiring.SealedToolCache
    mark_untrusted: bool = False


def _answer_node(deps: AgentDeps):
    def node(state: AgentState) -> dict[str, Any]:
        messages: list[Message] = list(state["messages"])
        usage = state.get("usage") or Usage()
        by_model = dict(state.get("usage_by_model") or {})
        tools = [GET_FACT_SPEC, GET_A_SHARE_FACT_SPEC] if state.get("use_tools") else []
        system = SYSTEM_PROMPT + ("\n" + TOOLS_HINT if tools else "")
        if tools and deps.mark_untrusted:
            system += "\n" + pit_wiring.UNTRUSTED_NOTE
        text = ""
        for _ in range(MAX_TOOL_STEPS + 1):
            resp = deps.llm.complete(messages, model=state["model"], system=system, tools=tools)
            usage.add(resp.usage)
            by_model.setdefault(state["model"], Usage()).add(resp.usage)
            if not resp.tool_calls:
                text = resp.text
                messages.append({"role": "assistant", "content": text})
                break
            messages.append(
                {"role": "assistant", "content": resp.text, "tool_calls": resp.tool_calls}
            )
            for tc in resp.tool_calls:
                result = _run_tool(deps, tc, state)
                if deps.mark_untrusted:
                    result = pit_wiring.wrap_untrusted(tc.name, result)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": result}
                )
        return {"messages": messages, "answer": text, "usage": usage, "usage_by_model": by_model}

    return node


def _run_tool(deps: AgentDeps, tc, state: AgentState) -> str:
    if deps.tool_guard is not None:
        try:
            deps.tool_guard(tc, state)
        except ValueError as exc:
            return json.dumps({"error": f"tool call refused: {exc}"})
    if deps.tool_cache is not None:
        cached = deps.tool_cache.get(tc.name, tc.arguments)
        if cached is not None:
            return cached
    result = _dispatch_tool(deps, tc, state)
    if deps.tool_cache is not None and "error" not in json.loads(result):
        deps.tool_cache.put(tc.name, tc.arguments, result)
    return result


def _dispatch_tool(deps: AgentDeps, tc, state: AgentState) -> str:
    if tc.name == GET_FACT_SPEC.name:
        return run_get_fact(deps.lookup_provider, tc.arguments, state.get("as_of"))
    if tc.name == GET_A_SHARE_FACT_SPEC.name:
        return run_get_a_share_fact(deps.lookup_provider, tc.arguments, state.get("as_of"))
    return json.dumps({"error": f"unknown tool {tc.name}"})


def _verify_node(deps: AgentDeps):
    def node(state: AgentState) -> dict[str, Any]:
        lookup = deps.lookup_provider(state["ticker"], state.get("as_of"))
        verifier = Verifier(lookup, deps.tol)
        years = set(state.get("fiscal_years") or []) or None
        audit = verifier.audit_text(state.get("answer", ""), fiscal_years=years)
        attempts = [
            *state.get("attempts", []),
            {
                "answer": state.get("answer", ""),
                "model": state.get("model", ""),
                **summarize_audit(audit),
                # Snapshot against this exact answer before revision/escalation
                # replaces the live audit. Do not backfill historical attempts.
                "audit": serialize_audit(audit),
            },
        ]
        return {"audit": audit, "attempts": attempts}

    return node


def feedback_message(state: AgentState, deps: AgentDeps) -> str:
    """Verifier feedback for the reviser (flags only, or flags + candidate receipts)."""
    bad = [r for r in state.get("audit", []) if r.verdict is not Verdict.SUPPORTED]
    lines = ["A verifier checked every number in your answer against the company's SEC filings."]
    for r in bad:
        lines.append(f"- '{r.claim.text}': {r.verdict.value} ({'; '.join(r.reasons)})")
    if not _has_final_number(state.get("answer", "")):
        lines.append(
            "- your reply has no final 'ANSWER: <number with unit>' line with a number; "
            "the question asks for a specific figure."
        )
    if state.get("feedback") == "receipts":
        lookup = deps.lookup_provider(state["ticker"], state.get("as_of"))
        lines.append("Reported figures available for the fiscal years in scope:")
        for name, series in lookup.items():
            for fy in state.get("fiscal_years") or []:
                f = series.get(fy)
                if f is not None:
                    lines.append(
                        f"  {name} FY{fy} (period ending {f.end}): {f.value:,.2f} {f.unit} "
                        f"[{f.concept}]"
                    )
    lines.append(
        "Revise your answer so that every number is supported by the filings. "
        "If you cannot support a number, remove it. Keep the 'ANSWER:' line."
    )
    return "\n".join(lines)


def _revise_node(deps: AgentDeps):
    def node(state: AgentState) -> dict[str, Any]:
        messages = [*state["messages"], {"role": "user", "content": feedback_message(state, deps)}]
        return {"messages": messages, "revisions": state.get("revisions", 0) + 1}

    return node


def _escalate_node(state: AgentState) -> dict[str, Any]:
    """Hand the question to the large model from scratch (fresh context, fresh budget)."""
    return {
        "model": state["escalate_model"],
        "messages": [{"role": "user", "content": state["question"]}],
        "escalated": True,
        "revisions_before_escalation": state.get("revisions", 0),
    }


def _has_final_number(answer: str) -> bool:
    return bool(extract_numbers(final_answer_text(answer)))


def verification_passed(state: AgentState) -> bool:
    """The gate: every number has a receipt *and* the final 'ANSWER:' carries a number.

    An answer without any number would otherwise pass vacuously.
    """
    answer = state.get("answer", "")
    return all(r.ok for r in state.get("audit", [])) and _has_final_number(answer)


def _route_after_verify(state: AgentState) -> str:
    mode = state.get("mode")
    if mode not in ("verify", "cascade"):
        return END
    if verification_passed(state):
        return END
    budget = state.get("max_revisions", 2)
    used = state.get("revisions", 0) - state.get("revisions_before_escalation", 0)
    if used < budget:
        return "revise"
    if mode == "cascade" and not state.get("escalated") and state.get("escalate_model"):
        return "escalate"
    return END


def build_graph(deps: AgentDeps):
    """Compile the LangGraph state machine."""
    g = StateGraph(AgentState)
    g.add_node("answer", _answer_node(deps))
    g.add_node("verify", _verify_node(deps))
    g.add_node("revise", _revise_node(deps))
    g.add_node("escalate", _escalate_node)
    g.add_edge(START, "answer")
    g.add_edge("answer", "verify")
    g.add_conditional_edges(
        "verify", _route_after_verify, {"revise": "revise", "escalate": "escalate", END: END}
    )
    g.add_edge("revise", "answer")
    g.add_edge("escalate", "answer")
    return g.compile()


def initial_state(
    question: str,
    *,
    ticker: str,
    fiscal_years: list[int],
    model: str,
    mode: str = "baseline",
    feedback: str = "flags",
    use_tools: bool = False,
    as_of=None,
    max_revisions: int = 2,
    escalate_model: str | None = None,
) -> AgentState:
    if mode == "cascade" and not escalate_model:
        raise ValueError("cascade mode needs escalate_model")
    return AgentState(
        question=question,
        ticker=ticker,
        fiscal_years=fiscal_years,
        as_of=as_of,
        mode=mode,  # type: ignore[typeddict-item]
        feedback=feedback,  # type: ignore[typeddict-item]
        use_tools=use_tools,
        model=model,
        max_revisions=max_revisions,
        escalate_model=escalate_model,
        escalated=False,
        revisions_before_escalation=0,
        usage_by_model={},
        messages=[{"role": "user", "content": question}],
        revisions=0,
        usage=Usage(),
        attempts=[],
    )
