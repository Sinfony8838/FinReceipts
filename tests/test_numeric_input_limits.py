"""Bound hostile input without silently returning a partial numeric audit."""

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from finreceipts.cli import main
from finreceipts.llm.client import FakeLLM
from finreceipts.models import Verdict
from finreceipts.runtime.store import verify_export
from finreceipts.verify.numbers import (
    MAX_NUMBER_MENTIONS,
    MAX_TEXT_CHARS,
    NumericInputError,
    extract_numbers,
)
from finreceipts.verify.verifier import check_value
from tests.test_run_api import client as _isolated_client
from tests.test_runtime import finish, setup_run

client = _isolated_client


@pytest.mark.parametrize("claimed,expected", [(1, 0), (1e308, 1e-300), (1e308, -1e308)])
def test_nonfinite_relative_error_is_unavailable_not_an_invalid_json_number(claimed, expected):
    verdict, relative_error, reasons = check_value(claimed, expected)
    assert verdict is Verdict.CONTRADICTED and relative_error is None
    assert reasons
    json.dumps({"verdict": verdict, "relative_error": relative_error}, allow_nan=False)


def test_zero_evidence_value_returns_a_complete_contradiction_response(client, aapl_lookup):
    zero = aapl_lookup["revenue"][2023].model_copy(update={"value": 0})
    client.app.state.fin.provider = lambda *_: {"revenue": {2023: zero}}
    response = client.post(
        "/api/audit",
        json={
            "text": "Apple FY2023 revenue was $1.",
            "ticker": "AAPL",
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert not result["all_numbers_supported"]
    assert result["audit"][0]["verdict"] == "contradicted"
    assert result["audit"][0]["relative_error"] is None
    assert result["audit"][0]["evidence"]["expected_value"] == 0
    json.dumps(result, allow_nan=False)


def test_maximum_normal_clause_batch_finishes_and_keeps_every_result():
    script = """
from finreceipts.verify.verifier import Verifier
from finreceipts.xbrl.metrics import lookup_from_companyfacts
from finreceipts.tools.edgar import EdgarClient
from finreceipts.tools.http_cache import JsonCache
from finreceipts.config import Settings
from pathlib import Path
import sys
with EdgarClient(Settings(), cache=JsonCache(Path(sys.argv[1])), mode='replay') as client:
    lookup = lookup_from_companyfacts(client.companyfacts(320193))
rows = Verifier(lookup).audit_text('Apple FY2023 revenue was $383.285 billion. ' * 256)
assert len(rows) == 256 and all(r.ok for r in rows)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).parent / "fixtures/sec")],
        check=True,
        timeout=10,
    )


@pytest.mark.parametrize("marker", ["", "USD", "-", "("])
def test_long_non_numeric_whitespace_finishes_in_an_isolated_process(marker):
    # The old regex exceeded three seconds with just 1024 spaces. The timeout
    # is a generous regression watchdog, not an application latency promise.
    script = (
        "import sys; from finreceipts.verify.numbers import extract_numbers; "
        "assert extract_numbers(sys.argv[1] + ' ' * 32000 + 'x') == []"
    )
    subprocess.run([sys.executable, "-c", script, marker], check=True, timeout=10)


@pytest.mark.parametrize(
    "literal", ["-$5 million", "(USD 1,234) million", "3 percent", "1.2万亿元"]
)
def test_whitespace_preserves_value_unit_precision_and_original_span(literal):
    (expected,) = extract_numbers(literal)
    text = " " * 32000 + literal
    (actual,) = extract_numbers(text)
    assert asdict(actual) | {"span": expected.span} == asdict(expected)
    assert text[slice(*actual.span)] == literal


def test_text_character_limit_is_inclusive_and_no_partial_list_is_returned():
    assert len(extract_numbers("x" * (MAX_TEXT_CHARS - 3) + " $1")) == 1
    with pytest.raises(NumericInputError, match=r"^audit_input_limit$"):
        extract_numbers("$1 " + "x" * MAX_TEXT_CHARS)


def test_numeric_count_limit_is_inclusive_and_ignored_years_do_not_consume_it():
    assert len(extract_numbers("2023 FY2023 $1 " * MAX_NUMBER_MENTIONS)) == MAX_NUMBER_MENTIONS
    with pytest.raises(NumericInputError, match=r"^audit_input_limit$"):
        extract_numbers("$1 " * (MAX_NUMBER_MENTIONS + 1))


@pytest.mark.parametrize(
    "text",
    ["9" * 400, "$" + "9" * 300 + " trillion", "-" + "9" * 400],
    ids=["overflow", "scale-overflow", "negative-overflow"],
)
def test_nonfinite_conversion_or_scaling_rejects_whole_extraction(text):
    with pytest.raises(NumericInputError, match=r"^numeric_out_of_range$"):
        extract_numbers("Revenue was $5. " + text)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("x" * (MAX_TEXT_CHARS + 1), "audit_input_limit"),
        ("$1 " * (MAX_NUMBER_MENTIONS + 1), "audit_input_limit"),
        ("$" + "9" * 400, "numeric_out_of_range"),
    ],
    ids=["characters", "mentions", "overflow"],
)
def test_api_explains_rejection_without_partial_results_or_leaking_text(client, text, code):
    response = client.post("/api/audit", json={"text": text, "ticker": "AAPL"})
    assert response.status_code == 422
    assert response.json()["code"] == code
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert "audit" not in response.json() and text not in response.text
    health = client.get("/health").json()
    assert health["execution"]["active"] == 0
    valid = client.post(
        "/api/audit", json={"text": "Apple FY2023 revenue was $383.285 billion.", "ticker": "AAPL"}
    )
    assert valid.status_code == 200 and valid.json()["all_numbers_supported"]


def test_cli_returns_actionable_error_without_writing_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FINRECEIPTS_OFFLINE", "1")
    monkeypatch.setenv("FINRECEIPTS_CACHE_DIR", str(Path(__file__).parent / "fixtures/sec"))
    source, report = tmp_path / "input.txt", tmp_path / "report.html"
    source.write_text("$" + "9" * 400)
    assert main(["audit", "--ticker", "AAPL", "--file", str(source), "--html", str(report)]) == 2
    captured = capsys.readouterr()
    assert "numeric_out_of_range" in captured.err and "Traceback" not in captured.err
    assert "999999999" not in captured.err and not report.exists()


@pytest.mark.parametrize("answer", ["$1 " * 257, "$" + "9" * 400], ids=["mentions", "overflow"])
def test_invalid_model_answer_cannot_complete_or_refund_dispatch(tmp_path, provider, answer):
    llm = FakeLLM(lambda *_: answer)
    service, doc = setup_run(tmp_path, provider, llm)
    out = finish(service, doc)
    assert out["status"] == "interrupted"
    assert out["dispatches"] == len(llm.calls) == 1
    assert not out["quality"]["numeric_gate_passed"]
    bundle = service.store.export(out["run_id"])
    verify_export(bundle)
    json.dumps(bundle, allow_nan=False)
