"""Capture observed-now aggregate SEC facts without inventing historical availability.

Receipts record collector claims, not authenticated time. No filing acceptance dates
or amendment parents are inferred from an aggregate companyfacts response.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from finreceipts.pit.catalog_io import _unique_object, load_catalog
from finreceipts.pit.policy import Filing, TimeEvidence, content_hash, fact_hash, utc
from finreceipts.tools.edgar import COMPANYFACTS_URL, parse_concept_facts

MAX_BYTES = 32 * 1024 * 1024


def _decode(raw: bytes) -> dict:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("snapshot must contain 1 to 33554432 decoded bytes")
    data = json.loads(raw, object_pairs_hook=_unique_object)
    if (
        not isinstance(data, dict)
        or type(data.get("cik")) is not int
        or not 0 < data["cik"] < 10**10
    ):
        raise ValueError("companyfacts requires a positive integer cik")
    if not isinstance(data.get("facts"), dict):
        raise ValueError("companyfacts requires a facts object")
    return data


def _filings(raw: bytes, observed: str, source: str) -> list[dict]:
    data = _decode(raw)
    by_accession: dict[str, list] = defaultdict(list)
    for taxonomy, concepts in data["facts"].items():
        if not isinstance(concepts, dict):
            raise ValueError("taxonomy must contain concept objects")
        for concept, node in concepts.items():
            if not isinstance(node, dict) or not isinstance(node.get("units"), dict):
                raise ValueError("concept must contain a units object")
            for rows in node["units"].values():
                if not isinstance(rows, list):
                    raise ValueError("unit facts must be a list")
                if any(
                    not isinstance(row, dict) or type(row.get("val")) not in {int, float}
                    for row in rows
                ):
                    raise ValueError("fact val must be a JSON number, not a boolean or string")
            for fact in parse_concept_facts(data, concept, taxonomy=taxonomy):
                if not math.isfinite(fact.value):
                    raise ValueError("non-finite fact value")
                by_accession[fact.accn].append(fact)
    if not by_accession:
        raise ValueError("snapshot contains no bindable facts")
    rows = []
    for accession, facts in sorted(by_accession.items()):
        forms = {f.form for f in facts}
        if len(forms) != 1:
            raise ValueError(f"conflicting forms for {accession}")
        record = Filing(
            accession=accession,
            cik=data["cik"],
            form=facts[0].form,
            sha256=content_hash(raw),
            first_observed=TimeEvidence(observed, "exact"),
            observation_source=source,
            fact_hashes=tuple(fact_hash(f) for f in facts),
        )
        rows.append(
            {
                "accession": record.accession,
                "cik": record.cik,
                "form": record.form,
                "sha256": record.sha256,
                "first_observed": {"raw": observed, "precision": "exact"},
                "observation_source": source,
                "fact_hashes": list(record.fact_hashes),
                "raw_path": "companyfacts.json",
            }
        )
    return rows


def capture_snapshot(
    output: Path,
    *,
    local_bytes: bytes | None = None,
    cik: int | None = None,
    user_agent: str | None = None,
    transport: httpx.BaseTransport | None = None,
    _clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    _monotonic: Callable[[], float] = time.monotonic,
) -> Path:
    """Write a new bundle; never overwrite, backdate, retry, or follow redirects.

    Local input is a new observation of supplied bytes, not verified SEC retrieval.
    Live capture performs one request only, requiring an operator-supplied contact.
    Injected clocks/transports are for deterministic tests, not provenance authority.
    """
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    if cik is not None and (type(cik) is not int or not 0 < cik < 10**10):
        raise ValueError("cik must be a positive integer of at most 10 digits")
    if local_bytes is None and (
        cik is None
        or not user_agent
        or "@" not in user_agent
        or "example.com" in user_agent
        or "\n" in user_agent
        or "\r" in user_agent
    ):
        raise ValueError(
            "live capture requires cik and an authorized descriptive contact User-Agent"
        )
    started = utc(_clock())
    tick = _monotonic()
    if local_bytes is not None:
        raw = local_bytes
        acquisition = "local_import"
    else:
        acquisition = "sec_https"
        with (
            httpx.Client(transport=transport, timeout=30, follow_redirects=False) as client,
            client.stream(
                "GET",
                COMPANYFACTS_URL.format(cik=cik),
                headers={"User-Agent": user_agent, "Accept": "application/json"},
            ) as response,
        ):
            if response.status_code != 200:
                raise ValueError(f"SEC capture stopped: HTTP {response.status_code}; no retries")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("SEC response exceeds capture size limit")
                chunks.append(chunk)
            raw = b"".join(chunks)
    ended = utc(_clock())
    elapsed = _monotonic() - tick
    if ended < started or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("collector clock moved backwards")
    data = _decode(raw)
    if cik is not None and data["cik"] != cik:
        raise ValueError("requested and returned CIK differ")
    url = COMPANYFACTS_URL.format(cik=data["cik"])
    source = f"{acquisition}:{url}#sha256={content_hash(raw)}"
    rows = _filings(raw, ended.isoformat(), source)
    receipt = {
        "schema_version": 1,
        "kind": "companyfacts_observation",
        "acquisition": acquisition,
        "source_url": url,
        "source_verified_by_collector": acquisition == "sec_https",
        "sha256": content_hash(raw),
        "byte_count": len(raw),
        "started_at": started.isoformat(),
        "observed_at": ended.isoformat(),
        "elapsed_monotonic_seconds": elapsed,
        "clock": "collector_system_utc_unattested",
        "bytes_definition": "decoded_response_body"
        if acquisition == "sec_https"
        else "local_input",
        "historical_availability_proven": False,
        "amendment_relations": "not_inferred",
    }
    # Validate everything before creating output. catalog.json is the completion marker.
    output.mkdir(parents=True, exist_ok=False)
    (output / "companyfacts.json").write_bytes(raw)
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    (output / "catalog.json").write_text(
        json.dumps({"schema_version": 1, "filings": rows}, indent=2) + "\n", encoding="utf-8"
    )
    return output / "catalog.json"


def validate_snapshot(path: Path) -> dict:
    """Verify internal consistency, never external truth or historical availability."""
    path = Path(path)
    catalog, blobs = load_catalog(path / "catalog.json")
    receipt = json.loads((path / "receipt.json").read_text(), object_pairs_hook=_unique_object)
    expected_keys = {
        "schema_version",
        "kind",
        "acquisition",
        "source_url",
        "source_verified_by_collector",
        "sha256",
        "byte_count",
        "started_at",
        "observed_at",
        "elapsed_monotonic_seconds",
        "clock",
        "bytes_definition",
        "historical_availability_proven",
        "amendment_relations",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        raise ValueError("invalid observation receipt fields")
    raw = (path / "companyfacts.json").read_bytes()
    data = _decode(raw)
    acquisition = receipt["acquisition"]
    elapsed = receipt["elapsed_monotonic_seconds"]
    if (
        type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != 1
        or receipt["kind"] != "companyfacts_observation"
        or acquisition not in {"sec_https", "local_import"}
        or receipt["source_url"] != COMPANYFACTS_URL.format(cik=data["cik"])
        or receipt["source_verified_by_collector"] is not (acquisition == "sec_https")
        or receipt["sha256"] != content_hash(raw)
        or type(receipt["byte_count"]) is not int
        or receipt["byte_count"] != len(raw)
        or receipt["clock"] != "collector_system_utc_unattested"
        or receipt["historical_availability_proven"] is not False
        or receipt["amendment_relations"] != "not_inferred"
        or receipt["bytes_definition"]
        != ("decoded_response_body" if acquisition == "sec_https" else "local_input")
        or type(elapsed) not in {int, float}
        or not math.isfinite(elapsed)
        or elapsed < 0
    ):
        raise ValueError("observation receipt integrity or contract mismatch")
    start = TimeEvidence(receipt["started_at"], "exact")
    end = TimeEvidence(receipt["observed_at"], "exact")
    if end.lower_utc < start.lower_utc:
        raise ValueError("collector clock moved backwards")
    source = f"{acquisition}:{receipt['source_url']}#sha256={content_hash(raw)}"
    expected = {"schema_version": 1, "filings": _filings(raw, receipt["observed_at"], source)}
    manifest = json.loads((path / "catalog.json").read_text(), object_pairs_hook=_unique_object)
    if manifest != expected or any(blob != raw for blob in blobs.values()):
        raise ValueError("catalog does not match observed bytes and derived bindings")
    return {
        "filings": len(catalog.filings),
        "sha256": content_hash(raw),
        "observed_at": receipt["observed_at"],
        "acquisition": acquisition,
        "historical_availability_proven": False,
    }
