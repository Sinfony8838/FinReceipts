"""Metric definitions (direct XBRL concepts and derived ratios).

A :class:`MetricSpec` says how to compute a value from annual facts so that
both the eval generator and the verifier use the *same* definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from finreceipts.models import Evidence, Fact
from finreceipts.xbrl.facts import annual_series


class MetricKind(StrEnum):
    DIRECT = "direct"
    YOY_GROWTH = "yoy_growth"
    RATIO = "ratio"


@dataclass(frozen=True)
class Concept:
    """A business concept with ordered XBRL concept fallbacks."""

    name: str
    xbrl: tuple[str, ...]
    unit: str = "USD"


REVENUE = Concept(
    "revenue",
    (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
)
NET_INCOME = Concept("net income", ("NetIncomeLoss", "ProfitLoss"))
OPERATING_INCOME = Concept("operating income", ("OperatingIncomeLoss",))
GROSS_PROFIT = Concept("gross profit", ("GrossProfit",))
TOTAL_ASSETS = Concept("total assets", ("Assets",))
TOTAL_LIABILITIES = Concept("total liabilities", ("Liabilities",))
CASH = Concept(
    "cash and cash equivalents",
    ("CashAndCashEquivalentsAtCarryingValue",),
)
OPERATING_CASH_FLOW = Concept(
    "net cash provided by operating activities",
    ("NetCashProvidedByUsedInOperatingActivities",),
)
EPS_DILUTED = Concept("diluted EPS", ("EarningsPerShareDiluted",), unit="USD/shares")
RND = Concept("research and development expense", ("ResearchAndDevelopmentExpense",))

CONCEPTS: dict[str, Concept] = {
    c.name: c
    for c in (
        REVENUE,
        NET_INCOME,
        OPERATING_INCOME,
        GROSS_PROFIT,
        TOTAL_ASSETS,
        TOTAL_LIABILITIES,
        CASH,
        OPERATING_CASH_FLOW,
        EPS_DILUTED,
        RND,
    )
}


@dataclass(frozen=True)
class MetricSpec:
    """How to compute one metric for one fiscal year."""

    kind: MetricKind
    concept: Concept
    denominator: Concept | None = None

    @property
    def name(self) -> str:
        if self.kind is MetricKind.DIRECT:
            return self.concept.name
        if self.kind is MetricKind.YOY_GROWTH:
            return f"year-over-year growth in {self.concept.name}"
        assert self.denominator is not None
        return f"{self.concept.name} as a percentage of {self.denominator.name}"

    @property
    def unit(self) -> str:
        return self.concept.unit if self.kind is MetricKind.DIRECT else "percent"


FactLookup = dict[str, dict[int, Fact]]
"""concept name -> fiscal year -> fact (already annualised)."""


def build_lookup(
    facts_by_xbrl: dict[str, list[Fact]],
    *,
    as_of: date | None = None,
    prefer: str = "original",
) -> FactLookup:
    """Resolve every known :class:`Concept` to an annual series using fallbacks.

    For each fiscal year, the first XBRL concept in the fallback list that has a
    value wins (companies switch tags over time, e.g. ``Revenues`` ->
    ``RevenueFromContractWithCustomerExcludingAssessedTax``). ``prefer`` is
    passed to :func:`annual_series` (default: value as originally reported).
    """
    lookup: FactLookup = {}
    for concept in CONCEPTS.values():
        merged: dict[int, Fact] = {}
        for tag in concept.xbrl:
            rows = [f for f in facts_by_xbrl.get(tag, []) if f.unit == concept.unit]
            for fy, fact in annual_series(rows, as_of=as_of, prefer=prefer).items():
                merged.setdefault(fy, fact)
        if merged:
            lookup[concept.name] = dict(sorted(merged.items()))
    return lookup


def compute(spec: MetricSpec, lookup: FactLookup, fiscal_year: int) -> Evidence | None:
    """Compute ``spec`` for ``fiscal_year``; ``None`` if inputs are missing."""
    series = lookup.get(spec.concept.name, {})
    cur = series.get(fiscal_year)
    if cur is None:
        return None
    if spec.kind is MetricKind.DIRECT:
        return Evidence(
            facts=[cur],
            expected_value=cur.value,
            expected_unit=spec.unit,
            formula=f"{cur.concept}[FY{fiscal_year}]",
        )
    if spec.kind is MetricKind.YOY_GROWTH:
        prev = series.get(fiscal_year - 1)
        if prev is None or prev.value == 0:
            return None
        growth = (cur.value - prev.value) / abs(prev.value) * 100.0
        return Evidence(
            facts=[cur, prev],
            expected_value=growth,
            expected_unit="percent",
            formula=(
                f"({cur.concept}[FY{fiscal_year}] - {prev.concept}[FY{fiscal_year - 1}])"
                f" / |{prev.concept}[FY{fiscal_year - 1}]| * 100"
            ),
        )
    assert spec.denominator is not None
    den = lookup.get(spec.denominator.name, {}).get(fiscal_year)
    if den is None or den.value == 0:
        return None
    return Evidence(
        facts=[cur, den],
        expected_value=cur.value / den.value * 100.0,
        expected_unit="percent",
        formula=f"{cur.concept}[FY{fiscal_year}] / {den.concept}[FY{fiscal_year}] * 100",
    )


def all_xbrl_tags() -> set[str]:
    """Every XBRL tag referenced by a known concept."""
    return {tag for c in CONCEPTS.values() for tag in c.xbrl}


def lookup_from_companyfacts(
    companyfacts: dict, *, as_of: date | None = None, prefer: str = "original"
) -> FactLookup:
    """Build a :data:`FactLookup` straight from a ``companyfacts`` payload."""
    from finreceipts.tools.edgar import parse_concept_facts

    facts = {tag: parse_concept_facts(companyfacts, tag) for tag in all_xbrl_tags()}
    return build_lookup(facts, as_of=as_of, prefer=prefer)
