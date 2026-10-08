"""Merging must not erase enforcement boundaries or invent historical traces."""

import json
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime

import pytest
from scripts.merge_runs import main, merge_verification_metadata

from finreceipts.agent.pit_wiring import PitContext, SealedToolCache
from finreceipts.evals.controls import control_metadata
from finreceipts.evals.runner import RunConfig
from finreceipts.pit import Catalog, Policy


def summary(*, cache=False):
    context = PitContext(Policy(datetime(2024, 1, 1, tzinfo=UTC)), Catalog(()), {})
    return {
        "result_schema_version": 2,
        "config": asdict(RunConfig(model="fake", use_tools=cache, sealed_tool_cache=cache)),
        "verification_context": control_metadata(
            point_in_time=True,
            pit_context=context,
            cache=SealedToolCache(context) if cache else None,
        ),
        "api_attempts": {"fake": 2},
        "api_failures": {"fake": 1},
    }


def item(item_id):
    # A genuine old shape: no audit, audit schema or verification metadata.
    return {
        "id": item_id,
        "kind": "direct",
        "correct": True,
        "parsed": "100",
        "numbers": 1,
        "unsupported": 0,
        "revisions": 0,
        "input_tokens": 10,
        "output_tokens": 5,
        "llm_calls": 1,
        "latency_s": 1.0,
        "answer": "100",
        "attempts": [{"answer": "100", "numbers": 1, "supported": 1}],
    }


