"""Synthetic local sidecars: integrity validation is not provenance authentication."""

import json
from dataclasses import asdict

import pytest

from finreceipts.pit import Filing, TimeEvidence, content_hash, fact_hash
from finreceipts.pit.catalog_io import load_catalog
from tests.conftest import make_fact


@pytest.fixture
def manifest(tmp_path):
    accession = "0000000001-23-000001"
    fact = make_fact(100e9, accn=accession)
    content = b"synthetic filing bytes, not a real SEC retrieval"
    (tmp_path / "filing.bin").write_bytes(content)
    row = asdict(
        Filing(
            accession,
            1,
            "10-K",
            content_hash(content),
            first_observed=TimeEvidence("2023-11-03T21:00:00Z"),
            observation_source="synthetic-test-only",
            fact_hashes=(fact_hash(fact),),
        )
    )
    row["fact_hashes"] = list(row["fact_hashes"])
    row["raw_path"] = "filing.bin"
    path = tmp_path / "catalog.json"
    data = {"schema_version": 1, "filings": [row]}
    path.write_text(json.dumps(data))
    return path, data, fact, content


def test_load_catalog_binds_exact_bytes_and_facts(manifest):
    path, _, fact, content = manifest
    catalog, blobs = load_catalog(path)
    record = catalog.record(fact.accn)
    assert record is not None and fact_hash(fact) in record.fact_hashes
    assert blobs == {fact.accn: content}
    assert record.observation_source == "synthetic-test-only"


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": "0" * 64},
        {"fact_hashes": "wrong"},
        {"fact_hashes": [1]},
        {"cik": True},
        {"first_observed": {"raw": "2023-01-01"}},
        {"first_observed": {"raw": "2023-01-01T00:00:00", "precision": "exact"}},
        {"observation_source": 7},
        {"extra": "ignored?"},
        {"revision_of": 3},
    ],
)
def test_malformed_catalog_refused(manifest, change):
    path, data, _, _ = manifest
    data["filings"][0].update(change)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_catalog(path)


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"filings": {}},
        {"extra": 1},
    ],
)
def test_malformed_root_refused(manifest, change):
    path, data, _, _ = manifest
    data.update(change)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_catalog(path)


def test_missing_and_duplicate_sources_refused(manifest):
    path, data, _, _ = manifest
    data["filings"].append(dict(data["filings"][0]))
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="duplicate"):
        load_catalog(path)
    data["filings"] = data["filings"][:1]
    data["filings"][0]["raw_path"] = "missing.bin"
    path.write_text(json.dumps(data))
    with pytest.raises(FileNotFoundError):
        load_catalog(path)


@pytest.mark.parametrize("kind", ["absolute", "parent", "symlink"])
def test_raw_path_cannot_escape_manifest_directory(manifest, tmp_path, kind):
    path, data, _, content = manifest
    outer = tmp_path.parent / f"{tmp_path.name}-outside.bin"
    outer.write_bytes(content)
    if kind == "absolute":
        raw_path = str(outer)
    elif kind == "parent":
        raw_path = "../" + outer.name
    else:
        (tmp_path / "escape.bin").symlink_to(outer)
        raw_path = "escape.bin"
    data["filings"][0]["raw_path"] = raw_path
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="beneath"):
        load_catalog(path)


def test_duplicate_json_fields_are_rejected(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text('{"schema_version":1,"schema_version":2,"filings":[]}')
    with pytest.raises(ValueError, match="duplicate JSON field"):
        load_catalog(path)
