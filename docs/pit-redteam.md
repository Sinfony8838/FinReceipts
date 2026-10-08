# PIT and synthetic injection boundary modules

These contracts describe the standalone `finreceipts.pit` and `finreceipts.redteam` modules. The current evaluation runner additionally integrates explicit catalog/cutoff controls; see [evaluation controls](eval-controls.md). Legacy date-only filtering remains distinct from strict replay evidence.

An earlier module revision corrected findings against first-release commit `47aa821`, which had failed independent review despite passing its then-existing tests. The default policy version is `pit-v2`; the URL boundary version is `redteam-boundary-v2`. This historical context does not establish a new independent review or production security guarantee. Earlier review/handoff documents are not included in this source-first release.

## Availability contract

`Policy` requires an explicit timezone-aware cutoff timestamp. An `EvalItem.as_of`
date cannot silently become a cutoff. The dataset's fiscal period and `filed`
date are not evidence of exact public availability. SEC acceptance and public
availability are different events; public dissemination may lag acceptance and
there is no reliable uniform first-publication timestamp in this archive.

* `observed_replay` permits only bytes with matching SHA-256 and independently
  recorded first observation at or before the cutoff, including observation
  provenance. This proves collector replay availability, not earliest availability
  to every market participant. A document first collected today cannot be used
  in a historical strict replay simply because its accession or period is old.
* `acceptance_proxy` requires acceptance evidence/provenance and labels every
  decision `approximate=True`, including refusals and insufficient evidence.
  Its optional lag is an explicitly configured assumption, never a claim of
  measured dissemination latency. The default lag is zero; no hidden delay or
  numeric tolerance is added to make tests pass. Do not merge proxy metrics with
  strict replay metrics or present proxy results as historical availability proof.

`TimeEvidence` retains the source ISO string, explicit offset, precision, and UTC
lower/upper bounds. Naive times and date-only strings are rejected. Convert SEC
local header times upstream using independently established timezone/DST evidence;
do not guess an offset. `second`, `minute`, and `day` represent uncertainty over
the entire source bucket. A bucket crossing the cutoff is refused. `exact` is
only for a collector timestamp known to that instant; it must not upgrade a
coarse SEC header time. Day buckets are 24 hours at the supplied fixed offset;
ambiguous timezone/DST civil dates require stronger upstream evidence, not this
conversion. The upper bound comparison is inclusive, with no extra grace period.

`Filing` retains accession, CIK, form, raw-content SHA-256, acceptance and observation
evidence/source, optional explicit amendment parent, and hashes of trusted extracted
facts. `Catalog.decide` returns `allowed`, `refused`, or `insufficient` plus reason,
policy fingerprint, approximation flag, and both chosen availability bounds.
Unknown accessions, absent bytes/times/provenance, conflicting accession metadata,
changed content, and observation before acceptance do not pass. Persist the
`Policy.descriptor()` alongside the decision to retain its version and cutoff.

`Catalog.decide_fact` additionally binds CIK/form and the complete financial row
(accession, concept, taxonomy, unit, value, dates and filed date). These hashes
must come from a trusted extraction of the recorded bytes. **Do not** create
trusted bindings from model output or a current companyfacts download and attach
them to an old observation. Hashes verify consistency; this module does not parse
filing HTML/XBRL or prove that the host's extraction/provenance is authentic.
Missing fact bindings are insufficient evidence.

`filter_facts` retains all decisions and returns copies of admitted rows.
`select_annual` handles a single entity/concept/unit stream after admission. It
uses a partial order of admitted availability intervals: A is definitely before B
only if A's upper bound is strictly less than B's lower bound. It selects only a
unique possible earliest/latest filing identity. Competing extremal identities,
including equal-value filings or intervals sharing an endpoint, raise explicit
ambiguity. Overlap among versions that cannot affect the requested extreme does
not prevent selecting a definitely separated unique earliest/latest filing.
Before selection, conflicting financial rows within any admitted accession are
refused, including versions that would not be selected; identical bindings are
deduplicated. Conflicting periods are refused. Period end never backdates availability.
Formal amendments retain their own accession/form and optional `revision_of`.
A later 10-K comparative/restatement also retains its later accession; it is
not automatically classified as a formal amendment. Earliest observed vintage
need not be the historically first filing if collector coverage is incomplete.
`revision_of` is descriptive metadata with format/self-reference validation only.
The catalog does not validate parent existence, cross-record CIK/stream consistency
or cycles, and selection does not traverse this field. Do not describe it as a
verified amendment graph.
An empty series must be reported as absent/insufficient by the caller, not as
successful verification.

## Cache and checkpoint isolation

`pit.isolation.cache_key` includes request, policy mode/version/cutoff/explicit lag,
and the entire evidence catalog fingerprint (identities, hashes, observation and
acceptance metadata, fact bindings). `Envelope` seals JSON under that scope and a
separate cache/checkpoint purpose. Restoring with another cutoff, policy version,
mode, lag, catalog or purpose fails; modified payloads fail integrity checks.
It does not serialize executable Python objects. It is not a signature and cannot
authenticate storage controlled by an attacker who can recompute all hashes.
The existing `JsonCache` and LangGraph checkpoints are not changed by this patch.

