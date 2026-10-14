# Observed-now SEC snapshots

The snapshot builder connects real companyfacts bytes to the existing explicit PIT catalog format. It does **not** build a historically valid filing archive from today's aggregate API response. No LLM or API key is used.

## Offline demonstration

From an installed source checkout, create a new output directory:

```sh
python scripts/sec_snapshot.py capture \
  --local-json tests/fixtures/sec/data_sec_gov_api_xbrl_companyfacts_CIK0000320193_json__587c133c2376.json.gz \
  --output .cache/aapl-observation
python scripts/sec_snapshot.py validate .cache/aapl-observation
```

The bundled fixture is attributed, trimmed SEC-derived data, not an original full HTTP response. This command records a **new local observation now**. It never reuses gzip metadata, a file modification time, a filing date, or an acceptance date as the observation time. Expected summary: 72 accession groups, `acquisition: local_import`, and `historical_availability_proven: false`. Timestamps vary per run. The same directory cannot be overwritten.

## One live capture

Set `FINRECEIPTS_SEC_USER_AGENT` to a descriptive project name and an authorized contact email in your local environment. Do not commit personal contact details or secrets. Placeholder `contact@example.com` is rejected.

```sh
# First set FINRECEIPTS_SEC_USER_AGENT locally to your authorized contact.
# FINRECEIPTS_OFFLINE must not be enabled for this explicitly live command.
python scripts/sec_snapshot.py capture --cik 320193 --output .cache/aapl-live-observation
python scripts/sec_snapshot.py validate .cache/aapl-live-observation
```

This makes one HTTPS request to the canonical SEC companyfacts endpoint. It retains the proxy configuration, verifies TLS through the HTTP client, refuses redirects and non-200 responses, does not retry, and bounds the decoded response to 32 MiB. It stores the decoded response body exactly, without JSON reserialization; these are not compressed wire bytes. It does not store the contact header, cookies, or proxy credentials. A configured SOCKS proxy may require the optional `httpx[socks]` dependency; install only through your approved package source and retain network controls.

Run sequentially and sparingly; do not use parallel loops or evade a refusal. SEC fair-access guidance imposes a total limit of 10 requests per second across a user's machines. The builder is a single-shot collector, not a cross-process rate limiter. See [SEC developer resources](https://www.sec.gov/about/developer-resources) and [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

## Bundle contract

- `companyfacts.json`: exact decoded response bytes, or exact decompressed local input bytes.
- `receipt.json`: canonical source URL, acquisition kind, SHA-256, byte count, collection start/end timestamps, monotonic elapsed duration, and explicit provenance limitations. `source_verified_by_collector` distinguishes the collector's own successful HTTPS response from unverified local imports; it is not a third-party attestation.
- `catalog.json`: a compatible v1 manifest. Each accession group binds to the entire same aggregate snapshot and to hashes of deterministically parsed facts. The raw bytes are **not** individual filing documents. `catalog.json` is written last as the completion marker; failed filesystem writes can leave an incomplete directory, which is not a valid bundle.

`validate` reloads the catalog, checks raw bytes and receipt fields, regenerates the fact bindings, and rejects disagreement, extra acceptance dates, guessed revision parents, changed observation timestamps, or tampered hashes. Keep the original receipt, catalog and bytes together. Validation checks internal consistency; a person who can rewrite all three can fabricate a consistent bundle. It provides no signature, trusted timestamp, independent archive attestation, or guarantee of clock accuracy. Clock rollback during capture is rejected, but system clock skew cannot be authenticated here.

## Time and amendment semantics

The observation timestamp is the collector's timezone-aware UTC clock after acquisition finishes. A cutoff before that timestamp is refused under `observed_replay`; a cutoff at or after it can admit the exact observed bytes and bound facts. All accessions share the snapshot's observation time, including old filings.

The builder deliberately supplies **no acceptance timestamps**. `acceptance_proxy` therefore returns `missing_availability_provenance`, even if a fact has a filing date decades ago. Current companyfacts values and metadata can reflect subsequent processing or revisions. The SEC describes these endpoints as aggregates updated as submissions are disseminated; a current response is not proof of historical availability.

A `10-K/A` or `10-Q/A` form remains visible as an amendment, but the builder does not infer its parent accession or a supersession order from neighboring rows. `revision_of` remains unset. Verified original filing extraction, source-backed amendment relationships, and independently collected historical snapshots are future work.

For explicit catalog evaluation, use the generated `catalog.json` with the existing [evaluation controls](eval-controls.md). Validate the complete bundle first and preserve the host trust boundary. Merely importing an untrusted manifest does not authenticate its claims. The UI's legacy `as_of` date filter does not automatically use this catalog.

## Validation record for this iteration

A bounded live AAPL companyfacts capture returned HTTP 200 on 2026-10-08. The native collector recorded `2026-10-08T11:12:35.903314+00:00`, 3,789,099 decoded bytes and 72 accession groups. SHA-256: `73a86c6aedc31f77cac2ea4df5f80f0b3bd7e6eb58bb4e01444fbedf3afb9c43`. The raw live bundle is not committed; this note is a local verification report, not independently reproducible historical evidence. Future responses and hashes may differ.

Synthetic tests cover cutoffs, missing proxy provenance, amendment non-inference, receipt/catalog tampering, duplicate JSON keys, malformed input, clock rollback, identity mismatch, no overwrite, offline enforcement, and single-request handling of redirects/403/429/500. A real bundled fixture also exercises the offline builder. No real-model benchmark, investment conclusion, or production readiness claim follows from these checks.
