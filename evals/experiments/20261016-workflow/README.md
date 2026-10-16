# 2026-10-16 workflow experiment

Real MiniMax responses on 30 preselected questions generated from the repository's
recorded AAPL/NVDA SEC fixtures (FY2023–2025). This is a two-company diagnostic experiment,
not a representative financial benchmark or a game-marketing evaluation.

- `manifest.json`: frozen configuration, sample/fact hashes and SEC source mappings.
- `candidate-pool.jsonl`, `sample.jsonl`: all 66 candidates and the balanced 30-item sample.
- `facts.json.gz`: supported-concept projection of the two existing SEC fixtures, used by
  the verifier for all three conditions. Decompressed bytes match `facts_sha256`.
- `baseline/`, `flags/`, `receipts/`: per-item model answers, audits and original summaries.
- `ledger.json`: HTTP attempt reservations and provider usage, including the probe; no keys
  or request headers. Raw summaries omit cache-read tokens; use the reconciled metrics.
- `reconciled.json`: recomputed answer/audit results and cache-aware cost estimates.
- `provenance.json`: source identity, environment and the failed online preparation attempt.

From the repository root, with dependencies installed, validate without paid calls:

```sh
python scripts/check_workflow_evidence.py evals/experiments/20261016-workflow
```

See [the experiment report](../../../docs/WORKFLOW_EVALUATION.md) for denominator definitions,
limitations and live reproduction. Actual provider bills were not reconciled. Original SEC
fixture retrieval timestamps are unknown; this package does not prove historical availability.
The scoped `.gitattributes` preserves original evidence line endings for cross-platform hashes.
