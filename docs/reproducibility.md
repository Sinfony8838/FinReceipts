# Source-release reproducibility and validation boundaries

## Source identity and included evidence

This is a filtered source candidate derived from the continuation of baseline commit `a97d3858ca1a986e34f3f19b1cd4ea5fa1505779`. The candidate replaces uncertain A-share snapshots with synthetic inputs and omits historical model outputs, result bundles and demo media. Tests of the earlier source tree must not be presented as tests of this filtered tree.

On 2026-10-08 UTC, the release preparation run reported **498 passed** for the filtered candidate's offline test suite, including two explicit synthetic replay/arithmetic oracle checks. This is a local functional test result, not a fresh model benchmark, a hosted CI result or evidence that all declared Python/dependency versions work. The release validation record must identify the exact final file manifest and environment; documentation-only changes should be included in its final checks.

The source distribution has no authoritative resolved/hash lock and does not include a dependency wheelhouse. An earlier supplied file named `requirements-lock.txt` contained no package requirements and is not included here. A dependency-license inventory used for release review identifies observed wheel metadata, not a complete lock or proof of reproducible installation.

## Direct dependency contract

`pyproject.toml` declares Python `>=3.11`, a `hatchling>=1.24` build backend, and these direct requirements:

| Scope | Requirements |
| --- | --- |
| Runtime | `httpx>=0.27`, `pydantic>=2.7`, `langgraph>=0.2` |
| Provider SDKs | `anthropic>=0.40`, `openai>=1.40` |
| Timezone data | `tzdata>=2024.1` |
| Development extra | `pytest>=8`, `pytest-cov>=5`, `ruff>=0.6`, `pre-commit>=3.7`, `httpx>=0.27`, `fastapi>=0.115` |
| Web extra | `fastapi>=0.115`, `uvicorn[standard]>=0.30` |

Lower bounds are declarations, not a tested compatibility matrix. The `akshare_a` adapter requires no AKShare installation. Dependencies retain their own licenses; see [third-party notices](../THIRD_PARTY_NOTICES.md).

`zoneinfo` needs IANA timezone data. `tzdata` supplies a fallback on systems without a database; normal resolution may still prefer system data. The dependency-contract regressions force `PYTHONTZPATH` empty in a subprocess and check CNINFO date conversion around Shanghai midnight. This does not validate every historical timezone transition or every allowed tzdata version.

## Commands and scope

After provisioning a suitable environment, run from the repository root:

```sh
FINRECEIPTS_OFFLINE=1 FINRECEIPTS_LLM_PROVIDER=fake \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -m "not live" -p no:cacheprovider -q
python -m ruff check . --no-cache
python -m ruff format --check . --no-cache
```

The offline variable requests application replay and `not live` filters marked tests. Neither denies process-wide network access. Rigorous offline validation also needs execution-level network restrictions, no API keys, and disabled external tracing/plugins. FakeLLM and synthetic HTTP transports test local behavior; they do not test real providers.

On 2026-10-08 UTC, a separate build check on **CPython 3.12.14, Linux x86-64** used Hatchling 1.24.0 to build the wheel and source archive. The wheel was installed into a clean isolated environment from approved local artifacts. From an empty working directory with `PYTHONPATH` absent, the imported package was verified to reside in `site-packages` and CLI `--help` passed. A copy of the full test suite without the source package then ran against the installed wheel under the offline socket guard: **498 passed**. The wheel contains the application and Apache license; it does not bundle test fixtures, datasets or historical results. The source archive retains only the three attributed SEC fixtures.

**Docker build/runtime, server deployment, real-model quality and a full Python-version matrix remain untested.** Docker `COPY` paths were inspected only. These package checks establish one environment, not byte-identical builds, every optional extra, or all versions allowed by the dependency lower bounds.

## Reproduction checklist for additional environments

The package check above covers one target. For additional targets and a distributable reproducibility bundle:

1. Record exact Python, OS/architecture, resolver/build tools and requested extras for each target.
2. Resolve and review all required runtime, test, web and build artifacts. Produce real pins and SHA-256 hashes plus artifact provenance; do not relabel an unrelated host freeze as a lock.
3. In an empty environment with network disabled, verify artifact hashes and install from only that local set, with index and package-cache fallback disabled. Include build-backend dependencies explicitly.
4. Build and install the project wheel; inspect its contents and entry points. Test that installed package rather than relying solely on an editable checkout or `PYTHONPATH`.
5. Run dependency consistency, full offline tests, lint/format, API/CLI smoke checks and timezone-fallback checks. Preserve commands, versions, exit statuses and final source/fixture hashes.
6. Repeat from empty environments and report each target separately. Docker execution, byte-identical builds, real-data historical availability and live-model performance each require separate evidence.

## Historical results

Earlier saved model experiments and baseline/continuation logs concern other source states. They are not bundled as current release evidence. The README preserves limited historical provenance, including evidence-fed 96/100 accuracy and total (not mean) 100-item cost estimates. New controls and synthetic fixtures do not retroactively revalidate those results.
