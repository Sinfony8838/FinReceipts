"""Safety and selection checks for the opt-in paid experiment helper."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from finreceipts.evals.dataset import read_jsonl
from finreceipts.llm.client import FatalAPIError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("workflow_eval", ROOT / "scripts/workflow_eval.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def request(**updates):
    body = {"model": module.MODEL, "max_tokens": 1024, "messages": []}
    body.update(updates)
    return httpx.Request("POST", "https://example.test/messages", json=body)


def test_selection_is_balanced_stable_and_order_independent():
    items = read_jsonl(ROOT / "evals/datasets/xbrl_numeric_v0.jsonl")
    selected = module.select(items)
    assert selected == module.select(list(reversed(items)))
    assert len({i.id for i in selected}) == 30
    assert {k: sum(i.kind == k for i in selected) for k in {i.kind for i in selected}} == {
        "direct": 10,
        "ratio": 10,
        "yoy_growth": 10,
    }


def test_budget_rejects_before_network_and_preserves_unknown(tmp_path):
    ledger = module.Ledger(tmp_path / "ledger.json")
    req = request()
    ledger.request(req)
    reserved = ledger.rows[0]["held_cny"]
    ledger.response(httpx.Response(500, request=req, json={"error": "unknown"}))
    assert ledger.rows[0]["held_cny"] == reserved
    ledger.rows[0]["held_cny"] = 8
    with pytest.raises(FatalAPIError, match="ceiling"):
        ledger.request(request())
    assert len(ledger.rows) == 1
    with pytest.raises(ValueError, match="overwrite"):
        module.Ledger(ledger.path)


def test_usage_reconciles_and_protocol_mismatch_stops(tmp_path):
    ledger = module.Ledger(tmp_path / "ledger.json")
    req = request()
    ledger.request(req)
    response = {
        "usage": {"input_tokens": 100, "output_tokens": 200},
        "content": [{"type": "thinking"}],
        "stop_reason": "end_turn",
    }
    with pytest.raises(FatalAPIError, match="protocol"):
        ledger.response(httpx.Response(200, request=req, json=response))
    rows = json.loads(ledger.path.read_text())
    assert rows[0]["held_cny"] == pytest.approx(0.00189)
    assert rows[0]["block_types"] == ["thinking"]


@pytest.mark.parametrize("updates", [{"model": "other"}, {"tools": [{}]}, {"max_tokens": 4096}])
def test_disallowed_request_never_reserved(tmp_path, updates):
    ledger = module.Ledger(tmp_path / "ledger.json")
    with pytest.raises(FatalAPIError):
        ledger.request(request(**updates))
    assert ledger.rows == []


def test_source_check_rejects_altered_answer_or_evidence():
    item = read_jsonl(ROOT / "evals/datasets/xbrl_numeric_v0.jsonl")[0]
    e = item.evidence[0]
    raw = {
        "facts": {
            "us-gaap": {
                e.concept: {
                    "units": {
                        e.unit: [
                            {
                                "val": e.value,
                                "accn": e.accn,
                                "start": e.start.isoformat() if e.start else None,
                                "end": e.end.isoformat(),
                                "filed": e.filed.isoformat(),
                                "form": e.form,
                            }
                        ]
                    }
                }
            }
        }
    }
    module.validate_item(item, raw)
    with pytest.raises(ValueError, match="answer key"):
        module.validate_item(item.model_copy(update={"expected_value": 1}), raw)
    raw["facts"]["us-gaap"][e.concept]["units"][e.unit][0]["val"] = 1
    with pytest.raises(ValueError, match="source row"):
        module.validate_item(item, raw)


def test_hook_stop_survives_sdk_error_translation(tmp_path):
    ledger = module.Ledger(tmp_path / "ledger.json")

    class TranslatedFailure:
        def complete(self, *_args, **_kwargs):
            ledger.stopped = "protocol mismatch"
            raise RuntimeError("SDK translated hook failure")

    guarded = module.GuardedClient(TranslatedFailure(), ledger)
    with pytest.raises(FatalAPIError, match="protocol mismatch"):
        guarded.complete([], model=module.MODEL)


def test_each_retry_has_its_own_reservation(tmp_path):
    ledger = module.Ledger(tmp_path / "ledger.json")
    first = request()
    ledger.request(first)
    ledger.response(httpx.Response(503, request=first, json={"error": "unavailable"}))
    unknown = ledger.rows[0]["held_cny"]
    second = request()
    ledger.request(second)
    ledger.response(
        httpx.Response(
            200,
            request=second,
            json={
                "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30},
                "content": [{"type": "text"}],
                "stop_reason": "end_turn",
            },
        )
    )
    assert len(ledger.rows) == 2
    assert ledger.rows[0]["held_cny"] == unknown
    assert ledger.rows[1]["held_cny"] == pytest.approx((40 * 2.1 + 20 * 8.4) / 1e6)


def test_usage_above_predeclared_bound_stops(tmp_path):
    ledger = module.Ledger(tmp_path / "ledger.json")
    req = request()
    ledger.request(req)
    with pytest.raises(FatalAPIError, match="bounds"):
        ledger.response(
            httpx.Response(
                200,
                request=req,
                json={
                    "usage": {"input_tokens": 999999, "output_tokens": 20},
                    "content": [],
                },
            )
        )
    assert ledger.stopped
