"""Run the existing agent one durable node at a time against frozen facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from finreceipts.agent.graph import (
    AgentDeps,
    initial_state,
    next_node,
    step_nodes,
    summarize_audit,
    verification_passed,
)
from finreceipts.agent.tools import GET_A_SHARE_FACT_SPEC, GET_FACT_SPEC
from finreceipts.runtime.codec import (
    decode_lookup,
    decode_state,
    encode_lookup,
    encode_state,
    implementation_id,
)
from finreceipts.runtime.store import BudgetExceeded, RunConflict, RunStore, StoreBusy
from finreceipts.verify.verifier import Verifier


class EvidenceUnavailable(ValueError):
    """No selected facts cover this request; not a provider parsing failure."""


def counter_audit(doc: dict) -> dict:
    """Two deterministic audits of the same answer/bytes; never another LLM call."""
    state = decode_state(doc["state"])
    verifier = Verifier(decode_lookup(doc["evidence"]))
    years = set(state["fiscal_years"])
    answer = state.get("answer", "")
    numeric = verifier.audit_text(answer, fiscal_years=years, numeric_only=True)
    scoped = verifier.audit_text(answer, fiscal_years=years)
    disagreements = []
    for left, right in zip(numeric, scoped, strict=True):
        if left.verdict != right.verdict:
            disagreements.append(
                {
                    "claim": right.claim.text,
                    "span": right.claim.span,
                    "numeric": left.verdict.value,
                    "bounded_annual": right.verdict.value,
                    "reasons": right.reasons,
                }
            )
    return {
        "evidence_digest": doc["evidence_digest"],
        "numeric": summarize_audit(numeric),
        "bounded_annual": summarize_audit(scoped),
        "disagreements": disagreements,
        "numeric_gate_passed": bool(numeric) and all(r.ok for r in numeric),
        "bounded_gate_passed": bool(scoped) and all(r.ok for r in scoped),
        "bounded_audit": [r.model_dump(mode="json") for r in scoped],
        "coverage": "supported annual clauses only; not whole-answer correctness",
        "model_calls": 0,
    }


@dataclass
class ReservedLLM:
    delegate: object
    store: RunStore
    run_id: str
    token: str
    maximum: int

    def complete(self, *args, **kwargs):
        # Reserve *before* dispatch: a crash cannot reset the paid-call budget.
        # Provider SDK internal retries are outside this logical-dispatch count.
        self.store.reserve(self.run_id, self.token, "dispatches", self.maximum)
        return self.delegate.complete(*args, **kwargs)


class RunService:
    def __init__(self, store: RunStore, llm, lookup_provider, *, provider_id="fake"):
        self.store, self.llm, self.lookup_provider = store, llm, lookup_provider
        self.provider_id = provider_id
        self.implementation_id = implementation_id()

    def create(self, key: str, request: dict) -> dict:
        request = {
            **request,
            "provider_id": self.provider_id,
            "implementation_id": self.implementation_id,
        }
        if isinstance(request.get("as_of"), date):
            request["as_of"] = request["as_of"].isoformat()
        existing = self.store.find(key, request)
        if existing:
            return existing
        state = initial_state(
            **{
                k: v
                for k, v in request.items()
                if k not in {"max_dispatches", "max_tool_calls", "provider_id", "implementation_id"}
            }
        )
        if isinstance(state.get("as_of"), str):
            state["as_of"] = date.fromisoformat(state["as_of"])
        # Pin only the requested periods and preceding years needed by growth.
        years = set(request["fiscal_years"])
        allowed = years | {y - 1 for y in years}
        lookup = self.lookup_provider(request["ticker"], state.get("as_of"))
        evidence = encode_lookup(
            {k: {y: f for y, f in v.items() if y in allowed} for k, v in lookup.items()}
        )
        if not any(evidence.values()):
            raise EvidenceUnavailable("no financial evidence for the requested scope")
        serialized = {**request, "as_of": state["as_of"].isoformat() if state["as_of"] else None}
        return self.store.create(key, serialized, evidence, encode_state(state))

    def advance(self, run_id: str, revision: int) -> dict:
        original = self.store.get(run_id)
        if original["request"]["provider_id"] != self.provider_id:
            raise RunConflict("provider configuration changed; create a new run")
        if original["request"]["implementation_id"] != self.implementation_id:
            raise RunConflict("implementation changed; export the old run and create a new run")
        doc, token = self.store.claim(run_id, revision)
        request = doc["request"]
        state = decode_state(doc["state"])
        evidence = decode_lookup(doc["evidence"])

        def frozen_provider(ticker, as_of):
            if ticker.upper() != state["ticker"] or as_of != state.get("as_of"):
                raise ValueError("tool request is outside the pinned evidence scope")
            return evidence

        def guard(tc, _state):
            self.store.reserve(run_id, token, "tool_calls", request["max_tool_calls"])
            if not state["use_tools"]:
                raise ValueError("tools disabled for this run")
            specs = {GET_FACT_SPEC.name: "ticker", GET_A_SHARE_FACT_SPEC.name: "code"}
            if tc.name not in specs:
                raise ValueError("tool is not allowed")
            args = tc.arguments
            expected = {specs[tc.name], "metric", "fiscal_year"}
            if not isinstance(args, dict) or set(args) != expected:
                raise ValueError("tool arguments must match the declared schema")
            years = set(state["fiscal_years"])
            allowed = years | {y - 1 for y in years}
            if (
                args[specs[tc.name]] != state["ticker"]
                or type(args["fiscal_year"]) is not int
                or args["fiscal_year"] not in allowed
                or not isinstance(args["metric"], str)
                or args["metric"] not in evidence
            ):
                raise ValueError("tool request is outside the pinned evidence scope")

        deps = AgentDeps(
            llm=ReservedLLM(self.llm, self.store, run_id, token, request["max_dispatches"]),
            lookup_provider=frozen_provider,
            tool_guard=guard,
            mark_untrusted=True,
        )
        try:
            node = doc["next_node"]
            delta = step_nodes(deps)[node](state)
            state.update(delta)
            following = next_node(node, state)
            passed = verification_passed(state)
            quality = {
                "numeric_gate_passed": passed,
                "whole_answer_verified": False,
                "reason": "numeric_match_only" if passed else "unresolved_claims",
            }
            # Verification must describe the current answer, not the previous attempt.
            if node == "answer":
                state["audit"] = []
                quality = {
                    "numeric_gate_passed": False,
                    "whole_answer_verified": False,
                    "reason": "awaiting_verification",
                }
            return self.store.finish(run_id, token, encode_state(state), following, quality)
        except (RunConflict, StoreBusy):
            # Never replay a provider call on contention, or turn an unknown
            # commit outcome into a second state mutation. Reconcile via GET.
            raise
        except Exception as exc:
            # Provider errors may contain private prompts/URLs/keys. Persist a code only.
            return self.store.fail(run_id, token, budget=isinstance(exc, BudgetExceeded))
