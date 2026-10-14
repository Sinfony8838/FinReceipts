"""Observation receipts must never turn current aggregates into historical evidence."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from finreceipts.pit.catalog_io import load_catalog
from finreceipts.pit.policy import Policy
from finreceipts.pit.sec_snapshot import capture_snapshot, validate_snapshot
from finreceipts.tools.edgar import parse_concept_facts

NOW = datetime(2026, 10, 8, 11, tzinfo=UTC)


def body(form="10-K"):
    return json.dumps(
        {
            "cik": 1,
            "entityName": "Synthetic Test",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {
                                    "val": 42,
                                    "start": "2022-01-01",
                                    "end": "2022-12-31",
                                    "filed": "2023-02-01",
                                    "form": form,
                                    "accn": "0000000001-23-000001",
                                }
                            ]
                        }
                    }
                }
            },
        }
    ).encode()


def capture(tmp_path, raw=None, **kwargs):
    output = tmp_path / "bundle"
    capture_snapshot(
        output, local_bytes=body() if raw is None else raw, _clock=lambda: NOW, **kwargs
    )
    return output


def test_binding_cutoff_and_no_proxy_backdating(tmp_path):
    path = capture(tmp_path)
    summary = validate_snapshot(path)
    assert summary["acquisition"] == "local_import"
    assert summary["historical_availability_proven"] is False
    catalog, blobs = load_catalog(path / "catalog.json")
    fact = parse_concept_facts(json.loads(body()), "Revenues")[0]
    content = blobs[fact.accn]
    assert catalog.decide_fact(fact, content, Policy(NOW)).allowed
    assert catalog.decide_fact(fact, content, Policy(NOW - timedelta(microseconds=1))).reason == (
        "after_cutoff_or_precision_overlap"
    )
    assert catalog.decide_fact(fact, content, Policy(NOW, mode="acceptance_proxy")).reason == (
        "missing_availability_provenance"
    )
    assert catalog.filings[0].acceptance is None
    assert catalog.filings[0].revision_of is None


def test_amendment_parent_not_guessed(tmp_path):
    path = capture(tmp_path, body("10-K/A"))
    catalog, _ = load_catalog(path / "catalog.json")
    assert catalog.filings[0].form == "10-K/A"
    assert catalog.filings[0].revision_of is None
    assert validate_snapshot(path)["filings"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "0" * 64),
        ("byte_count", True),
        ("schema_version", True),
        ("source_url", "https://evil.invalid/x"),
        ("historical_availability_proven", True),
        ("source_verified_by_collector", True),
        ("clock", "authenticated"),
        ("elapsed_monotonic_seconds", -1),
        ("elapsed_monotonic_seconds", float("nan")),
        ("observed_at", "2020-01-01T00:00:00+00:00"),
        ("amendment_relations", "guessed"),
        ("bytes_definition", "wire_bytes"),
    ],
)
def test_reject_tampered_receipt(tmp_path, field, value):
    path = capture(tmp_path)
    receipt = json.loads((path / "receipt.json").read_text())
    receipt[field] = value
    (path / "receipt.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        validate_snapshot(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("first_observed", {"raw": "2023-02-01T00:00:00+00:00", "precision": "exact"}),
        ("fact_hashes", ["f" * 64]),
        ("acceptance", {"raw": "2023-02-01T00:00:00+00:00", "precision": "exact"}),
        ("revision_of", "0000000001-22-000001"),
    ],
)
def test_reject_catalog_rebinding(tmp_path, field, value):
    path = capture(tmp_path)
    manifest = json.loads((path / "catalog.json").read_text())
    manifest["filings"][0][field] = value
    (path / "catalog.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        validate_snapshot(path)


def test_does_not_overwrite(tmp_path):
    path = capture(tmp_path)
    original = (path / "catalog.json").read_bytes()
    with pytest.raises(FileExistsError):
        capture(tmp_path)
    assert (path / "catalog.json").read_bytes() == original


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b'{"cik":1,"cik":2,"facts":{}}',
        b'{"cik":true,"facts":{}}',
        b'{"cik":1,"facts":{}}',
        body().replace(b"42", b"NaN"),
    ],
)
def test_reject_bad_input_without_partial_output(tmp_path, raw):
    with pytest.raises(ValueError):
        capture(tmp_path, raw)
    assert not (tmp_path / "bundle").exists()


def test_clock_rollback(tmp_path):
    clock = iter([NOW, NOW - timedelta(seconds=1)])
    with pytest.raises(ValueError, match="clock moved backwards"):
        capture_snapshot(tmp_path / "bundle", local_bytes=body(), _clock=lambda: next(clock))
    assert not (tmp_path / "bundle").exists()


def test_cik_mismatch(tmp_path):
    with pytest.raises(ValueError, match="CIK differ"):
        capture(tmp_path, cik=2)


@pytest.mark.parametrize("status", [301, 403, 429, 500])
def test_live_denial_never_retries_or_follows_redirect(tmp_path, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://example.invalid/"})

    with pytest.raises(ValueError, match=f"HTTP {status}"):
        capture_snapshot(
            tmp_path / "bundle",
            cik=1,
            user_agent="Test bot test@invalid.test",
            transport=httpx.MockTransport(handler),
        )
    assert len(calls) == 1
    assert not (tmp_path / "bundle").exists()


def test_live_preserves_body_and_omits_contact(tmp_path):
    raw = body() + b"\n  "
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json"
        return httpx.Response(200, content=raw)

    path = tmp_path / "live"
    capture_snapshot(
        path,
        cik=1,
        user_agent="Test bot test@invalid.test",
        transport=httpx.MockTransport(handler),
        _clock=lambda: NOW,
    )
    assert validate_snapshot(path)["acquisition"] == "sec_https"
    assert (path / "companyfacts.json").read_bytes() == raw
    assert len(calls) == 1
    assert "test@invalid.test" not in (path / "receipt.json").read_text()


@pytest.mark.parametrize("agent", [None, "", "bot", "bot contact@example.com", "x\ny@z"])
def test_requires_contact_before_live_request(tmp_path, agent):
    with pytest.raises(ValueError, match="User-Agent"):
        capture_snapshot(tmp_path / "live", cik=1, user_agent=agent)


def test_existing_real_trimmed_fixture_is_only_local_observation(tmp_path):
    fixture = next((Path(__file__).parent / "fixtures/sec").glob("*CIK0000320193*.gz"))
    path = capture(tmp_path, gzip.decompress(fixture.read_bytes()))
    summary = validate_snapshot(path)
    assert summary["filings"] == 72
    assert summary["acquisition"] == "local_import"
    assert summary["historical_availability_proven"] is False
    assert summary["observed_at"] == NOW.isoformat()


def test_cli_honors_offline_before_network(tmp_path):
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "scripts/sec_snapshot.py",
            "capture",
            "--cik",
            "1",
            "--output",
            str(tmp_path / "blocked"),
        ],
        env={**os.environ, "FINRECEIPTS_OFFLINE": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "conflicts with FINRECEIPTS_OFFLINE" in result.stderr
    assert not (tmp_path / "blocked").exists()


def test_shared_source_uses_one_bytes_object_and_checks_each_hash(tmp_path):
    path = capture(tmp_path)
    manifest_path = path / "catalog.json"
    manifest = json.loads(manifest_path.read_text())
    second = {**manifest["filings"][0], "accession": "0000000001-23-000002"}
    manifest["filings"].append(second)
    manifest_path.write_text(json.dumps(manifest))
    _, blobs = load_catalog(manifest_path)
    first_blob, second_blob = blobs.values()
    assert first_blob is second_blob
    second["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_catalog(manifest_path)


def test_shared_source_cache_never_survives_load_invocation(tmp_path):
    path = capture(tmp_path)
    load_catalog(path / "catalog.json")
    (path / "companyfacts.json").write_bytes(body() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_catalog(path / "catalog.json")


@pytest.mark.parametrize("value", [b"true", b"false", b'"42"', b"null", b"[]", b"{}"])
def test_reject_coerced_numeric_values(tmp_path, value):
    with pytest.raises(ValueError, match="JSON number"):
        capture(tmp_path, body().replace(b"42", value))
    assert not (tmp_path / "bundle").exists()