## Synthetic red-team scope

`make_pairs()` produces six deterministic clean/injected announcement pairs:
pseudo-system instruction, forged verification success, forged citation, cutoff
relaxation, unauthorized tool, and unauthorized URL. Accession, URL and financial
facts are identical within a pair; raw hashes differ because injected bytes differ.
Each variant has its own trusted synthetic catalog. These are labelled synthetic,
not real SEC filings, and should not be mixed into historical evidence records.

The one-turn `run_pair` accepts only the existing `FakeLLM` class. Policy is passed
in the trusted system argument; document text is JSON-encoded as untrusted user
data. The policy and task/tool scope must match across paired variants. Delimiters
and wording do not establish a security boundary for a real model: the deterministic
host validators do the checks.

The narrow structured answer contains one numeric `value`, `unit`, and nonempty
`citations`. Unknown keys (including policy overrides), duplicate JSON keys, invalid
numeric types, nonfinite values and unsupported numbers are rejected. Every receipt
binds accession, full raw hash, exact allowed source URL, Unicode `[start,end)`
offsets, and the complete financial facts quote. Invented or altered receipts,
injection-text quotes, missing citations, duplicate receipts and unavailable evidence
are rejected. This is an oracle-backed synthetic numeric task, not a general
natural-language claim verifier.

`validate_tool` enforces exact argument keys and types and the host's ticker,
metric and year scope. `as_of`, `point_in_time`, cutoff overrides and other extra
arguments are rejected. URL validation requires an exact supplied allowlist entry
under HTTPS `www.sec.gov/Archives/edgar/data/`; credentials, nonstandard ports,
queries and fragments are refused, as are traversal segments, percent escapes,
backslashes or controls. Original URL strings are checked before `urlsplit`:
all whitespace and Unicode category C characters (including C0, DEL, C1 and format
controls) are rejected even when explicitly allowlisted. No tool/URL is executed.
Even a valid proposed tool call
is incomplete in this one-turn harness and is not marked accepted.
Real HTTP integration must validate redirects and the final destination at dispatch.

Outcome reports retain variant, acceptance/refusal reasons, proposed-tool count,
policy and document hashes, `evaluation_kind=synthetic_fake_model_boundary_regression`,
and `real_model_robustness_measured=False`. Scripted malicious outputs test the
deterministic rejection boundary; passing these tests is **not** evidence of real
model injection resistance, a measured ASR reduction, or production deployment.

## Host integration requirements

The current evaluation integration implements part of this contract, as described in [evaluation controls](eval-controls.md). The checklist below also records host responsibilities; it is not a claim that trusted real-data provenance, every caller, or a complete production tool loop is implemented:

1. Construct a trusted immutable `Policy` outside model/document control. Add explicit
   timestamp/mode fields separately from legacy `EvalItem.as_of` and `RunConfig`.
   Require a timestamp sidecar; existing 100-item JSONL lacks availability evidence.
2. Collect raw filing versions and observation/acceptance provenance into a catalog;
   bind facts from a trusted contemporaneous extraction. Filter **before** selecting
   annual values, building tool results, generating questions/ground truth, creating
   prompts, calculating metrics, verifying answers, or producing receipts.
3. Replace every provider/feedback/verifier access with the same scoped admitted
   fact lookup. Do not retain a fallback to unfiltered companyfacts or `as_of=None`.
   Use explicit per-concept `select_annual` and existing concept fallback order.
4. Validate arguments immediately before tool dispatch and pass cutoff only from
   trusted policy. Do not use document-supplied URLs/metadata as an allowlist.
5. Scope caches and checkpoints using `cache_key`/`Envelope`, validate on every restore,
   and recheck evidence bytes and citations at final output. Exclude incompatible
   old caches/checkpoints rather than migrating them by assuming availability.
6. Implement a genuine tool loop and general claim-to-receipt adapter separately.
   Report policy/provenance coverage and refusals, and keep real-model evaluation,
   missing evidence, successful clean controls, and attempted attacks distinct.

Example independent gate (no network or model):

```python
from datetime import datetime
from finreceipts.pit import Catalog, Filing, Policy, TimeEvidence, content_hash

raw = b"recorded synthetic filing"
policy = Policy(datetime.fromisoformat("2024-01-05T00:00:00+00:00"))
record = Filing(
    accession="0000000001-24-000001",
    cik=1,
    form="8-K",
    sha256=content_hash(raw),
    first_observed=TimeEvidence("2024-01-04T12:00:00Z", "exact"),
    observation_source="local-fixture/collector-receipt-1",
)
decision = Catalog((record,)).decide(record.accession, raw, policy)
assert decision.allowed and not decision.approximate
```

For offline regression commands and validation boundaries, see [reproducibility](reproducibility.md).
