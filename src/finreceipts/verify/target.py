"""Bound final-value verification. No model call and no search for a lucky match."""

from __future__ import annotations

import math
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finreceipts.models import Claim, Evidence, Verdict
from finreceipts.verify.numbers import NumericInputError, extract_numbers
from finreceipts.verify.verifier import Tolerance, check_value
from finreceipts.xbrl.metrics import CONCEPTS, FactLookup, MetricKind, MetricSpec, compute


class VerificationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: MetricKind
    metric: str
    fiscal_year: int = Field(ge=1990, le=2100)
    denominator: str | None = None

    @model_validator(mode="after")
    def valid_metric(self):
        if self.metric not in CONCEPTS:
            raise ValueError("unknown metric")
        if self.kind == MetricKind.RATIO:
            if self.denominator not in CONCEPTS:
                raise ValueError("ratio requires a known denominator")
        elif self.denominator is not None:
            raise ValueError("denominator is only valid for ratios")
        return self

    def spec(self) -> MetricSpec:
        return MetricSpec(self.kind, CONCEPTS[self.metric], CONCEPTS.get(self.denominator))


class TargetAudit(BaseModel):
    claim: Claim | None = None
    verdict: Verdict = Verdict.UNSUPPORTED
    reason_codes: list[str] = Field(default_factory=list)
    evidence: Evidence | None = None
    relative_error: float | None = None

    @property
    def ok(self) -> bool:
        return self.verdict == Verdict.SUPPORTED


def target_evidence(target: VerificationTarget, lookup: FactLookup) -> tuple[Evidence | None, str]:
    series = lookup.get(target.metric, {})
    if target.fiscal_year not in series:
        return None, "missing_evidence"
    denominator = None
    if target.kind == MetricKind.YOY_GROWTH:
        denominator = series.get(target.fiscal_year - 1)
    elif target.kind == MetricKind.RATIO:
        denominator = lookup.get(target.denominator, {}).get(target.fiscal_year)
    if target.kind != MetricKind.DIRECT:
        if denominator is None:
            return None, "missing_evidence"
        if denominator.value == 0:
            return None, "zero_denominator"
    evidence = compute(target.spec(), lookup, target.fiscal_year)
    if evidence is None or evidence.expected_value is None:
        return None, "missing_evidence"
    if not math.isfinite(evidence.expected_value) or any(
        not math.isfinite(f.value) for f in evidence.facts
    ):
        raise NumericInputError("numeric_out_of_range")
    return evidence, ""


def verify_target_answer(
    answer: str | None,
    target: VerificationTarget | dict,
    lookup: FactLookup,
    tol: Tolerance | None = None,
) -> TargetAudit:
    """Check one explicit final quantity against only the requested annual metric.

    Empty/missing evidence abstains, zero stays zero, scale conversion is not FX.
    The caller supplies the target, never the expected value. Prose is outside
    this final-value contract and has its own bounded annual-clause audit.
    """
    target = VerificationTarget.model_validate(target)
    evidence, reason = target_evidence(target, lookup)
    if reason:
        return TargetAudit(reason_codes=[reason])
    if not answer or not answer.strip():
        return TargetAudit(reason_codes=["empty_answer"], evidence=evidence)
    # Enforce the extraction limit on the whole answer, not just its final line.
    extract_numbers(answer)
    final = answer.strip().splitlines()[-1]
    match = re.fullmatch(r"ANSWER:\s*(.+)", final, re.IGNORECASE)
    if not match:
        return TargetAudit(reason_codes=["missing_final_answer"], evidence=evidence)
    mentions = extract_numbers(match[1], keep_years=True)
    if len(mentions) != 1:
        code = "no_final_number" if not mentions else "multiple_final_numbers"
        return TargetAudit(reason_codes=[code], evidence=evidence)
    mention = mentions[0]
    claim = Claim(
        text=mention.text,
        value=mention.value,
        unit=mention.unit,
        metric=target.spec().name,
        entity=evidence.facts[0].entity_name,
        period_end=evidence.facts[0].end,
    )
    if mention.unit is None or mention.unit_conflict:
        return TargetAudit(
            claim=claim,
            evidence=evidence,
            reason_codes=["unit_conflict" if mention.unit_conflict else "missing_unit"],
        )
    verdict, relative_error, reasons = check_value(
        mention.value,
        evidence.expected_value,
        claimed_unit=mention.unit,
        expected_unit=evidence.expected_unit,
        rounding_step=mention.rounding_step,
        negative_context=mention.negative_context,
        tol=tol,
    )
    code = "value_mismatch"
    if reasons and reasons[0].startswith("unit mismatch"):
        code = "unit_mismatch"
    elif any("scale error" in r for r in reasons):
        code = "scale_error"
    elif any("sign error" in r for r in reasons):
        code = "sign_error"
    return TargetAudit(
        claim=claim,
        verdict=verdict,
        evidence=evidence,
        relative_error=relative_error,
        reason_codes=[] if verdict == Verdict.SUPPORTED else [code],
    )
