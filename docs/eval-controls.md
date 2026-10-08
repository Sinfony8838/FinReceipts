# Evaluation controls and per-number evidence

This offline continuation adds controls to the evaluation CLI and Python runner.
It does not provide a real SEC provenance catalog, establish a real-data PIT
benchmark, or run a model service. Synthetic fixtures test the local mechanisms.
See [reproducibility.md](reproducibility.md) for installation and verification limits.

## Defaults and opt-in flags

Existing defaults are preserved: `eval` uses each dataset item's date-only
`as_of` for the legacy **filed-date** filter. This is not the explicit timestamp,
raw-byte, trusted-fact-binding policy. `--no-pit` disables that legacy filter.

The following switches are off by default:

- `--pit-catalog PATH` plus `--pit-cutoff TIMESTAMP`: use a host-supplied local
  catalog, verified raw bytes, and an explicit timezone-aware fixed cutoff.
- `--pit-policy observed_replay|acceptance_proxy`: default `observed_replay`.
  Acceptance remains an approximate availability proxy.
- `--pit-prefer original|latest`: default `original`.
- `--pit-acceptance-lag-seconds NUMBER`: nonnegative finite lag; a nonzero lag
  is valid only for `acceptance_proxy`.
- `--guard-tools`: exact keys/types plus task ticker/year scope.
- `--mark-untrusted`: wrap tool results and add the untrusted-data instruction.
- `--sealed-tool-cache`: in-memory, run-local cache, bound to policy, catalog,
  selection preference, tool name and arguments. Replants from another context
  are refused. Its hash envelope is unsigned and **not attacker authentication**.

All three tool controls require `--tools`; the sealed cache additionally requires
an explicit PIT catalog/context. Policy options without a catalog, a catalog
without a cutoff, date-only/naive cutoffs, and catalog plus `--no-pit` fail before
creating an LLM client or truncating result files. Explicit SEC PIT rejects CN
items, including existing prefix/suffix stock-code aliases. Ordinary CN evaluation
continues to use the legacy path. Other callers combining providers themselves
remain responsible for identifying the policies used by each market.

### Important evaluation distinction

The explicitly supplied fixed cutoff is authoritative for every item. Dataset
`as_of` dates do not override it. Existing expected answers are still scored as
provided; this change does **not** establish that their values or evidence were
available at the chosen cutoff. Review the dataset against the catalog/policy
before treating accuracy as a PIT-valid result. Every new run and item records
`ground_truth_pit_validated: false`; the CLI also prints this caveat.

The host must establish provenance independently. A syntactically valid catalog
with consistent hashes can still contain false collection timestamps. This loader
verifies structure and byte integrity, not who supplied it or whether the history
is true. Do not populate missing dates/hashes from model guesses or present-day
fetches and call that historical observation.

## Local catalog format (schema version 1)

The JSON root has exactly `schema_version: 1` and `filings: [...]`. Each filing has:

- Required: `accession`, `cik`, `form`, `sha256`, `fact_hashes`, `raw_path`
- Optional: `first_observed`, `observation_source`, `acceptance`,
  `acceptance_source`, `revision_of`
- Time evidence: exactly `{"raw": "ISO-8601 timestamp with offset", "precision":
  "exact|second|minute|day"}`; optional times may be null
- `sha256`: digest of the actual preserved raw filing bytes
- `fact_hashes`: list of trusted extraction bindings produced with the existing
  `finreceipts.pit.fact_hash` contract, not hashes of the model's answer
- `raw_path`: relative file path beneath the catalog's directory; absolute paths,
  parent traversal, escaping symlinks, absent files and mismatched hashes fail

Duplicate JSON fields and accessions are rejected. Missing availability evidence
or fact bindings never admits rows. An empty catalog is valid but admits nothing.
Unknown, late or unbound rows never fall back to the legacy provider; ambiguous
streams are dropped under the existing fail-closed selector. Source bytes must
already exist locally; the loader fetches nothing.

The SEC facts adapter passes raw parsed companyfacts rows into PIT selection
**before** annualisation. The catalog must bind the rows actually present in the
provided SEC cache. This package does not construct that trusted extraction or
verify cross-filing amendment linkage.

## CLI and Python usage

Example command, only after the local catalog and dataset have been reviewed:

```sh
FINRECEIPTS_OFFLINE=1 FINRECEIPTS_LLM_PROVIDER=fake \
FINRECEIPTS_CACHE_DIR=tests/fixtures/sec \
python -m finreceipts.cli eval --model fake \
  --dataset /path/to/us-cutoff-reviewed.jsonl \
  --pit-catalog /path/to/catalog.json --pit-cutoff 2024-01-01T00:00:00Z \
  --pit-policy observed_replay --tools --guard-tools --mark-untrusted \
  --sealed-tool-cache --out-dir /path/to/new-output-directory
```

`fake` is an offline smoke path, not a model-quality experiment. A complete
installation including LangGraph is required to run the graph. There is no
implicit permission to make paid calls or download live data.

Python callers pass `pit_context=PitContext(...)` and
`facts_provider=ticker_to_unfiltered_xbrl_rows` to `run_eval`. Pass the three
booleans in `RunConfig`. Omitting both PIT arguments preserves the legacy
provider. Supplying only one is an error. Validation runs before output creation.

## New trace fields

`items.jsonl` now includes:

- `audit_schema_version: 1`
- `audit`: final per-number verifier snapshots, each including complete `claim`,
  `verdict`, `reasons`, relative error and `evidence` when present
- `attempts[*].audit`: snapshots belonging to that attempt's exact answer/model,
  preserving existing count and answer fields
- `verification_context`: enforcement kind; explicit policy/catalog fingerprints
  and limits where applicable; original dataset date

Each claim preserves exact text and character span. Evidence preserves all matched
facts, periods, accession, concept, raw numeric value, unit, filing/source URL,
expected value/unit, formula and notes. This is the deterministic verifier's match,
not evidence that the model itself used a source or that an unbound match is a
semantically perfect interpretation of the answer. Unsupported evidence stays
null. Non-finite numeric values use explicit strings `NaN`, `Infinity` or
`-Infinity` so the audit payload is valid strict JSON.

An empty `audit: []` means a completed audit found no numeric claims. `audit: null`
means the audit is unavailable, for example execution failed. Historical files
and attempts with no trace are not backfilled. If graph invocation fails after an
earlier revision, the existing runner still lacks partial-state recovery and may
lose that item's earlier attempts; this continuation does not claim otherwise.

`summary.json` has `result_schema_version: 2`, the config flags, and enforcement
metadata. Strict context is labelled `host_supplied_not_independently_authenticated`.
The merge script refuses incompatible schema/enforcement contexts rather than
pooling different cutoffs/catalogs into one result. The injection-eval script
retains its legacy string `attempts` field and adds `attempt_audits` and final
`audit`; no new injection/model run was performed here.

## Verification scope

Tests with synthetic catalogs, facts and FakeLLM responses establish deterministic
local behavior only. They do not establish real SEC historical availability,
real-model injection resistance, new A-share performance, or any improvement in
previously recorded model metrics. See [reproducibility](reproducibility.md) for current checks and remaining build/installation limits, separately from historical evidence.
