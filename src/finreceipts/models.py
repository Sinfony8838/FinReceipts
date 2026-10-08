"""Core data models: facts, claims, evidence and verification results.

These pydantic models are the contract between the data layer (SEC XBRL),
the agent (LLM-produced claims) and the deterministic verifier.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field


class PeriodType(StrEnum):
    """Whether a fact measures a flow over a duration or a stock at an instant."""

    DURATION = "duration"
    INSTANT = "instant"


class Fact(BaseModel):
    """One reported XBRL fact, as published in SEC ``companyfacts``.

    ``fy``/``fp``/``form`` describe the *filing* the fact came from, not
    necessarily the period of the value (a 10-K reports prior years too).
    Use :attr:`start`/:attr:`end` to reason about the period itself.
    """

    cik: int
    entity_name: str
    taxonomy: str = "us-gaap"
    concept: str
    label: str | None = None
    unit: str
    value: float
    start: date | None = None
    end: date
    accn: str
    form: str
    filed: date
    fy: int | None = None
    fp: str | None = None
    frame: str | None = None
    # Optional non-SEC filing URL (A-share cninfo / Eastmoney evidence).
    filing_url: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def period_type(self) -> PeriodType:
        return PeriodType.INSTANT if self.start is None else PeriodType.DURATION

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_days(self) -> int | None:
        return None if self.start is None else (self.end - self.start).days + 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_url(self) -> str:
        """Link to the filing that contains this fact (SEC index or ``filing_url``)."""
        if self.filing_url:
            return self.filing_url
        accn_nodash = self.accn.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/{self.cik}/{accn_nodash}/"
            f"{self.accn}-index.htm"
        )


class Evidence(BaseModel):
    """A fact (or set of facts) offered in support of a claim."""

    facts: list[Fact] = Field(default_factory=list)
    expected_value: float | None = None
    expected_unit: str | None = None
    formula: str | None = None
    note: str | None = None


class Claim(BaseModel):
    """A single numeric assertion extracted from an answer.

    ``text`` is the exact surface string (e.g. ``"$383.3 billion"``) so the
    report can highlight it in place.
    """

    text: str
    value: float
    unit: str | None = None
    metric: str | None = None
    entity: str | None = None
    period_end: date | None = None
    span: tuple[int, int] | None = None


class Verdict(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"


class VerificationResult(BaseModel):
    """Outcome of checking one claim against evidence."""

    claim: Claim
    verdict: Verdict
    evidence: Evidence | None = None
    relative_error: float | None = None
    reasons: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == Verdict.SUPPORTED
