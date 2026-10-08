"""Lossless, JSON-safe snapshots of the verifier's per-number evidence."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from finreceipts.models import VerificationResult


def _json_safe(value: Any) -> Any:
    """Keep non-finite numbers explicit without emitting invalid JSON tokens."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def serialize_audit(audit: Iterable[VerificationResult]) -> list[dict[str, Any]]:
    """Snapshot every result, including unsupported claims and all matched facts.

    Pydantic's JSON mode preserves the complete model fields, including computed
    source URLs, while converting dates, enums, and spans into JSON primitives.
    Non-finite floats are represented by the strings ``Infinity``, ``-Infinity``,
    and ``NaN`` so strict JSON writers accept them without losing their meaning.

    The returned objects are detached from the live models. This function only
    serializes evidence supplied by the verifier; it never reconstructs missing
    evidence for old answers or invents receipts for unsupported claims.
    """
    return [_json_safe(result.model_dump(mode="json")) for result in audit]
