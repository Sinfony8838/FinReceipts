"""Deterministic fake LLM replies for key-free demos (not evaluation numbers)."""

from __future__ import annotations

from collections.abc import Sequence

from finreceipts.llm.client import LLMResponse, Message, ToolSpec

# Hand-written NVDA FY2025 summary used by the offline audit demo / GIF.
# Five numbers match SEC XBRL; "80%" gross margin is intentionally unsupported.
DEMO_NVDA_ANSWER = (
    "NVIDIA's revenue rose to $130.5 billion in fiscal 2025 from $60.9 billion, "
    "up 114%. Net income was $72.9 billion and diluted EPS was $2.94. "
    "Gross margin reached 80%.\n"
    "ANSWER: $130.5 billion"
)

DEMO_AAPL_WRONG = (
    "Apple reported revenue of $390.0 billion for fiscal 2023.\nANSWER: $390.0 billion"
)

DEMO_AAPL_RIGHT = (
    "Apple's revenue for the fiscal year ended September 30, 2023 was "
    "$383.3 billion.\n"
    "ANSWER: $383.3 billion"
)


def demo_responder(
    messages: Sequence[Message], model: str, tools: Sequence[ToolSpec] = ()
) -> LLMResponse | str:
    """Produce a short canned answer so the SSE demo works without an API key.

    On the first answer attempt for an Apple-like question, return a wrong figure
    so verify→revise can demonstrate a red unsupported claim, then a corrected one.
    """
    del model, tools
    blob = " ".join(str(m.get("content", "")) for m in messages).lower()
    # Revision turn: feedback mentions unsupported / verifier
    if "verifier checked" in blob or "unsupported" in blob or "revise your answer" in blob:
        if "apple" in blob or "aapl" in blob:
            return DEMO_AAPL_RIGHT
        if "nvidia" in blob or "nvda" in blob:
            # Keep the unsupported 80% so the receipts report still shows red.
            return DEMO_NVDA_ANSWER
        return DEMO_AAPL_RIGHT
    if "nvidia" in blob or "nvda" in blob:
        return DEMO_NVDA_ANSWER
    if "apple" in blob or "aapl" in blob:
        return DEMO_AAPL_WRONG
    return (
        "I do not have a verified figure for that question in demo mode. "
        "Try asking about Apple FY2023 revenue or NVIDIA FY2025 results.\n"
        "ANSWER: n/a"
    )
