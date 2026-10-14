# Changelog

## 0.1.0 — Evidence Workbench (preview)

### Added

- Durable financial research runs with frozen facts, JSON checkpoints and atomic state/event writes.
- Persisted model/tool reservations before dispatch, revision checks, lease fencing and explicit recovery.
- Cursor-based event catch-up, JSON evidence exports and offline integrity verification.
- Counter-audit of numeric matching versus supported annual clauses using the same answer and evidence.
- Observed-now SEC snapshots with raw-response hashes, collection receipts and bound catalogs.
- Unified Chinese workbench and quick-ask interface with light, dark and system themes.
- Responsive result tables, concise execution timelines and expandable original evidence.
- Offline fault tests, browser acceptance scripts and documented reproduction procedures.

### Scope

This is a local research preview. Recovery is at committed node boundaries; unknown external calls
may repeat. Dispatch limits are not billing caps. Numeric matches do not establish whole-answer
correctness, and snapshot hashes do not prove historical public availability.
Offline/fake-model demonstrations are not real-model quality benchmarks.

See [runtime contracts](docs/RUNTIME.md), [validation](docs/VALIDATION.md) and
[UI acceptance](docs/UI_REDESIGN.md).
