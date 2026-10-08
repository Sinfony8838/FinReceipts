"""Load host-supplied PIT sidecars and raw bytes without network access.

This verifies integrity and shape, not the truth of collection provenance.
Never create availability metadata or fact bindings from model output.
"""

from __future__ import annotations

import json
from pathlib import Path

from finreceipts.pit.policy import Catalog, Filing, TimeEvidence, content_hash

_REQUIRED = {"accession", "cik", "form", "sha256", "fact_hashes", "raw_path"}
_OPTIONAL = {
    "first_observed",
    "observation_source",
    "acceptance",
    "acceptance_source",
    "revision_of",
}


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON field: {key}")
        out[key] = value
    return out


def _time(value: object) -> TimeEvidence | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"raw", "precision"}:
        raise ValueError("time evidence requires exactly raw and precision")
    if not isinstance(value["raw"], str) or not isinstance(value["precision"], str):
        raise ValueError("time evidence must use strings")
    return TimeEvidence(**value)


def load_catalog(path: Path) -> tuple[Catalog, dict[str, bytes]]:
    """Read a v1 manifest; raw paths must stay beneath its directory.

    Reject conflicting records, missing bytes and mismatched hashes before
    any model is called. Availability and fact-binding decisions still belong
    to the policy selector. A supplied sidecar is not an authenticated catalog.
    Sources must stay immutable during loading; shared resolved paths reuse the
    same in-memory bytes. Each invocation reads the sources anew.
    """
    path = Path(path).resolve(strict=True)
    raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema_version", "filings"}
        or type(raw["schema_version"]) is not int
        or raw["schema_version"] != 1
        or not isinstance(raw["filings"], list)
    ):
        raise ValueError("catalog requires schema_version=1 and a filings list")
    records: list[Filing] = []
    blobs: dict[str, bytes] = {}
    # Several accession groups can share one immutable aggregate snapshot.
    # Keep one copy per resolved path in this invocation, never a global cache.
    sources: dict[Path, tuple[bytes, str]] = {}
    for row in raw["filings"]:
        if not isinstance(row, dict) or not _REQUIRED <= set(row) <= _REQUIRED | _OPTIONAL:
            raise ValueError("invalid catalog filing fields")
        strings = {"accession", "form", "sha256", "raw_path"}
        strings |= {"observation_source", "acceptance_source"} & set(row)
        if any(not isinstance(row[key], str) for key in strings):
            raise ValueError("catalog filing text fields must be strings")
        if not isinstance(row["fact_hashes"], list) or any(
            not isinstance(value, str) for value in row["fact_hashes"]
        ):
            raise ValueError("fact_hashes must be a list of SHA-256 strings")
        if row.get("revision_of") is not None and not isinstance(row["revision_of"], str):
            raise ValueError("revision_of must be an accession or null")
        record = Filing(
            **{
                k: v
                for k, v in row.items()
                if k not in {"raw_path", "first_observed", "acceptance"}
            },
            first_observed=_time(row.get("first_observed")),
            acceptance=_time(row.get("acceptance")),
        )
        relative = Path(row["raw_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("raw_path must be a relative path beneath the catalog directory")
        blob_path = (path.parent / relative).resolve(strict=True)
        if not blob_path.is_relative_to(path.parent) or not blob_path.is_file():
            raise ValueError("raw_path must resolve to a file beneath the catalog directory")
        if blob_path not in sources:
            content = blob_path.read_bytes()
            sources[blob_path] = (content, content_hash(content))
        content, actual_hash = sources[blob_path]
        if actual_hash != record.sha256:
            raise ValueError(f"raw content hash mismatch for {record.accession}")
        if record.accession in blobs:
            raise ValueError(f"duplicate catalog accession: {record.accession}")
        blobs[record.accession] = content
        records.append(record)
    return Catalog(tuple(records)), blobs