def write_run(path, metadata, rows):
    path.mkdir()
    (path / "summary.json").write_text(json.dumps(metadata), encoding="utf-8")
    (path / "items.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    return path


def merge(monkeypatch, out, *runs):
    monkeypatch.setattr(sys, "argv", ["merge_runs.py", str(out), *map(str, runs)])
    main()
    return json.loads((out / "summary.json").read_text())


@pytest.mark.parametrize(
    "path,value",
    [
        (("policy", "cutoff_utc"), "2025-01-01T00:00:00+00:00"),
        (("policy", "mode"), "acceptance_proxy"),
        (("policy", "version"), "pit-next"),
        (("policy", "acceptance_lag_us"), 1),
        (("policy_fingerprint",), "different-policy"),
        (("catalog_fingerprint",), "different-catalog"),
        (("catalog_filings",), 1),
        (("prefer",), "latest"),
        (("fact_selection",), "legacy_filed_date"),
        (("cutoff_source",), "dataset_as_of"),
        (("provenance",), "independently_authenticated"),
        (("ground_truth_pit_validated",), True),
        (("cn_pit_supported",), True),
        (("future_enforcement_label",), "different"),
    ],
)
def test_incompatible_contexts_fail_without_touching_output(tmp_path, monkeypatch, path, value):
    first = summary()
    second = deepcopy(first)
    target = second["verification_context"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    runs = [
        write_run(tmp_path / "a", first, [item("a")]),
        write_run(tmp_path / "b", second, [item("b")]),
    ]
    out = write_run(tmp_path / "out", {"keep": "summary"}, [item("keep")])
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json*")}
    with pytest.raises(ValueError, match="verification contexts differ"):
        merge(monkeypatch, out, *runs)
    assert all(p.read_bytes() == content for p, content in before.items())


def test_compatible_pit_context_and_original_traces_survive(tmp_path, monkeypatch):
    metadata = summary()
    rows = [item("a"), item("b")]
    rows[0].update(audit=None, audit_schema_version=1)
    rows[1].update(audit=[], audit_schema_version=1)
    rows[1]["attempts"][0]["audit"] = []
    for row in rows:
        row["verification_context"] = {
            **metadata["verification_context"],
            "dataset_as_of": "2023-11-03",
        }
    runs = [write_run(tmp_path / row["id"], metadata, [row]) for row in rows]
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json*")}
    out = tmp_path / "out"
    merged = merge(monkeypatch, out, *runs)
    assert merged["result_schema_version"] == 2
    assert merged["verification_context"] == metadata["verification_context"]
    assert merged["n_items"] == 2 and merged["accuracy"] == 1.0
    assert merged["api_attempts"] == {"fake": 4}
    assert merged["api_failures"] == {"fake": 2}
    assert (out / "items.jsonl").read_bytes() == b"".join(
        before[run / "items.jsonl"] for run in runs
    )
    assert all(p.read_bytes() == content for p, content in before.items())


def test_cache_counters_are_summed_without_changing_enforcement_or_inputs(tmp_path, monkeypatch):
    first, second = summary(cache=True), summary(cache=True)
    first["verification_context"]["tool_cache"].update(hits=2, rejected=1)
    second["verification_context"]["tool_cache"].update(hits=3, rejected=4)
    original = deepcopy([first, second])
    combined = merge_verification_metadata([first, second])
    assert [first, second] == original
    expected_cache = {**first["verification_context"]["tool_cache"], "hits": 5, "rejected": 5}
    assert combined["verification_context"]["tool_cache"] == expected_cache
    assert combined["tool_cache_aggregation"] == "sum_of_independent_run_counters"
    runs = [
        write_run(tmp_path / "a", first, [item("a")]),
        write_run(tmp_path / "b", second, [item("b")]),
    ]
    out = tmp_path / "out"
    merge(monkeypatch, out, *runs)
    third = write_run(tmp_path / "c", first, [item("c")])
    merged_again = merge(monkeypatch, tmp_path / "again", out, third)
    assert merged_again["verification_context"]["tool_cache"]["hits"] == 7
    assert merged_again["verification_context"]["tool_cache"]["rejected"] == 6


@pytest.mark.parametrize("key,value", [("scope", "shared"), ("integrity", "authenticated")])
def test_cache_trust_and_scope_are_compatibility_boundaries(key, value):
    first, second = summary(cache=True), summary(cache=True)
    second["verification_context"]["tool_cache"][key] = value
    with pytest.raises(ValueError, match="verification contexts differ"):
        merge_verification_metadata([first, second])


@pytest.mark.parametrize("value", [None, -1, True, 1.5, "1"])
def test_invalid_cache_counters_are_not_invented_or_summed(value):
    metadata = summary(cache=True)
    metadata["verification_context"]["tool_cache"]["hits"] = value
    with pytest.raises(ValueError, match="nonnegative integer"):
        merge_verification_metadata([metadata, deepcopy(metadata)])


@pytest.mark.parametrize("version", [None, 1])
def test_matching_legacy_runs_do_not_gain_schema_or_trace_claims(tmp_path, monkeypatch, version):
    metadata = {"config": {"model": "fake", "point_in_time": True}}
    if version is not None:
        metadata["result_schema_version"] = version
    rows = [item("a"), item("b")]
    runs = [write_run(tmp_path / row["id"], metadata, [row]) for row in rows]
    merged = merge(monkeypatch, tmp_path / "out", *runs)
    assert "verification_context" not in merged and "tool_cache_aggregation" not in merged
    assert merged.get("result_schema_version") == version
    assert ("result_schema_version" in merged) == (version is not None)
    assert [
        json.loads(line) for line in (tmp_path / "out" / "items.jsonl").read_text().splitlines()
    ] == rows


def test_missing_and_explicit_legacy_versions_are_not_equivalent():
    first = {"config": {"model": "fake"}}
    second = {**first, "result_schema_version": 1}
    with pytest.raises(ValueError, match="result schemas"):
        merge_verification_metadata([first, second])


def test_legacy_and_modern_cannot_mix_even_with_identical_config():
    modern = summary()
    legacy = {"config": modern["config"]}
    with pytest.raises(ValueError, match="result schemas"):
        merge_verification_metadata([legacy, modern])


@pytest.mark.parametrize("version", [None, True, 2.0, "2", 3])
def test_unknown_or_malformed_explicit_schema_is_rejected(version):
    metadata = {**summary(), "result_schema_version": version}
    with pytest.raises(ValueError, match="result_schema_version"):
        merge_verification_metadata([metadata, deepcopy(metadata)])


@pytest.mark.parametrize(
    "path",
    [
        ("verification_context",),
        ("verification_context", "policy"),
        ("verification_context", "policy", "cutoff_utc"),
        ("verification_context", "catalog_fingerprint"),
        ("verification_context", "provenance"),
        ("verification_context", "tool_cache"),
        ("verification_context", "tool_cache", "hits"),
        ("verification_context", "tool_cache", "integrity"),
    ],
)
def test_identically_incomplete_modern_metadata_is_not_legacy_compatibility(path):
    metadata = summary(cache=True)
    target = metadata
    for key in path[:-1]:
        target = target[key]
    target.pop(path[-1])
    with pytest.raises(ValueError):
        merge_verification_metadata([metadata, deepcopy(metadata)])


@pytest.mark.parametrize("flag", ["guard_tools", "mark_untrusted", "sealed_tool_cache"])
def test_legacy_metadata_cannot_claim_new_controls(flag):
    metadata = {"config": {"model": "fake", flag: True}}
    with pytest.raises(ValueError, match="legacy result schema"):
        merge_verification_metadata([metadata, deepcopy(metadata)])


@pytest.mark.parametrize("duplicate", [True, False])
def test_original_merge_guards_remain_in_force(tmp_path, monkeypatch, duplicate):
    first, second = summary(), summary()
    if not duplicate:
        second["config"]["model"] = "different"
    a = write_run(tmp_path / "a", first, [item("a")])
    b = write_run(tmp_path / "b", second, [item("a" if duplicate else "b")])
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="duplicate item ids" if duplicate else "configs differ"):
        merge(monkeypatch, out, a, b)
    assert not out.exists()


def test_output_cannot_overwrite_a_historical_source(tmp_path, monkeypatch):
    path = write_run(tmp_path / "run", {"config": {"model": "fake"}}, [item("a")])
    before = {p: p.read_bytes() for p in path.iterdir()}
    with pytest.raises(ValueError, match="overwrite an input"):
        merge(monkeypatch, path, path)
    assert all(p.read_bytes() == content for p, content in before.items())
