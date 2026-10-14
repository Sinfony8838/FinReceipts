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

## Current operating model

The public main-branch API is request-scoped and intended for local research. It does not
claim durable job recovery, multi-tenant isolation or production service guarantees.
Engineering extensions should be evaluated against their own source revision and tests.
