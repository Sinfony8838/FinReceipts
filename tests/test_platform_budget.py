import json

import pytest

from finreceipts.llm.client import AnthropicCompatClient, LLMResponse, ToolCall
from finreceipts.platform.eval_budget import EvalBudget
from finreceipts.runtime.codec import decode_state, encode_state


def test_unknown_eval_attempt_survives_restart_and_cannot_reset_budget(tmp_path):
    ledger = EvalBudget(tmp_path)
    attempt = ledger.reserve(9_900_000, "case", "minimax")
    ledger.close()
    ledger = EvalBudget(tmp_path)
    try:
        assert ledger.total() == 9_900_000
        with pytest.raises(RuntimeError, match="exhausted"):
            ledger.reserve(200_000, "next", "minimax")
        ledger.settle(attempt, 1000, {"output_tokens": 1})
        assert ledger.total() == 1000
        with pytest.raises(RuntimeError):
            ledger.settle(attempt, 0, {})
    finally:
        ledger.close()


def test_evaluation_has_one_writer(tmp_path):
    ledger = EvalBudget(tmp_path)
    try:
        with pytest.raises(FileExistsError):
            EvalBudget(tmp_path)
    finally:
        ledger.close()


def test_legacy_checkpoint_keeps_native_thinking_and_tool_pairing():
    blocks = [
        {"type": "thinking", "thinking": "opaque", "signature": "original"},
        {"type": "tool_use", "id": "c1", "name": "get", "input": {"metric": "revenue"}},
    ]
    response = LLMResponse(
        "", [ToolCall("c1", "get", {"metric": "revenue"})], native_content=blocks
    )
    state = {
        "messages": [
            response.assistant_message(),
            {"role": "tool", "tool_call_id": "c1", "content": "123 USD"},
        ]
    }
    restored = decode_state(json.loads(json.dumps(encode_state(state))))
    wire = AnthropicCompatClient._to_wire(restored["messages"])
    assert wire[0]["content"] == blocks
    assert wire[1]["content"][0]["tool_use_id"] == "c1"
