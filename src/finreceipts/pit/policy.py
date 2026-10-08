"""Fail-closed availability decisions against trusted, immutable sidecar metadata.

SEC acceptance is a proxy, never a measured public-availability timestamp.
Observation proves only that these exact bytes were observed by the collector.
Metadata must be supplied by the host, never inferred from document instructions.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from finreceipts.models import Fact


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def digest(value: object) -> str:
    return content_hash(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )


def fact_hash(fact: Fact) -> str:
    """Bind a parsed row to its identity, period and value in a trusted extraction."""
    return digest(
        {
            "cik": fact.cik,
            "taxonomy": fact.taxonomy,
            "concept": fact.concept,
            "unit": fact.unit,
            "value": fact.value,
            "start": str(fact.start),
            "end": str(fact.end),
            "accn": fact.accn,
            "form": fact.form,
            "filed": str(fact.filed),
        }
    )


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an explicit timezone-aware datetime is required; dates are not cutoffs")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class TimeEvidence:
    raw: str
    precision: Literal["exact", "second", "minute", "day"] = "second"

    def __post_init__(self) -> None:
        if self.precision not in {"exact", "second", "minute", "day"}:
            raise ValueError("unsupported timestamp precision")
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})",
            self.raw,
        ):
            raise ValueError("timestamp must be ISO 8601 with explicit UTC offset")
        dt = datetime.fromisoformat(self.raw.replace("Z", "+00:00"))
        utc(dt)
        if self.precision in {"second", "exact"} and not re.match(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", self.raw
        ):
            raise ValueError("second precision requires seconds in the source")
        if self.precision == "second" and dt.microsecond:
            raise ValueError("second precision must start at a second boundary")
        if self.precision == "minute" and (dt.second or dt.microsecond):
            raise ValueError("minute precision must start at a minute boundary")
        if self.precision == "day" and (dt.hour or dt.minute or dt.second or dt.microsecond):
            raise ValueError("day precision must start at local midnight")

    @property
    def lower_utc(self) -> datetime:
        return utc(datetime.fromisoformat(self.raw.replace("Z", "+00:00")))

    @property
    def upper_utc(self) -> datetime:
        # Closed interval: unknown sub-second/minute/day time is conservatively late.
        width = {
            "exact": timedelta(),
            "second": timedelta(seconds=1),
            "minute": timedelta(minutes=1),
            "day": timedelta(days=1),
        }[self.precision]
        return self.lower_utc + width - (timedelta(microseconds=1) if width else timedelta())

    def descriptor(self) -> dict:
        return {
            "raw": self.raw,
            "precision": self.precision,
            "lower_utc": self.lower_utc.isoformat(),
            "upper_utc": self.upper_utc.isoformat(),
        }


@dataclass(frozen=True)
class Filing:
    accession: str
    cik: int
    form: str
    sha256: str
    first_observed: TimeEvidence | None = None
    observation_source: str = ""
    acceptance: TimeEvidence | None = None
    acceptance_source: str = ""
    revision_of: str | None = None
    fact_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", self.accession):
            raise ValueError("invalid accession")
        if type(self.cik) is not int or self.cik <= 0 or not self.form:
            raise ValueError("invalid filing identity")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("a raw-content SHA-256 is required")
        object.__setattr__(self, "fact_hashes", tuple(sorted(set(self.fact_hashes))))
        if any(not re.fullmatch(r"[0-9a-f]{64}", h) for h in self.fact_hashes):
            raise ValueError("invalid fact binding")
        if self.revision_of is not None and (
            self.revision_of == self.accession
            or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", self.revision_of)
        ):
            raise ValueError("invalid revision parent")

    def descriptor(self) -> dict:
        out = asdict(self)
        out["first_observed"] = self.first_observed.descriptor() if self.first_observed else None
        out["acceptance"] = self.acceptance.descriptor() if self.acceptance else None
        return out


@dataclass(frozen=True)
class Policy:
    cutoff: datetime
    mode: Literal["observed_replay", "acceptance_proxy"] = "observed_replay"
    version: str = "pit-v2"
    acceptance_lag: timedelta = timedelta()

    def __post_init__(self) -> None:
        object.__setattr__(self, "cutoff", utc(self.cutoff))
        if self.mode not in {"observed_replay", "acceptance_proxy"} or not self.version:
            raise ValueError("an explicit supported policy and version are required")
        if self.acceptance_lag < timedelta():
            raise ValueError("proxy lag cannot be negative")
        if self.mode == "observed_replay" and self.acceptance_lag:
            raise ValueError("observed replay does not use proxy lag")

    def descriptor(self) -> dict:
        return {
            "mode": self.mode,
            "version": self.version,
            "cutoff_utc": self.cutoff.isoformat(),
            "acceptance_lag_us": self.acceptance_lag // timedelta(microseconds=1),
        }

    @property
    def fingerprint(self) -> str:
        return digest(self.descriptor())


@dataclass(frozen=True)
class Decision:
    status: Literal["allowed", "refused", "insufficient"]
    reason: str
    accession: str
    policy_fingerprint: str
    approximate: bool
    available_upper_utc: datetime | None = None
    available_lower_utc: datetime | None = None

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"


@dataclass(frozen=True)
class Catalog:
    filings: tuple[Filing, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "filings", tuple(self.filings))

    @property
    def fingerprint(self) -> str:
        return digest(sorted((f.descriptor() for f in self.filings), key=digest))

    def record(self, accession: str) -> Filing | None:
        matches = [r for r in self.filings if r.accession == accession]
        if not matches:
            return None
        if any(r != matches[0] for r in matches):
            raise ValueError("conflicting accession metadata")
        return matches[0]

    def decide(self, accession: str, content: bytes | None, policy: Policy) -> Decision:
        def result(status, reason, available=None, lower=None):
            return Decision(
                status,
                reason,
                accession,
                policy.fingerprint,
                policy.mode == "acceptance_proxy",
                available,
                lower,
            )

        try:
            record = self.record(accession)
        except ValueError:
            return result("refused", "conflicting_accession")
        if record is None:
            return result("insufficient", "unknown_accession")
        if content is None:
            return result("insufficient", "missing_raw_content")
        if content_hash(content) != record.sha256:
            return result("refused", "content_hash_mismatch")
        if (
            record.acceptance
            and record.first_observed
            and record.first_observed.upper_utc < record.acceptance.lower_utc
        ):
            return result("refused", "observation_precedes_acceptance")
        if policy.mode == "observed_replay":
            time, source = record.first_observed, record.observation_source
            lag = timedelta()
        else:
            time, source = record.acceptance, record.acceptance_source
            lag = policy.acceptance_lag
        if time is None or not source:
            return result("insufficient", "missing_availability_provenance")
        available = time.upper_utc + lag
        lower = time.lower_utc + lag
        if available > policy.cutoff:
            return result("refused", "after_cutoff_or_precision_overlap", available, lower)
        return result(
            "allowed",
            "acceptance_proxy_approximate"
            if policy.mode == "acceptance_proxy"
            else "observed_bytes_replay",
            available,
            lower,
        )

    def decide_fact(self, fact: Fact, content: bytes | None, policy: Policy) -> Decision:
        decision = self.decide(fact.accn, content, policy)
        if not decision.allowed:
            return decision
        record = self.record(fact.accn)
        assert record is not None
        reason = None
        status = "refused"
        if (fact.cik, fact.form) != (record.cik, record.form):
            reason = "filing_identity_mismatch"
        elif not record.fact_hashes:
            reason, status = "missing_trusted_fact_binding", "insufficient"
        elif fact_hash(fact) not in record.fact_hashes:
            reason = "fact_binding_mismatch"
        if reason:
            return Decision(status, reason, fact.accn, policy.fingerprint, decision.approximate)
        return decision
