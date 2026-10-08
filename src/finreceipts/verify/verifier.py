"""Deterministic numeric verifier.

Given a claimed number and the evidence (expected value + unit + period), decide
whether the claim is *supported*, *contradicted* or *unsupported*, and explain
why (unit mismatch, scale error, sign error, period mismatch, out of tolerance).

The tolerance is rounding-aware: ``"$383.3 billion"`` displays one decimal of
billions, so anything within ±$0.05bn of the expected value is accepted, in
addition to a small relative tolerance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from finreceipts.models import Claim, Evidence, Verdict, VerificationResult
from finreceipts.verify.numbers import NumberMention, extract_numbers
from finreceipts.xbrl.metrics import FactLookup

SCALE_FACTORS = (1e3, 1e6, 1e9)
PERIOD_SLACK_DAYS = 7


@dataclass(frozen=True)
class Tolerance:
    """Acceptance thresholds.

    rel:       relative tolerance for amounts (fraction of expected value).
    pct_abs:   absolute tolerance for percentages, in percentage points.
    rounding:  whether to also accept differences explained by display rounding.
    """

    rel: float = 0.005
    pct_abs: float = 0.05
    rounding: bool = True


def _units_compatible(claimed: str | None, expected: str | None) -> bool:
    if claimed is None or expected is None:
        return True
    if claimed == expected:
        return True
    # A per-share amount written as "$6.13" / "6.13元" is parsed as USD / CNY.
    pairs = ({claimed, expected},)
    return any(pair == {"USD", "USD/shares"} or pair == {"CNY", "CNY/shares"} for pair in pairs)


def check_value(
    claimed: float,
    expected: float,
    *,
    claimed_unit: str | None = None,
    expected_unit: str | None = None,
    rounding_step: float = 0.0,
    negative_context: bool = False,
    tol: Tolerance | None = None,
) -> tuple[Verdict, float | None, list[str]]:
    """Compare one claimed value with one expected value.

    Returns ``(verdict, relative_error, reasons)``.
    """
    tol = tol or Tolerance()
    reasons: list[str] = []
    if not _units_compatible(claimed_unit, expected_unit):
        return Verdict.CONTRADICTED, None, [f"unit mismatch: {claimed_unit} vs {expected_unit}"]

    # "Revenue declined 2.8%" states a negative growth with a positive literal.
    if negative_context and expected_unit == "percent" and claimed > 0 and expected < 0:
        claimed = -claimed
        reasons.append("sign inferred from wording (decline)")

    diff = abs(claimed - expected)
    rel_err = diff / abs(expected) if expected else (0.0 if claimed == 0 else math.inf)

    if expected_unit == "percent":
        allowed = max(tol.pct_abs, rounding_step if tol.rounding else 0.0)
    else:
        allowed = max(tol.rel * abs(expected), rounding_step if tol.rounding else 0.0)

    if diff <= allowed + 1e-12:
        return Verdict.SUPPORTED, rel_err, reasons

    opposite_sign = (
        expected != 0 and claimed != 0 and math.copysign(1, claimed) != math.copysign(1, expected)
    )
    if opposite_sign and abs(abs(claimed) - abs(expected)) <= allowed + 1e-12:
        return Verdict.CONTRADICTED, rel_err, [*reasons, "sign error"]
    if expected and claimed:
        ratio = abs(claimed / expected)
        for f in SCALE_FACTORS:
            for r in (f, 1 / f):
                if abs(ratio - r) / r < max(tol.rel, 0.01):
                    return (
                        Verdict.CONTRADICTED,
                        rel_err,
                        [
                            *reasons,
                            f"scale error (off by a factor of {r:g})",
                        ],
                    )
    return (
        Verdict.CONTRADICTED,
        rel_err,
        [
            *reasons,
            f"value differs: claimed {claimed:,.6g} vs expected {expected:,.6g}",
        ],
    )


class Verifier:
    """Verify claims against a :data:`FactLookup` built from XBRL facts."""

    def __init__(self, lookup: FactLookup, tol: Tolerance | None = None) -> None:
        self.lookup = lookup
        self.tol = tol or Tolerance()

    def verify_against(
        self, claim: Claim, evidence: Evidence, mention: NumberMention | None = None
    ) -> VerificationResult:
        """Check ``claim`` against known ``evidence`` (bound verification)."""
        if evidence.expected_value is None:
            return VerificationResult(
                claim=claim,
                verdict=Verdict.UNSUPPORTED,
                evidence=evidence,
                reasons=["evidence has no expected value"],
            )
        reasons: list[str] = []
        if claim.period_end and evidence.facts:
            fact_end = evidence.facts[0].end
            if abs((fact_end - claim.period_end).days) > PERIOD_SLACK_DAYS:
                return VerificationResult(
                    claim=claim,
                    verdict=Verdict.CONTRADICTED,
                    evidence=evidence,
                    reasons=[f"period mismatch: claim {claim.period_end} vs fact {fact_end}"],
                )
        verdict, rel_err, more = check_value(
            claim.value,
            evidence.expected_value,
            claimed_unit=claim.unit,
            expected_unit=evidence.expected_unit,
            rounding_step=mention.rounding_step if mention else 0.0,
            negative_context=mention.negative_context if mention else False,
            tol=self.tol,
        )
        return VerificationResult(
            claim=claim,
            verdict=verdict,
            evidence=evidence,
            relative_error=rel_err,
            reasons=reasons + more,
        )

    def find_receipt(
        self, mention: NumberMention, *, fiscal_years: set[int] | None = None
    ) -> VerificationResult:
        """Unbound verification: search all known facts for one matching ``mention``.

        Used to flag *unsupported numbers* — numbers in an answer that no fact
        in the lookup can account for. Percentages are matched against
        year-over-year growth and simple ratios computed on the fly.
        """
        from finreceipts.xbrl.metrics import CONCEPTS, MetricKind, MetricSpec, compute

        claim = Claim(text=mention.text, value=mention.value, unit=mention.unit, span=mention.span)
        candidates: list[Evidence] = []
        if mention.unit == "percent":
            names = list(self.lookup)
            for name in names:
                for fy in self.lookup[name]:
                    if fiscal_years and fy not in fiscal_years:
                        continue
                    ev = compute(MetricSpec(MetricKind.YOY_GROWTH, CONCEPTS[name]), self.lookup, fy)
                    if ev:
                        candidates.append(ev)
                    for den in ("revenue", "total assets"):
                        if den != name and den in self.lookup:
                            spec = MetricSpec(MetricKind.RATIO, CONCEPTS[name], CONCEPTS[den])
                            ev = compute(spec, self.lookup, fy)
                            if ev:
                                candidates.append(ev)
        else:
            for name, series in self.lookup.items():
                for fy in series:
                    if fiscal_years and fy not in fiscal_years:
                        continue
                    ev = compute(MetricSpec(MetricKind.DIRECT, CONCEPTS[name]), self.lookup, fy)
                    if ev:
                        candidates.append(ev)
        best: VerificationResult | None = None
        for ev in candidates:
            res = self.verify_against(claim, ev, mention)
            if res.ok and (best is None or (res.relative_error or 0) < (best.relative_error or 0)):
                best = res
        if best is not None:
            return best
        return VerificationResult(
            claim=claim, verdict=Verdict.UNSUPPORTED, reasons=["no matching fact found"]
        )

    def audit_text(
        self, text: str, *, fiscal_years: set[int] | None = None
    ) -> list[VerificationResult]:
        """Find a receipt for every number in ``text`` (years are ignored)."""
        return [self.find_receipt(m, fiscal_years=fiscal_years) for m in extract_numbers(text)]


def claim_from_mention(mention: NumberMention, period_end: date | None = None) -> Claim:
    return Claim(
        text=mention.text,
        value=mention.value,
        unit=mention.unit,
        span=mention.span,
        period_end=period_end,
    )
