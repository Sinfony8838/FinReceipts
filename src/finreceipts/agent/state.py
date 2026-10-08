"""LangGraph state schema for the answer -> verify -> revise loop."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, TypedDict

from finreceipts.llm.client import Message, Usage
from finreceipts.models import VerificationResult

Mode = Literal["baseline", "verify", "cascade"]
Feedback = Literal["flags", "receipts"]


class AgentState(TypedDict, total=False):
    """Mutable state threaded through the graph.

    ``mode="baseline"`` answers once (the verifier still runs, for measurement);
    ``mode="verify"`` loops answer -> verify -> revise until every number has a
    receipt or ``max_revisions`` is reached.
    ``mode="cascade"`` runs the verify loop on ``model`` (the small tier) and, if
    the verifier still flags a number, escalates once to ``escalate_model`` (the
    large tier), which answers from scratch and gets its own verify loop.
    """

    question: str
    ticker: str
    fiscal_years: list[int]
    as_of: date | None
    mode: Mode
    feedback: Feedback
    use_tools: bool
    model: str
    max_revisions: int
    escalate_model: str | None

    messages: list[Message]
    answer: str
    audit: list[VerificationResult]
    revisions: int
    usage: Usage
    attempts: list[dict[str, Any]]
    escalated: bool
    revisions_before_escalation: int
    usage_by_model: dict[str, Usage]
