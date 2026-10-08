"""Explicit evaluation controls, separate from the legacy filed-date filter."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from finreceipts.agent.pit_wiring import (
    FactsProvider,
    PitContext,
    SealedToolCache,
    pit_lookup_provider,
)
from finreceipts.agent.tools import LookupProvider
from finreceipts.evals.dataset import EvalItem
from finreceipts.tools.akshare_a import is_a_share_code


def validate_controls(
    items: Iterable[EvalItem],
    *,
    point_in_time: bool,
    use_tools: bool,
    guard_tools: bool,
    mark_untrusted: bool,
    sealed_tool_cache: bool,
    pit_context: PitContext | None,
    facts_provider: FactsProvider | None,
) -> None:
    """Fail before any model call or output-file truncation on invalid controls."""
    if any((guard_tools, mark_untrusted, sealed_tool_cache)) and not use_tools:
        raise ValueError("tool controls require use_tools/--tools")
    if sealed_tool_cache and pit_context is None:
        raise ValueError("sealed tool cache requires an explicit PIT context/catalog")
    if (pit_context is None) != (facts_provider is None):
        raise ValueError("PIT context and raw facts provider must be supplied together")
    if pit_context is None:
        return
    if not point_in_time:
        raise ValueError("explicit PIT context conflicts with disabled point-in-time filtering")
    if pit_context.prefer not in {"original", "latest"}:
        raise ValueError("PIT prefer must be original or latest")
    for item in items:
        if item.market != "US" or is_a_share_code(item.ticker):
            raise ValueError(
                "explicit PIT evaluation supports US SEC items only; CN is not protected"
            )


def controlled_provider(
    provider: LookupProvider,
    pit_context: PitContext | None,
    facts_provider: FactsProvider | None,
) -> LookupProvider:
    """Never silently fall back to a legacy provider when PIT was requested."""
    if pit_context is None:
        if facts_provider is not None:
            raise ValueError("raw facts provider requires a PIT context")
        return provider
    if facts_provider is None:
        raise ValueError("PIT context requires a raw facts provider")
    strict = pit_lookup_provider(facts_provider, pit_context)

    def lookup(ticker, as_of):
        # Guard the provider too: a model must not bypass dataset validation by
        # requesting a CN code through the alternate tool when scope is off.
        if is_a_share_code(ticker):
            raise ValueError("CN facts are not supported by the explicit SEC PIT policy")
        return strict(ticker, as_of)

    return lookup


def control_metadata(
    *, point_in_time: bool, pit_context: PitContext | None, cache: SealedToolCache | None
) -> dict[str, Any]:
    """Persist what was actually enforced without overstating source trust."""
    out: dict[str, Any] = {
        "fact_selection": (
            "explicit_catalog_pit"
            if pit_context is not None
            else "legacy_filed_date"
            if point_in_time
            else "unfiltered"
        ),
        "ground_truth_pit_validated": False,
    }
    if pit_context is not None:
        out.update(
            policy=pit_context.policy.descriptor(),
            policy_fingerprint=pit_context.policy.fingerprint,
            catalog_fingerprint=pit_context.catalog.fingerprint,
            catalog_filings=len(pit_context.catalog.filings),
            prefer=pit_context.prefer,
            cutoff_source="explicit_host_setting_not_dataset_as_of",
            provenance="host_supplied_not_independently_authenticated",
            cn_pit_supported=False,
        )
    if cache is not None:
        out["tool_cache"] = {
            "scope": "in_memory_run_only",
            "integrity": "unsigned_hash_envelope_not_authentication",
            "hits": cache.hits,
            "rejected": cache.rejected,
        }
    return out
