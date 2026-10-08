"""Merge per-item results of the same configuration run on disjoint item sets.

Usage: python scripts/merge_runs.py OUT_DIR RUN_DIR [RUN_DIR ...] [--prices prices.json]
Recomputes the summary from the union of items.jsonl (duplicate ids are an error).
Schema and enforcement metadata must match; independent cache counters are summed.
Original item records are retained, including the absence of historical audit traces.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from finreceipts.evals.runner import ItemResult, summarize


def _verification_metadata(summary: dict[str, Any]) -> dict[str, Any]:
    """Extract comparable metadata without treating absent values as defaults."""
    metadata = {
        key: deepcopy(summary[key])
        for key in ("result_schema_version", "verification_context")
        if key in summary
    }
    version = metadata.get("result_schema_version")
    if "result_schema_version" in metadata and (type(version) is not int or version not in {1, 2}):
        raise ValueError("unsupported result_schema_version")
    if version != 2:
        if "verification_context" in metadata or any(
            summary["config"].get(key)
            for key in ("guard_tools", "mark_untrusted", "sealed_tool_cache")
        ):
            raise ValueError("legacy result schema cannot establish verification_context")
        return metadata

    context = metadata.get("verification_context")
    if not isinstance(context, dict) or not {
        "fact_selection",
        "ground_truth_pit_validated",
    }.issubset(context):
        raise ValueError("schema 2 requires verification_context")
    if context["fact_selection"] not in {"explicit_catalog_pit", "legacy_filed_date", "unfiltered"}:
        raise ValueError("unsupported verification_context fact_selection")
    if context["fact_selection"] == "explicit_catalog_pit":
        required = {
            "policy",
            "policy_fingerprint",
            "catalog_fingerprint",
            "catalog_filings",
            "prefer",
            "cutoff_source",
            "provenance",
            "cn_pit_supported",
        }
        policy = context.get("policy")
        if (
            not required.issubset(context)
            or not isinstance(policy, dict)
            or not {"mode", "version", "cutoff_utc", "acceptance_lag_us"}.issubset(policy)
        ):
            raise ValueError("incomplete explicit PIT verification_context")

    if summary["config"].get("sealed_tool_cache") and "tool_cache" not in context:
        raise ValueError("sealed_tool_cache requires tool_cache metadata")
    if "tool_cache" in context:
        cache = context["tool_cache"]
        if not isinstance(cache, dict) or not {"scope", "integrity"}.issubset(cache):
            raise ValueError("incomplete tool_cache metadata")
        for key in ("hits", "rejected"):
            value = cache.pop(key, None)
            if type(value) is not int or value < 0:
                raise ValueError(f"tool_cache {key} must be a nonnegative integer")
    return metadata


def merge_verification_metadata(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Require exact metadata equality except for known run-local counters.

    Missing legacy metadata only matches the same missing metadata/schema, never
    an explicit modern context. Compare every label, including future fields, so
    provenance or enforcement distinctions cannot disappear during a merge.
    """
    metadata = [_verification_metadata(summary) for summary in summaries]
    signatures = [json.dumps(value, sort_keys=True, allow_nan=False) for value in metadata]
    if any(signature != signatures[0] for signature in signatures):
        raise ValueError("result schemas or verification contexts differ")
    merged = metadata[0]
    context = merged.get("verification_context", {})
    if "tool_cache" in context:
        for key in ("hits", "rejected"):
            context["tool_cache"][key] = sum(
                summary["verification_context"]["tool_cache"][key] for summary in summaries
            )
        merged["tool_cache_aggregation"] = "sum_of_independent_run_counters"
    return merged


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--prices")
    a = ap.parse_args()
    results: list[ItemResult] = []
    item_lines: list[str] = []
    summaries, attempts, failures = [], {}, {}
    out = Path(a.out)
    if any(out.resolve() == Path(run).resolve() for run in a.runs):
        raise ValueError("output must not overwrite an input run")
    for run in a.runs:
        d = Path(run)
        s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        summaries.append(s)
        for k, v in (s.get("api_attempts") or {}).items():
            attempts[k] = attempts.get(k, 0) + v
        for k, v in (s.get("api_failures") or {}).items():
            failures[k] = failures.get(k, 0) + v
        for line in (d / "items.jsonl").read_text(encoding="utf-8").splitlines():
            results.append(ItemResult(**json.loads(line)))
            item_lines.append(line)
    ids = [r.id for r in results]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate item ids across runs")
    if any(s["config"] != summaries[0]["config"] for s in summaries):
        raise ValueError("configs differ")
    metadata = merge_verification_metadata(summaries)
    prices = None
    if a.prices:
        raw = json.loads(Path(a.prices).read_text(encoding="utf-8"))
        prices = {m: (float(v[0]), float(v[1])) for m, v in raw.items()}
    summary = {
        "config": summaries[0]["config"],
        **metadata,
        **summarize(results, prices),
        "api_attempts": attempts,
        "api_failures": failures,
        "merged_from": [str(r) for r in a.runs],
    }
    out.mkdir(parents=True, exist_ok=True)
    with (out / "items.jsonl").open("w", encoding="utf-8") as fh:
        for line in item_lines:
            fh.write(line + "\n")
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"{out}: n={len(results)} accuracy={summary['accuracy']:.3f}")


if __name__ == "__main__":
    main()
