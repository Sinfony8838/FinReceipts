# Project story / 项目说明

[English README](../README.md) · [中文说明](../README.zh-CN.md) · [Demo](DEMO_WALKTHROUGH.md)

## Short portfolio summary

**English:** FinReceipts is a pre-alpha financial research agent harness that links supported types of extracted numeric claims to financial evidence. It combines SEC XBRL lookup, deterministic matching, a LangGraph revision/escalation loop, and HTML receipts with a local FastAPI/SSE demo. The project explores using verification as both a review interface and a control signal. Development is AI-assisted; the maintainer's contribution includes design, orchestration and review. The public release provides offline functional tests and synthetic boundary checks, with explicit limits on claim coverage, data provenance and historical evaluation results.

**中文：** FinReceipts 是一个 pre-alpha 金融研究 Agent Harness，将已提取、受支持类型的数值声明与财务证据对应起来。项目结合 SEC XBRL 查询、确定性匹配、LangGraph 修订／升级流程，以及 HTML 证据收据和本地 FastAPI/SSE 演示，探索把核验结果同时用于人工复查和流程控制。开发采用 AI 辅助方式，维护者参与设计、任务编排与审查。公开版本提供离线功能测试与合成边界检查，并明确说明声明覆盖、数据来源和历史评测的限制。

## Problem and design / 问题与设计

A financial answer can be fluent while confusing a period, unit or reported metric. A citation alone does not show whether the number matches the cited evidence. FinReceipts makes the selected evidence inspectable and reuses the audit in a bounded agent loop.

金融回答可能文字流畅，却混淆期间、单位或指标。仅有引用链接，仍需检查数字与材料是否一致。这个项目把选中的证据展开给人看，也把核验结果用于控制有限次数的修订流程。

1. **Evidence acquisition:** SEC companyfacts plus replay caching provide financial facts. A-share and announcement adapters exist, but this source release uses synthetic A-share inputs and excludes the original provider snapshots.
2. **Verification:** extraction and deterministic matching operate within a supported set of values, units, metrics and derived calculations. The verifier does not use the evaluation answer key to judge each extracted number.
3. **Control:** baseline audits without revision; verify adds feedback and bounded revisions; cascade can route unresolved answers to a configured escalation model.
4. **Inspection:** receipts expose the selected fact, calculation when applicable, period and source. API outputs preserve per-attempt audits; SSE exposes workflow steps in the local UI.

源码入口：[`tools/`](../src/finreceipts/tools/)、[`verify/`](../src/finreceipts/verify/)、[`agent/graph.py`](../src/finreceipts/agent/graph.py)、[`report/html.py`](../src/finreceipts/report/html.py)、[`app/`](../src/finreceipts/app/)。

## Decisions worth discussing / 值得讲清楚的取舍

- **Deterministic checks:** matching rules are testable and receipts are inspectable, but limited extraction, ambiguous entities/metrics, tolerances and incomplete evidence can still produce misleading reassurance. A matching number alone does not validate the surrounding meaning.
- **Two feedback conditions:** flags tell the reviser what failed; receipts feedback also supplies retrieved financial values. Improvements under receipts feedback must be described as evidence-fed correction, not pure self-correction.
- **Bounded revision and routing:** the workflow records attempts and can stop with unresolved numbers. A finite budget makes failure visible rather than implying unlimited repair.
- **Replay versus historical truth:** cached data makes local behavior repeatable. It does not prove the data was publicly available at a past cutoff. Exact availability requires independent timestamp and byte provenance.
- **Release scope:** uncertain redistribution rights led to excluding original A-share snapshots and replacing their test role with synthetic inputs. The code license does not grant rights to upstream data.

## Evidence and limits / 证据与限制

The [reproducibility record](reproducibility.md) reports 498 offline tests for the source-release preparation and installed wheel in the documented environment. Functional test counts establish behavior covered by those tests; they are not a model accuracy score. Check the repository's current CI results for the exact commit being discussed.

Earlier experiments used 100 US numeric questions across 23 companies. The historical receipts-feedback result was 96/100, with retrieved XBRL values supplied to the reviser. The historical cascade and large-baseline estimates of ¥0.69 and ¥0.73 were total costs for separate 100-item runs. These results predate later source changes and their result artifacts are not bundled here. They should not be used as current performance or savings claims. See the [README provenance notes](../README.md#historical-experiment-provenance).

当前可以展示的是本地核验、修订和收据流程，以及有范围说明的功能测试。不能据此宣称生产可用、真实模型抗注入安全、完整历史时点重放，或所有财务声明都能核验。源码中的合成用例和固定回答演示，也不能替代新的真实模型评测。

## Contribution scope / 贡献范围

The project was developed with AI assistance. Describe the maintainer's role as design, orchestration and review, and discuss concrete decisions and checks that can be demonstrated in the repository. Do not imply that the maintainer manually authored every line, trained the underlying models, or independently established historical data availability. External model providers and upstream data sources remain separate contributions.

项目使用 AI 辅助开发。介绍个人贡献时，可围绕设计、任务编排、审查以及仓库中能展示的具体取舍和检查展开，不把这些经历包装成独立手写全部代码、训练底层模型或已完成真实历史可用性验证。

## Interview follow-ups / 面试追问

- **“Why not ask another LLM to judge?”** Deterministic matching offers reproducible checks and inspectable evidence for the supported numeric subset. It does not replace broader semantic or expert review.
- **“What does green mean?”** A selected fact or derived value matched within the rules and tolerance. Show the receipt and original filing, then state the coverage limit.
- **“Does 96/100 measure self-correction?”** No. It was a historical evidence-fed condition. A new comparison should separate baseline, flags-only and receipts feedback and report errors, denominators and cost assumptions.
- **“What would you validate next?”** Broader semantic mismatch cases, independently proven availability data, fresh provider comparisons and a reviewed dependency/environment matrix. These are validation needs, not completed capabilities.
