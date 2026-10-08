# FinReceipts

[中文说明](README.zh-CN.md) · Pre-alpha, local research software · [Not investment advice](DISCLAIMER.md)

FinReceipts is a finance research agent harness that verifies **extracted, supported types of numeric claims** against available financial evidence. A deterministic verifier supplies feedback for answer revision and small-to-large model routing, and renders matched facts as HTML receipts. It does not extract every possible claim, validate all prose, or establish that an answer is comprehensively true.

## Why this project

A plausible financial answer can still contain a wrong number. Reviewing it means finding the issuer, fiscal period, unit and filing behind each claim. FinReceipts makes that review visible: extract supported numeric claim types, look for matching financial facts, and attach an inspectable receipt to each extracted number.

The same audit also drives the agent workflow:

```text
Question → model answer → numeric extraction → deterministic fact matching
                              ↑                         │
                              └── revise / escalate ────┤
                                                       └→ answer + HTML receipts
```

A receipt contains the selected XBRL concept, reported value, period, filing date and a link to the SEC filing. Reviewers can inspect the evidence rather than relying on the model's confidence. Revision is budgeted; an answer can still finish with unresolved numbers.

### A concrete before / after

The key-free Apple demo starts with a deliberately scripted wrong answer:

- Before: “Apple reported revenue of **$390.0 billion** for fiscal 2023.” The verifier finds no matching fact.
- After one scripted revision: “Apple's revenue … was **$383.3 billion**.” The receipt points to **$383,285,000,000**, the selected revenue concept, the period ending **2023-09-30**, and the [2023 10-K](https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/0000320193-23-000106-index.htm). The rounded answer matches under the configured tolerance.

This demonstrates the wiring, not model reasoning: both answers are canned. See the [three-minute demo](docs/DEMO_WALKTHROUGH.md) for exact inputs and expected outputs, or the [project story](docs/PROJECT_STORY.md) for design choices and contribution scope.

## What is included

- SEC XBRL fact lookup, numeric extraction and deterministic matching
- LangGraph answer → verify → revise / escalate workflow
- Flags-only feedback and receipts feedback that also supplies retrieved values
- HTML receipts, FastAPI endpoints and server-sent events
- Explicit timestamp/catalog evaluation controls and synthetic injection-boundary tests
- [Observed-now SEC snapshot bundles](docs/sec-snapshots.md) with byte hashes, collection receipts and bound catalogs; no historical availability claim
- A-share HTTP adapters with **synthetic** offline test inputs

A supported receipt means a match under the verifier's rules. An unsupported result means no supporting match was found; it does not by itself prove the claim false. Matching depends on metric coverage, entity/period interpretation, units, tolerances and the evidence supplied.

## Local quick start

Python 3.11 or newer is declared. Run these commands in a POSIX shell (Linux/macOS), using your chosen environment:

```sh
git clone https://github.com/Sinfony8838/FinReceipts.git
cd FinReceipts
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev,web]"
FINRECEIPTS_OFFLINE=1 FINRECEIPTS_LLM_PROVIDER=fake pytest -m "not live"
FINRECEIPTS_HOST=127.0.0.1 FINRECEIPTS_LLM_PROVIDER=fake \
  FINRECEIPTS_OFFLINE=1 FINRECEIPTS_CACHE_DIR=tests/fixtures/sec finreceipts-api
# Open http://127.0.0.1:8000
```

Select **AAPL FY2023**, choose **verify** and **receipts**, then click **Run with SSE**. Expect `answer → verify → revise → answer → verify` and a receipt for $383.3 billion. The [walkthrough](docs/DEMO_WALKTHROUGH.md) also provides a model-free audit command and troubleshooting.

Installation can access a package index; these are instructions, not a claim of a tested clean install on every supported platform. The offline SEC cache contains company facts for AAPL and NVDA, not all companies in the bundled US evaluation dataset. Fake-model output is a demonstration, not a quality benchmark.

The quick start above requires a source checkout, which supplies `examples/` and `tests/fixtures/sec/`. A standalone installed wheel does not bundle those reports or SEC fixtures. Wheel-only users must provide an authorized SEC cache via `FINRECEIPTS_CACHE_DIR` and, if using static example reports, their own `examples/` directory in the working directory. A successful package install alone does not provide the complete offline demo.

Docker configuration is provided as an alternative local setup:

```sh
docker compose up --build
```

The application is a local research demo without a production authentication/security review. Keep it on loopback; do not expose it publicly with paid API credentials. See [reproducibility](docs/reproducibility.md) for validation boundaries and [LLM access](docs/llm_access.md) before enabling a real provider.

## Interfaces and layout

- `finreceipts`: `audit`, `eval`, `record`, `record-ashare`, `gen-eval`, `gen-eval-cn`
- `GET /health`, `GET /`, `POST /api/ask`, `POST /api/audit`
- `POST` or `GET /api/ask/stream`: agent-step events
- `src/finreceipts/tools/`: SEC, A-share and announcement clients plus record/replay cache
- `src/finreceipts/verify/`, `xbrl/`: claim matching and financial metric selection
- `src/finreceipts/agent/`, `llm/`: workflow and model adapters
- `src/finreceipts/pit/`, `redteam/`: availability policy and synthetic boundary checks
- `src/finreceipts/evals/`, `report/`, `app/`: evaluation, receipts and local UI

## Historical experiment provenance

Earlier saved experiments dated 2026-10-06–07 used 100 US numeric QA items across 23 companies and a mainland MiniMax Anthropic-compatible endpoint. These predate subsequent continuation and source-release edits. Their provider outputs and result artifacts are **not included in this source-first release**, and no fresh live-model benchmark is claimed here.

The historical receipts-feedback run recorded 96 correct answers out of 100. The reviser was given verifier-retrieved XBRL values: this is **evidence-fed correction, not pure model self-correction**. Flags-only feedback is a distinct condition. Historical small baseline and flags-only runs each had one request error: accuracy used all 100 items, while unsupported-answer fractions used 99 successful responses.

The historical cascade and large-baseline cost estimates of approximately **¥0.69 and ¥0.73 are total CNY costs for their respective 100-item runs**, not mean costs per item. They depend on historical token accounting and assumed prices, not current billing. In those experiments “small” and “large” referred to MiniMax-M3 without and with thinking, rather than independently sized models. These retained provenance notes do not establish current implementation performance or reproduce the omitted experiments.

## Point-in-time and injection limits

Legacy date-based filtering is not proof of historical public availability. Explicit catalog/cutoff controls require independently established raw bytes, timestamps and trusted fact bindings. The bundled US QA data is not a validated real-data point-in-time benchmark. Synthetic fake-model boundary tests do not measure real-model injection resistance. See [evaluation controls](docs/eval-controls.md) and [PIT/boundary contracts](docs/pit-redteam.md).

## Data and licenses

Three attributed SEC JSON fixtures are retained. Original Eastmoney/cninfo snapshots and their generated A-share dataset are excluded; synthetic A-share inputs exercise local behavior, not company performance. Live retrieval is separate and subject to upstream access and reuse terms. See [data sources](docs/data_sources.md).

Project code retains [Apache-2.0](LICENSE). Dependencies and upstream data retain their own terms, including MPL-covered dependency components; see [third-party notices](THIRD_PARTY_NOTICES.md). No dependency wheelhouse or container image is distributed here.

## Prior work

Numeric grounding and citation verification have established precedents, including [FinDVer](https://github.com/yilunzhao/FinDVer), XBRL Agent, ALCE, Self-RAG and FActScore. This project explores using the same deterministic numeric verifier as a revision, routing and measurement signal; it does not claim to invent citations or financial verification.
