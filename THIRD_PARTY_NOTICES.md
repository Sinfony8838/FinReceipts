# Third-party notices and provenance

## Scope

FinReceipts project code is licensed under [Apache-2.0](LICENSE). That license does not replace licenses of separately installed dependencies or terms governing upstream data. This source-first release does not bundle the reviewed dependency wheels, a wheelhouse or a container image. No project-specific rights-holder identity is asserted by this notice beyond existing project metadata.

## SEC data attribution

The retained fixtures originate from the U.S. Securities and Exchange Commission (SEC):

- `tests/fixtures/sec/www_sec_gov_files_company_tickers_json__ace4a0fb8e9e.json.gz`: https://www.sec.gov/files/company_tickers.json
- `tests/fixtures/sec/data_sec_gov_api_xbrl_companyfacts_CIK0000320193_json__587c133c2376.json.gz`: https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json
- `tests/fixtures/sec/data_sec_gov_api_xbrl_companyfacts_CIK0001045810_json__b986f6790669.json.gz`: https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json

These mappings come from fixture filenames and the SEC client URL templates. Exact retrieval dates and independent first-observation provenance were not supplied; no dates are invented here. SEC-derived evaluation questions retain source/evidence references where recorded.

The [SEC webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions) describes SEC government-created content and EDGAR public filings as free to access and reuse. This attribution is not SEC endorsement. Source material is not relicensed by this project’s Apache license. Follow SEC access guidance when retrieving new data. See [data sources](docs/data_sources.md) for coverage and historical-availability limitations.

Original Eastmoney and cninfo snapshots and the generated real-data A-share dataset are excluded because redistribution permission was not evidenced. Their synthetic replacements are invented test inputs. The HTTP adapter does not bundle or depend on AKShare; references to its endpoint documentation confer no rights to third-party datasets.

## Observed dependency license metadata

The table below transcribes license expressions/fields from 52 wheel artifacts reviewed during release preparation. It includes direct, transitive and development artifacts observed at that time, and may include packages not needed by a particular installation. It is **not** a lock file, a complete final SBOM, a list of bundled code, or a guarantee that future resolutions use the same versions/licenses. An absent expression would require inspecting the distribution’s own license files.

| Distribution | Observed version | License metadata |
| --- | --- | --- |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anthropic | 1.12.1 | MIT |
| anyio | 4.15.1 | MIT |
| certifi | 2026.7.22 | MPL-2.0 |
| charset-normalizer | 3.5.2 | MIT |
| distro | 1.9.0 | Apache License, Version 2.0 |
| docstring_parser | 0.18.0 | MIT |
| fastapi | 0.142.4 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.13.1 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| httpx2 | 2.13.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| jiter | 0.17.0 | MIT |
| jsonpatch | 1.33 | Modified BSD License |
| jsonpointer | 3.2.0 | Modified BSD License |
| langchain-core | 1.6.7 | MIT |
| langchain-protocol | 0.0.19 | MIT |
| langgraph | 1.2.14 | MIT |
| langgraph-checkpoint | 4.2.0 | MIT |
| langgraph-prebuilt | 1.1.0 | MIT |
| langgraph-sdk | 0.4.6 | MIT |
| langsmith | 0.14.4 | MIT |
| openai | 3.26.1 | Apache-2.0 |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| orjson | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| ormsgpack | 1.12.2 | Apache-2.0 OR MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.6.0 | MIT |
| pydantic | 2.13.5 | MIT |
| pydantic_core | 2.46.5 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pytest | 9.1.1 | MIT |
| PyYAML | 6.0.3 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| requests-toolbelt | 1.0.0 | Apache 2.0 |
| ruff | 0.16.10 | MIT |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| starlette | 1.7.0 | BSD-3-Clause |
| tenacity | 9.2.1 | Apache-2.0 |
| truststore | 0.10.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| tzdata | 2026.5 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uuid_utils | 0.17.1 | BSD-3-Clause |
| websockets | 16.1.1 | BSD-3-Clause |
| xxhash | 4.0.1 | BSD-2-Clause |
| zstandard | 0.25.0 | BSD-3-Clause |

In particular, `certifi` carries MPL-2.0 metadata, and `orjson` declares `MPL-2.0 AND (Apache-2.0 OR MIT)`. The dependency tree must not be described as entirely MIT/Apache. Preserve each distributed component’s actual license and required notices, including any third-party components documented inside its license files.

If separately distributing dependency wheels, vendored code, source bundles containing dependencies, or a container image, inventory the exact distributed artifacts and preserve their required licenses/notices. Review applicable MPL source-availability obligations for covered files and modifications. This summary does not substitute for those component-specific requirements or provide a blanket redistribution grant.
