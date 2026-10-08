"""Selecting the *right* fact out of the many reported per concept.

The same value is typically reported in several filings (current year, then
as a comparative in the next two 10-Ks), quarterly and annual periods are
mixed, and restatements exist. These helpers normalise that mess into one
fact per fiscal period, with an optional point-in-time cut-off (``as_of``).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from finreceipts.models import Fact, PeriodType

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT", "20-F", "20-F/A", "40-F"})
ANNUAL_MIN_DAYS = 350
ANNUAL_MAX_DAYS = 380


def fiscal_year_label(period_end: date) -> int:
    """Label a fiscal year by the calendar year in which it ends.

    52/53-week years that end in the first week of January are attributed to
    the previous calendar year (e.g. a year ending 2023-01-01 is FY2022).
    """
    if period_end.month == 1 and period_end.day <= 7:
        return period_end.year - 1
    return period_end.year


def is_annual(fact: Fact) -> bool:
    """True for full-year duration facts or year-end instants from annual forms."""
    if fact.form not in ANNUAL_FORMS:
        return False
    if fact.period_type is PeriodType.INSTANT:
        return True
    assert fact.duration_days is not None
    return ANNUAL_MIN_DAYS <= fact.duration_days <= ANNUAL_MAX_DAYS


def visible_as_of(facts: Iterable[Fact], as_of: date | None) -> list[Fact]:
    """Point-in-time filter: keep only facts *filed* on or before ``as_of``."""
    if as_of is None:
        return list(facts)
    return [f for f in facts if f.filed <= as_of]


def annual_series(
    facts: Iterable[Fact],
    *,
    as_of: date | None = None,
    prefer: str = "latest",
) -> dict[int, Fact]:
    """Return one annual fact per fiscal-year label.

    Parameters
    ----------
    as_of:
        Ignore facts filed after this date (no look-ahead).
    prefer:
        ``"latest"`` keeps the most recently filed value (as restated);
        ``"original"`` keeps the first filed value (as originally reported).
    """
    if prefer not in {"latest", "original"}:
        raise ValueError("prefer must be 'latest' or 'original'")
    chosen: dict[int, Fact] = {}
    for fact in visible_as_of(facts, as_of):
        if not is_annual(fact):
            continue
        fy = fiscal_year_label(fact.end)
        current = chosen.get(fy)
        if current is None:
            chosen[fy] = fact
            continue
        # The fiscal year's own period beats a mislabelled neighbour.
        if current.end != fact.end:
            if fact.end > current.end:
                chosen[fy] = fact
            continue
        newer = fact.filed > current.filed
        if (prefer == "latest" and newer) or (prefer == "original" and not newer):
            chosen[fy] = fact
    return dict(sorted(chosen.items()))


def restated_years(facts: Iterable[Fact]) -> dict[int, tuple[float, float]]:
    """Fiscal years whose originally reported value differs from the latest one."""
    facts = list(facts)
    orig = annual_series(facts, prefer="original")
    latest = annual_series(facts, prefer="latest")
    return {
        fy: (orig[fy].value, latest[fy].value)
        for fy in orig
        if fy in latest and orig[fy].value != latest[fy].value
    }
