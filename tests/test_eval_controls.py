"""Control/preflight tests do not require LangGraph or a model service."""

import json
from dataclasses import asdict, replace
from datetime import UTC, date, datetime

import pytest

from finreceipts.agent.pit_wiring import PitContext, SealedToolCache
from finreceipts.cli import _eval_pit_context, build_parser, main
from finreceipts.evals.controls import control_metadata, controlled_provider, validate_controls
from finreceipts.evals.dataset import EvalItem, write_jsonl
from finreceipts.evals.runner import ItemResult, RunConfig, run_eval
from finreceipts.llm.client import FakeLLM
from finreceipts.pit import Catalog, Filing, Policy, TimeEvidence, content_hash, fact_hash
from tests.conftest import make_fact

A = "0000000001-23-000001"
B = "0000000001-24-000001"
ORIGINAL = make_fact(100e9, accn=A)
LATER = make_fact(110e9, accn=B, filed="2024-11-03")
BLOBS = {A: b"synthetic original", B: b"synthetic comparative"}
CATALOG = Catalog(
    tuple(
        Filing(
            fact.accn,
            1,
            "10-K",
            content_hash(BLOBS[fact.accn]),
            first_observed=TimeEvidence(stamp),
            observation_source="synthetic-test-only",
            fact_hashes=(fact_hash(fact),),
        )
        for fact, stamp in [(ORIGINAL, "2023-11-03T00:00:00Z"), (LATER, "2024-11-03T00:00:00Z")]
    )
)
CONTEXT = PitContext(Policy(datetime(2024, 1, 1, tzinfo=UTC)), CATALOG, BLOBS)


def raw_facts(ticker):
    return {"Revenues": [ORIGINAL, LATER]}


def legacy(ticker, as_of):
    return {"revenue": {2023: LATER}}


def item(**changes):
    values = dict(
        id="synthetic-1",
        ticker="TST",
        entity="Test",
        metric="revenue",
        kind="direct",
        fiscal_year=2023,
        period_end=date(2023, 9, 30),
        question="Test revenue?",
        expected_value=100e9,
        expected_unit="USD",
        formula="Revenues[2023]",
        evidence=[],
        as_of=date(2023, 11, 3),
    )
    values.update(changes)
    return EvalItem(**values)


def kwargs(**changes):
    values = dict(
        point_in_time=True,
        use_tools=False,
        guard_tools=False,
        mark_untrusted=False,
        sealed_tool_cache=False,
        pit_context=None,
        facts_provider=None,
    )
    values.update(changes)
    return values


def test_defaults_do_not_enable_strict_pit_or_tool_controls():
    cfg = RunConfig(model="fake")
    assert cfg.point_in_time and not any(
        (cfg.guard_tools, cfg.mark_untrusted, cfg.sealed_tool_cache)
    )
    args = build_parser().parse_args(["eval"])
    assert _eval_pit_context(args) is None
    assert controlled_provider(legacy, None, None) is legacy
    validate_controls([item()], **kwargs())
    metadata = control_metadata(point_in_time=True, pit_context=None, cache=None)
    assert metadata["fact_selection"] == "legacy_filed_date"
    assert "policy" not in metadata
    assert not metadata["ground_truth_pit_validated"]
    metadata = control_metadata(point_in_time=False, pit_context=None, cache=None)
    assert metadata["fact_selection"] == "unfiltered"


@pytest.mark.parametrize(
    "change",
    [
        {"guard_tools": True},
        {"mark_untrusted": True},
        {"sealed_tool_cache": True},
        {"sealed_tool_cache": True, "use_tools": True},
        {"pit_context": CONTEXT},
        {"facts_provider": raw_facts},
        {"pit_context": CONTEXT, "facts_provider": raw_facts, "point_in_time": False},
        {"pit_context": replace(CONTEXT, prefer="invalid"), "facts_provider": raw_facts},
    ],
)
def test_invalid_control_combinations_fail(change):
    with pytest.raises(ValueError):
        validate_controls([item()], **kwargs(**change))


@pytest.mark.parametrize("market,ticker", [("CN", "600519"), ("US", "600519"), ("CN", "TST")])
def test_explicit_pit_refuses_cn_dataset(market, ticker):
    with pytest.raises(ValueError, match="US SEC"):
        validate_controls(
            [item(market=market, ticker=ticker)],
            **kwargs(pit_context=CONTEXT, facts_provider=raw_facts),
        )


def test_provider_uses_explicit_policy_and_never_legacy_fallback():
    provider = controlled_provider(legacy, CONTEXT, raw_facts)
    # Even a later legacy date cannot move the explicitly supplied cutoff.
    assert provider("TST", date(2099, 1, 1))["revenue"][2023].value == 100e9
    with pytest.raises(ValueError, match="CN"):
        provider("600519", None)
    empty = replace(CONTEXT, catalog=Catalog(()))
    assert controlled_provider(legacy, empty, raw_facts)("TST", None) == {}
    tampered = replace(CONTEXT, blobs={A: b"wrong", B: BLOBS[B]})
    assert controlled_provider(legacy, tampered, raw_facts)("TST", None) == {}
    for context, facts in [(CONTEXT, None), (None, raw_facts)]:
        with pytest.raises(ValueError):
            controlled_provider(legacy, context, facts)


