# FinReceipts

[中文说明](README.zh-CN.md) · Pre-alpha, local research software · [Not investment advice](DISCLAIMER.md)

FinReceipts is a finance research agent harness that verifies **extracted, supported types of numeric claims** against available financial evidence. A deterministic verifier supplies feedback for answer revision and small-to-large model routing, and renders matched facts as HTML receipts. It does not extract every possible claim, validate all prose, or establish that an answer is comprehensively true.

## What is included

- SEC XBRL fact lookup, numeric extraction and deterministic matching
- LangGraph answer → verify → revise / escalate workflow
- Flags-only feedback and receipts feedback that also supplies retrieved values
- HTML receipts, FastAPI endpoints and server-sent events
- Explicit timestamp/catalog evaluation controls and synthetic injection-boundary tests
- A-share HTTP adapters with **synthetic** offline test inputs

A supported receipt means a match under the verifier's rules. An unsupported result means no supporting match was found; it does not by itself prove the claim false. Matching depends on metric coverage, entity/period interpretation, units, tolerances and the evidence supplied.

## Local quick start

Python 3.11 or newer is declared. From a source checkout, in your chosen environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev,web]"
FINRECEIPTS_OFFLINE=1 FINRECEIPTS_LLM_PROVIDER=fake pytest -m "not live"
FINRECEIPTS_HOST=127.0.0.1 FINRECEIPTS_LLM_PROVIDER=fake \
  FINRECEIPTS_OFFLINE=1 FINRECEIPTS_CACHE_DIR=tests/fixtures/sec finreceipts-api
# Open http://127.0.0.1:8000
```

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
