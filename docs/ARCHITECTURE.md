# Architecture and design decisions

[English overview](../README.md) · [中文概览](../README.zh-CN.md)

FinReceipts separates generation, verification and evidence inspection. The model proposes
an answer; deterministic code checks supported numeric mentions; workflow policy decides
whether to revise, escalate or finish.

## Components

| Layer | Responsibility | Implementation |
|---|---|---|
| Data | SEC facts, replay cache, provider adapters | [tools](../src/finreceipts/tools/) |
| Financial model | Fact normalization and direct/derived metrics | [xbrl](../src/finreceipts/xbrl/) |
| Verification | Number extraction, units, tolerances and evidence matching | [verify](../src/finreceipts/verify/) |
| Agent | Answer, audit, feedback, bounded revision and escalation | [graph.py](../src/finreceipts/agent/graph.py) |
| Interfaces | CLI, FastAPI, step events and HTML receipts | [app](../src/finreceipts/app/) · [report](../src/finreceipts/report/) |
| Evaluation | Separate feedback conditions and availability policies | [evals](../src/finreceipts/evals/) · [pit](../src/finreceipts/pit/) |

## Decisions

**Use a deterministic verifier for the supported numeric subset.** Matching and arithmetic
can be exercised with fixed facts and negative cases. This makes failures inspectable;
it does not turn extraction or matching into a general semantic truth detector.

**Reuse the audit as a control signal.** Baseline answers are audited once. Verify mode
can revise; cascade mode can move unresolved work to a configured escalation model.
Finite revision limits make termination explicit even when claims remain unresolved.

**Keep feedback conditions separate.** Flags identify unsupported claims. Receipts
feedback also supplies retrieved values. The latter is evidence-fed correction, so its
results should not be described as pure model self-correction.

**Treat evidence selection as part of the result.** A receipt exposes the chosen value,
period, source and calculation where applicable. A matching amount alone does not prove
that the surrounding text attributes it to the right company or metric.

**Distinguish replay from historical availability.** A cache supports repeatable local
behavior. Historical availability needs separately trusted bytes, timestamps and bindings.
The catalog controls do not make the bundled QA dataset a validated historical benchmark.

## How to review the implementation

- [Agent tests](../tests/test_agent.py) and [cascade tests](../tests/test_cascade.py): routing and bounded revisions.
- [Verifier tests](../tests/test_verifier.py) and [metric tests](../tests/test_metrics.py): matching and financial calculations.
- [Attempt evidence](../tests/test_attempt_evidence.py): evidence retained across attempts.
- [API tests](../tests/test_api.py): request and streaming behavior.
- [Evaluation controls](../tests/test_eval_controls.py): experiment conditions and boundaries.

The code was developed with AI assistance. Design choices, implementation and verification
can be inspected in the repository; model providers and upstream data remain separate dependencies.

## Text scope boundary

The text-audit path binds a bounded annual-clause grammar, not arbitrary prose. Supported subjects
come from the selected evidence's legal entity name and its suffix-stripped form; there is no
hard-coded Apple/NVIDIA issuer allowlist. Both `FY2023 Apple revenue ...` and
`Apple FY2023 revenue ...` bind the same issuer, metric and annual period. Explicit `fiscal year
2023`, possessives and legal names such as `Apple Inc.'s` are also supported.

The complete subject and numeric clause must still parse. Unknown issuers, quarterly or segment
modifiers, non-GAAP wording, duplicated period attachments and unresolved pronoun antecedents
abstain. A recognized but wrong year is compared with that year's evidence, rather than searching
another year for the same number. A caller-supplied year filter must agree with the text. Bare
`Apple 2023 revenue ...`, two-digit fiscal years and unrestricted narrative remain outside this
grammar. Numeric-only matching remains a separate explicit mode.

Regression examples use retained SEC facts for both companies and preserve original numeric spans:
[subject and period tests](../tests/test_subject_period.py),
[existing scope tests](../tests/test_text_scope.py). The installed-package gate checks both a valid
issuer and a wrong-issuer collision through the real HTTP audit endpoint, plus the CLI entrypoint.

## Durable runtime in version 0.1.0

The original ask API remains request-scoped. The separate run API freezes the fact set,
request and implementation identity; persists JSON checkpoints and events atomically;
reserves dispatch/tool budgets before execution; and uses revisions and leases to fence
stale updates. Recovery is explicit at committed node boundaries.

The counter-audit holds the answer and evidence constant while comparing numeric-only
matching with supported annual-clause verification. This exposes a difference between
finding a matching amount and supporting its meaning, without another model call.

Read the [runtime contract](RUNTIME.md), [fault-validation evidence](VALIDATION.md)
and [interface design](UI_REDESIGN.md). This extension is local research software,
not a claim of production isolation or exactly-once provider execution.
