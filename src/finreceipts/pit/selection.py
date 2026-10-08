"""Select only verified rows before choosing original/latest reporting vintage."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from finreceipts.models import Fact
from finreceipts.xbrl.facts import fiscal_year_label, is_annual

from .policy import Catalog, Decision, Policy, fact_hash


@dataclass(frozen=True)
class FilteredFacts:
    facts: tuple[Fact, ...]
    decisions: tuple[Decision, ...]


def filter_facts(
    facts: Iterable[Fact],
    catalog: Catalog,
    blobs: Mapping[str, bytes],
    policy: Policy,
) -> FilteredFacts:
    kept, decisions = [], []
    for fact in facts:
        decision = catalog.decide_fact(fact, blobs.get(fact.accn), policy)
        decisions.append(decision)
        if decision.allowed:
            kept.append(fact.model_copy(deep=True))
    return FilteredFacts(tuple(kept), tuple(decisions))


def select_annual(
    facts: Iterable[Fact],
    catalog: Catalog,
    blobs: Mapping[str, bytes],
    policy: Policy,
    *,
    prefer: str = "original",
) -> dict[int, Fact]:
    """One concept/unit/entity stream; never backdate later comparatives or amendments.

    A filing is definitely earlier only when its upper bound precedes the other's
    lower bound. Multiple possible original/latest identities are refused.
    A later comparative is not automatically labelled a formal amendment.
    """
    if prefer not in {"original", "latest"}:
        raise ValueError("prefer must be original or latest")
    filtered = filter_facts(facts, catalog, blobs, policy)
    rows = [f for f in filtered.facts if is_annual(f)]
    if len({(f.cik, f.taxonomy, f.concept, f.unit) for f in rows}) > 1:
        raise ValueError("selection requires a single entity/concept/unit stream")
    groups: dict[int, list[Fact]] = {}
    for fact in rows:
        groups.setdefault(fiscal_year_label(fact.end), []).append(fact)
    chosen = {}
    for year, group in groups.items():
        if len({(f.start, f.end) for f in group}) != 1:
            raise ValueError("conflicting fiscal periods")
        # Check every admitted vintage, not just the eventually selected one.
        # Distinct trusted values for one accession cannot be a restatement.
        by_accession: dict[str, set[str]] = {}
        for fact in group:
            by_accession.setdefault(fact.accn, set()).add(fact_hash(fact))
        if any(len(bindings) > 1 for bindings in by_accession.values()):
            raise ValueError("conflicting fact rows within an accession")
        ranked = {}
        for fact in group:
            decision = catalog.decide_fact(fact, blobs[fact.accn], policy)
            assert decision.available_lower_utc is not None
            assert decision.available_upper_utc is not None
            ranked.setdefault(
                fact.accn, (decision.available_lower_utc, decision.available_upper_utc, fact)
            )
        candidates = []
        for accession, (lower, upper, fact) in ranked.items():
            # A partial order, not sorting interval endpoints. Equality and
            # overlapping intervals cannot establish a strict filing order.
            dominated = any(
                (other_upper < lower if prefer == "original" else upper < other_lower)
                for other_accn, (other_lower, other_upper, _) in ranked.items()
                if other_accn != accession
            )
            if not dominated:
                candidates.append(fact)
        if len(candidates) != 1:
            raise ValueError("ambiguous filing order: overlapping availability intervals")
        chosen[year] = candidates[0]
    return dict(sorted(chosen.items()))
