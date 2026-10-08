"""Agent-facing tools backed by XBRL / A-share facts (point-in-time aware)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

from finreceipts.llm.client import ToolSpec
from finreceipts.tools.akshare_a import METRIC_FIELDS, is_a_share_code, normalize_code
from finreceipts.xbrl.metrics import CONCEPTS, FactLookup

LookupProvider = Callable[[str, date | None], FactLookup]
"""(ticker, as_of) -> FactLookup."""

_A_SHARE_METRICS = sorted(m for m in METRIC_FIELDS if m in CONCEPTS)

GET_FACT_SPEC = ToolSpec(
    name="get_financial_fact",
    description=(
        "Look up an annual figure reported in a US company's 10-K (SEC XBRL). "
        "Returns the value in base units (US dollars, or USD per share for EPS), the "
        "period, the XBRL concept and a link to the filing."
    ),
    parameters={
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Stock ticker, e.g. AAPL"},
            "metric": {"type": "string", "enum": sorted(CONCEPTS)},
            "fiscal_year": {
                "type": "integer",
                "description": "Fiscal year, labelled by the calendar year in which it ends",
            },
        },
        "required": ["ticker", "metric", "fiscal_year"],
    },
)

GET_A_SHARE_FACT_SPEC = ToolSpec(
    name="get_a_share_financial_fact",
    description=(
        "Look up an annual figure from a China A-share company's consolidated annual report "
        "(Eastmoney / AKShare-compatible fields). Values are in CNY (or CNY per share for EPS). "
        "Use 6-digit codes such as 600519."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "6-digit A-share code, e.g. 600519",
            },
            "metric": {"type": "string", "enum": _A_SHARE_METRICS},
            "fiscal_year": {
                "type": "integer",
                "description": "Fiscal year ending calendar year (年报会计年度)",
            },
        },
        "required": ["code", "metric", "fiscal_year"],
    },
)


def run_get_fact(provider: LookupProvider, args: dict, as_of: date | None) -> str:
    """Execute ``get_financial_fact``; always returns a JSON string (errors included)."""
    try:
        ticker = str(args["ticker"]).upper()
        metric = str(args["metric"])
        fy = int(args["fiscal_year"])
    except (KeyError, TypeError, ValueError) as exc:
        return json.dumps({"error": f"bad arguments: {exc}"})
    if metric not in CONCEPTS:
        return json.dumps({"error": f"unknown metric {metric!r}", "allowed": sorted(CONCEPTS)})
    try:
        lookup = provider(ticker, as_of)
    except Exception as exc:
        return json.dumps({"error": f"lookup failed: {type(exc).__name__}: {exc}"})
    fact = lookup.get(metric, {}).get(fy)
    if fact is None:
        return json.dumps({"error": f"no {metric} for {ticker} FY{fy} (as of {as_of})"})
    return json.dumps(
        {
            "ticker": ticker,
            "metric": metric,
            "fiscal_year": fy,
            "value": fact.value,
            "unit": fact.unit,
            "period_start": str(fact.start) if fact.start else None,
            "period_end": str(fact.end),
            "concept": fact.concept,
            "accession": fact.accn,
            "filed": str(fact.filed),
            "source_url": fact.source_url,
        }
    )


def run_get_a_share_fact(provider: LookupProvider, args: dict, as_of: date | None) -> str:
    """Execute ``get_a_share_financial_fact``."""
    try:
        code = normalize_code(str(args["code"]))
        metric = str(args["metric"])
        fy = int(args["fiscal_year"])
    except (KeyError, TypeError, ValueError) as exc:
        return json.dumps({"error": f"bad arguments: {exc}"})
    if metric not in METRIC_FIELDS:
        return json.dumps({"error": f"unknown metric {metric!r}", "allowed": sorted(METRIC_FIELDS)})
    try:
        lookup = provider(code, as_of)
    except Exception as exc:
        return json.dumps({"error": f"lookup failed: {type(exc).__name__}: {exc}"})
    fact = lookup.get(metric, {}).get(fy)
    if fact is None:
        return json.dumps({"error": f"no {metric} for {code} FY{fy} (as of {as_of})"})
    return json.dumps(
        {
            "code": code,
            "metric": metric,
            "fiscal_year": fy,
            "value": fact.value,
            "unit": fact.unit,
            "period_start": str(fact.start) if fact.start else None,
            "period_end": str(fact.end),
            "concept": fact.concept,
            "accession": fact.accn,
            "filed": str(fact.filed),
            "source_url": fact.source_url,
            "market": "CN",
        },
        ensure_ascii=False,
    )


def combined_lookup_provider(
    us_provider: LookupProvider, cn_provider: LookupProvider
) -> LookupProvider:
    """Route 6-digit codes to the A-share provider; everything else to US EDGAR."""

    def provider(ticker: str, as_of: date | None) -> FactLookup:
        if is_a_share_code(ticker):
            return cn_provider(normalize_code(ticker), as_of)
        return us_provider(ticker, as_of)

    return provider