def test_metadata_identifies_policy_and_unsigned_cache():
    cache = SealedToolCache(CONTEXT)
    meta = control_metadata(point_in_time=True, pit_context=CONTEXT, cache=cache)
    assert meta["policy_fingerprint"] == CONTEXT.policy.fingerprint
    assert meta["catalog_fingerprint"] == CATALOG.fingerprint
    assert not meta["ground_truth_pit_validated"] and not meta["cn_pit_supported"]
    assert "host_supplied" in meta["provenance"]
    assert "unsigned" in meta["tool_cache"]["integrity"]
    json.dumps(meta, allow_nan=False)


@pytest.mark.parametrize(
    "args",
    [
        ["--pit-cutoff", "2024-01-01T00:00:00Z"],
        ["--pit-policy", "observed_replay"],
        ["--pit-prefer", "latest"],
        ["--pit-acceptance-lag-seconds", "0"],
        ["--pit-catalog", "missing.json"],
        ["--pit-catalog", "missing.json", "--no-pit"],
        ["--pit-catalog", "missing.json", "--pit-cutoff", "2024-01-01"],
        ["--pit-catalog", "missing.json", "--pit-cutoff", "2024-01-01T00:00:00"],
        [
            "--pit-catalog",
            "missing.json",
            "--pit-cutoff",
            "2024-01-01T00:00:00Z",
            "--pit-acceptance-lag-seconds",
            "nan",
        ],
        [
            "--pit-catalog",
            "missing.json",
            "--pit-cutoff",
            "2024-01-01T00:00:00Z",
            "--pit-acceptance-lag-seconds",
            "-1",
        ],
    ],
)
def test_cli_pit_options_refuse_invalid_combinations(args):
    parsed = build_parser().parse_args(["eval", *args])
    with pytest.raises(ValueError):
        _eval_pit_context(parsed)


def test_cli_loads_empty_catalog_without_inventing_provenance(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"schema_version": 1, "filings": []}))
    parsed = build_parser().parse_args(
        [
            "eval",
            "--pit-catalog",
            str(path),
            "--pit-cutoff",
            "2024-01-01T08:00:00+08:00",
            "--pit-policy",
            "acceptance_proxy",
            "--pit-acceptance-lag-seconds",
            "30",
        ]
    )
    context = _eval_pit_context(parsed)
    assert context.policy.cutoff == datetime(2024, 1, 1, tzinfo=UTC)
    assert context.policy.acceptance_lag.total_seconds() == 30
    assert not context.catalog.filings and not context.blobs


@pytest.mark.parametrize("args", [["--guard-tools"], ["--pit-cutoff", "2024-01-01T00:00:00Z"]])
def test_cli_validation_happens_before_llm_or_output_write(tmp_path, monkeypatch, args):
    import finreceipts.cli as cli

    dataset = tmp_path / "data.jsonl"
    write_jsonl([item()], dataset)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "items.jsonl").write_text("do not truncate")

    def forbidden(*args, **kwargs):
        raise AssertionError("model client must not be constructed")

    monkeypatch.setattr(cli, "make_client", forbidden)
    assert (
        main(
            [
                "eval",
                "--dataset",
                str(dataset),
                "--model",
                "fake",
                "--out-dir",
                str(out_dir),
                *args,
            ]
        )
        == 2
    )
    assert (out_dir / "items.jsonl").read_text() == "do not truncate"


def test_runner_preflight_before_graph_import_and_output_truncation(tmp_path):
    out = tmp_path / "items.jsonl"
    out.write_text("keep")
    llm = FakeLLM(lambda *_: "must not run")
    with pytest.raises(ValueError):
        run_eval([item()], llm, legacy, RunConfig(model="fake", guard_tools=True), out_dir=tmp_path)
    assert not llm.calls and out.read_text() == "keep"


def test_missing_historical_trace_is_not_fabricated():
    historic = dict(
        id="old",
        kind="direct",
        correct=True,
        parsed="100",
        numbers=1,
        unsupported=0,
        revisions=0,
        input_tokens=1,
        output_tokens=1,
        llm_calls=1,
        latency_s=1.0,
        answer="100",
        attempts=[{"answer": "100", "numbers": 1, "supported": 1}],
    )
    result = ItemResult(**historic)
    assert result.audit is None and "audit" not in result.attempts[0]
    assert asdict(result)["audit"] is None


def test_cache_separates_selection_preferences_and_rejects_replant():
    original = SealedToolCache(CONTEXT)
    latest = SealedToolCache(replace(CONTEXT, prefer="latest"), original.store)
    args = {"ticker": "TST", "metric": "revenue", "fiscal_year": 2023}
    original.put("get_financial_fact", args, '{"value":100}')
    assert original.get("get_financial_fact", args) == '{"value":100}'
    assert original.key("get_financial_fact", args) != latest.key("get_financial_fact", args)
    assert latest.get("get_financial_fact", args) is None
    env = original.store[original.key("get_financial_fact", args)]
    latest.store[latest.key("get_financial_fact", args)] = env
    assert latest.get("get_financial_fact", args) is None and latest.rejected == 1
    other_args = {**args, "fiscal_year": 2022}
    original.store[original.key("get_financial_fact", other_args)] = env
    assert original.get("get_financial_fact", other_args) is None and original.rejected == 1


@pytest.mark.parametrize("ticker", ["600519.SH", "SH600519", " 600519 ", "sz000001", "000001.SZ"])
def test_all_supported_cn_spellings_refused_before_execution(ticker):
    with pytest.raises(ValueError, match="US SEC"):
        validate_controls(
            [item(ticker=ticker)], **kwargs(pit_context=CONTEXT, facts_provider=raw_facts)
        )
    with pytest.raises(ValueError, match="CN"):
        controlled_provider(legacy, CONTEXT, raw_facts)(ticker, None)
