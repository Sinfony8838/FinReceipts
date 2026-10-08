"""Auto-generate numeric QA items from SEC ``companyfacts``.

Ground truth is computed deterministically with the same
:class:`~finreceipts.xbrl.metrics.MetricSpec` used by the verifier, using the
value *as originally reported* in that fiscal year's own annual report.
Each item records the exact facts (accession number, filing date) it relies on
and an ``as_of`` date (the latest filing date among them) for point-in-time use.
"""

from __future__ import annotations

import random
from collections.abc import Iterable

from finreceipts.evals.dataset import EvalItem, EvidenceRef
from finreceipts.tools.edgar import EdgarClient
from finreceipts.xbrl.metrics import (
    CONCEPTS,
    FactLookup,
    MetricKind,
    MetricSpec,
    compute,
    lookup_from_companyfacts,
)

DIRECT_SPECS = [
    MetricSpec(MetricKind.DIRECT, CONCEPTS[n])
    for n in (
        "revenue",
        "net income",
        "operating income",
        "total assets",
        "net cash provided by operating activities",
        "research and development expense",
        "diluted EPS",
    )
]
GROWTH_SPECS = [MetricSpec(MetricKind.YOY_GROWTH, CONCEPTS[n]) for n in ("revenue", "net income")]
RATIO_SPECS = [
    MetricSpec(MetricKind.RATIO, CONCEPTS["net income"], CONCEPTS["revenue"]),
    MetricSpec(MetricKind.RATIO, CONCEPTS["operating income"], CONCEPTS["revenue"]),
]

DEFAULT_TICKERS = (
    "AAPL MSFT GOOGL AMZN NVDA META TSLA JNJ PG KO PEP WMT COST INTC CSCO "
    "ORCL ADBE NKE MCD HD PFE CVX DIS NFLX"
).split()


def question_text(
    spec: MetricSpec, entity: str, period_end_str: str, tags: tuple[str, ...] = ()
) -> str:
    """Natural-language question for a spec (explicit period, unit and XBRL tag).

    The XBRL tag(s) are named to remove definitional ambiguity (e.g. Walmart's
    "net sales" vs "total revenues").
    """
    when = f"the fiscal year ended {period_end_str}"
    tag_hint = f" (XBRL: {', '.join(tags)})" if tags else ""
    if spec.kind is MetricKind.DIRECT:
        unit = "in US dollars per share" if spec.unit == "USD/shares" else "in US dollars"
        return (
            f"What was {entity}'s {spec.concept.name}{tag_hint} for {when}, as reported in "
            f"its annual report on Form 10-K? Answer {unit}."
        )
    if spec.kind is MetricKind.YOY_GROWTH:
        return (
            f"By what percentage did {entity}'s {spec.concept.name}{tag_hint} change in {when} "
            f"compared with the prior fiscal year? Answer as a percentage (negative for a decline)."
        )
    assert spec.denominator is not None
    return (
        f"What was {entity}'s {spec.concept.name} as a percentage of its "
        f"{spec.denominator.name}{tag_hint} for {when}? Answer as a percentage."
    )


def items_for_company(
    ticker: str,
    cik: int,
    entity: str,
    lookup: FactLookup,
    fiscal_years: Iterable[int],
    specs: Iterable[MetricSpec],
) -> list[EvalItem]:
    out: list[EvalItem] = []
    for spec in specs:
        for fy in fiscal_years:
            ev = compute(spec, lookup, fy)
            if ev is None or ev.expected_value is None:
                continue
            main = ev.facts[0]
            period = main.end.strftime("%B %d, %Y").replace(" 0", " ")
            out.append(
                EvalItem(
                    id=f"{ticker}-{fy}-{spec.kind.value}-{spec.concept.name.replace(' ', '_')}",
                    ticker=ticker,
                    cik=cik,
                    entity=entity,
                    metric=spec.name,
                    kind=spec.kind.value,
                    fiscal_year=fy,
                    period_end=main.end,
                    question=question_text(
                        spec, entity, period, tuple(dict.fromkeys(f.concept for f in ev.facts))
                    ),
                    expected_value=ev.expected_value,
                    expected_unit=ev.expected_unit or spec.unit,
                    formula=ev.formula or "",
                    evidence=[
                        EvidenceRef(
                            concept=f.concept,
                            value=f.value,
                            unit=f.unit,
                            start=f.start,
                            end=f.end,
                            accn=f.accn,
                            form=f.form,
                            filed=f.filed,
                            source_url=f.source_url,
                        )
                        for f in ev.facts
                    ],
                    as_of=max(f.filed for f in ev.facts),
                )
            )
    return out


def generate(
    client: EdgarClient,
    *,
    tickers: Iterable[str] = DEFAULT_TICKERS,
    fiscal_years: Iterable[int] = (2023, 2024, 2025),
    n_direct: int = 60,
    n_growth: int = 20,
    n_ratio: int = 20,
    seed: int = 7,
) -> list[EvalItem]:
    """Build a stratified, seeded sample of eval items across companies."""
    fiscal_years = list(fiscal_years)
    pools: dict[str, list[EvalItem]] = {"direct": [], "yoy_growth": [], "ratio": []}
    for ticker in tickers:
        cik = client.ticker_to_cik(ticker)
        cf = client.companyfacts(cik)
        lookup = lookup_from_companyfacts(cf)
        entity = cf.get("entityName", ticker)
        for kind, specs in (
            ("direct", DIRECT_SPECS),
            ("yoy_growth", GROWTH_SPECS),
            ("ratio", RATIO_SPECS),
        ):
            pools[kind].extend(items_for_company(ticker, cik, entity, lookup, fiscal_years, specs))
    rng = random.Random(seed)
    picked: list[EvalItem] = []
    for kind, n in (("direct", n_direct), ("yoy_growth", n_growth), ("ratio", n_ratio)):
        pool = sorted(pools[kind], key=lambda it: it.id)
        picked.extend(rng.sample(pool, min(n, len(pool))))
    return sorted(picked, key=lambda it: it.id)
