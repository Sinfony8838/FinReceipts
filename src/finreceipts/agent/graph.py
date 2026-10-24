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
import time
from copy import deepcopy
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
from finreceipts.verify.target import VerificationTarget, target_evidence, verify_target_answer
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
    tool_observer: Any = None


def _answer_node(deps: AgentDeps):
    def node(state: AgentState) -> dict[str, Any]:
        messages: list[Message] = list(state["messages"])
        usage = deepcopy(state.get("usage") or Usage())
        by_model = deepcopy(state.get("usage_by_model") or {})
        tools = [GET_FACT_SPEC, GET_A_SHARE_FACT_SPEC] if state.get("use_tools") else []
        system = SYSTEM_PROMPT + ("\n" + TOOLS_HINT if tools else "")
        if state.get("target"):
            system += "\nExplicit verification target: " + json.dumps(
                state["target"], sort_keys=True
            )
        if tools and deps.mark_untrusted:
            system += "\n" + pit_wiring.UNTRUSTED_NOTE
        text = ""
        for _ in range(MAX_TOOL_STEPS + 1):
            resp = deps.llm.complete(messages, model=state["model"], system=system, tools=tools)
            usage.add(resp.usage)
            by_model.setdefault(state["model"], Usage()).add(resp.usage)
            if not resp.tool_calls:
                text = resp.text
                messages.append(resp.assistant_message())
                break
            messages.append(resp.assistant_message())
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
    started = time.perf_counter()
    if deps.tool_observer:
        deps.tool_observer("started", tc, None, 0.0)
    try:
        result = _execute_tool(deps, tc, state)
    except Exception:
        if deps.tool_observer:
            deps.tool_observer("error", tc, None, time.perf_counter() - started)
        raise
    if deps.tool_observer:
        deps.tool_observer("completed", tc, result, time.perf_counter() - started)
    return result


def _execute_tool(deps: AgentDeps, tc, state: AgentState) -> str:
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
        # Historical numeric QA routing remains numeric-only. It must not be
        # described as natural-text semantic attribution verification.
        audit = verifier.audit_text(state.get("answer", ""), fiscal_years=years, numeric_only=True)
        target_delta = {}
        if state.get("target"):
            target_delta["target_audit"] = verify_target_answer(
                state.get("answer"), state["target"], lookup, deps.tol
            ).model_dump(mode="json")
        attempts = [
            *state.get("attempts", []),
            {
                "answer": state.get("answer", ""),
                "model": state.get("model", ""),
                "audit_mode": "numeric_only",
                **summarize_audit(audit),
                # Snapshot against this exact answer before revision/escalation
                # replaces the live audit. Do not backfill historical attempts.
                "audit": serialize_audit(audit),
                **target_delta,
            },
        ]
        return {"audit": audit, "attempts": attempts, **target_delta}

    return node


def feedback_message(state: AgentState, deps: AgentDeps) -> str:
    """Verifier feedback for the reviser (flags only, or flags + candidate receipts)."""
    bad = [r for r in state.get("audit", []) if r.verdict is not Verdict.SUPPORTED]
    lines = ["A verifier checked every number in your answer against the company's SEC filings."]
    if state.get("target"):
        lines.append("Target check: " + json.dumps(state.get("target_audit", {}), sort_keys=True))
        if state.get("feedback") != "receipts":
            # Flags must not accidentally disclose the expected value through the audit.
            lines[-1] = "Target check: " + json.dumps(
                {k: v for k, v in state.get("target_audit", {}).items() if k != "evidence"}
            )
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
        if state.get("target"):
            evidence, _ = target_evidence(
                VerificationTarget.model_validate(state["target"]), lookup
            )
            if evidence:
                lines.append("Target evidence: " + evidence.model_dump_json())
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
    audit = state.get("audit", [])
    numeric_passed = bool(audit) and all(r.ok for r in audit) and _has_final_number(answer)
    target_passed = (
        not state.get("target") or state.get("target_audit", {}).get("verdict") == "supported"
    )
    return numeric_passed and target_passed


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


def step_nodes(deps: AgentDeps) -> dict[str, Any]:
    """Shared node implementations for LangGraph and the durable step runner."""
    return {
        "answer": _answer_node(deps),
        "verify": _verify_node(deps),
        "revise": _revise_node(deps),
        "escalate": _escalate_node,
    }


def next_node(node: str, state: AgentState) -> str:
    """One transition policy, shared with the durable runner."""
    if node == "verify":
        return _route_after_verify(state)
    return {"answer": "verify", "revise": "answer", "escalate": "answer"}[node]


def build_graph(deps: AgentDeps):
    """Compile the LangGraph state machine."""
    g = StateGraph(AgentState)
    for name, node in step_nodes(deps).items():
        g.add_node(name, node)
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
    target: dict | None = None,
) -> AgentState:
    if mode == "cascade" and not escalate_model:
        raise ValueError("cascade mode needs escalate_model")
    state = AgentState(
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
    if target is not None:
        parsed = VerificationTarget.model_validate(target)
        if max_revisions > 2 or mode == "cascade":
            raise ValueError("target mode allows at most two revisions and no escalation")
        if fiscal_years != [parsed.fiscal_year]:
            raise ValueError("target requires exactly its fiscal year")
        state["target"] = parsed.model_dump(mode="json")
    return state
