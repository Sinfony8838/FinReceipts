# Data sources and fixture provenance

Public accessibility and permission to redistribute are different questions. The project's code license does not relicense upstream disclosures or grant rights to third-party data.

## Included SEC fixtures

The three gzip JSON fixtures in `tests/fixtures/sec/` correspond to:

| Fixture | Source |
| --- | --- |
| `www_sec_gov_files_company_tickers_json__ace4a0fb8e9e.json.gz` | https://www.sec.gov/files/company_tickers.json |
| `data_sec_gov_api_xbrl_companyfacts_CIK0000320193_json__587c133c2376.json.gz` | https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json |
| `data_sec_gov_api_xbrl_companyfacts_CIK0001045810_json__b986f6790669.json.gz` | https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json |

Source URL mappings follow the fixture names and `finreceipts.tools.edgar` URL templates. Exact retrieval dates and independent first-observation receipts are not recorded in the supplied fixture provenance; none are inferred from filesystem dates, fiscal periods or filing dates. These fixtures support functional replay, not strict historical-availability proof.

The [SEC webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions) describes government-created website content and EDGAR public filing content as free to access and reuse. Attribution here identifies the source; the SEC does not endorse this project. Consult the original filing before relying on a number.

`evals/datasets/xbrl_numeric_v0.jsonl` contains SEC-derived questions and evidence references. The three fixtures do not provide full companyfacts coverage for that dataset. Its date-only `as_of` fields do not establish exact public availability. See [evaluation controls](eval-controls.md).

Live access should follow [SEC automated-access guidance](https://www.sec.gov/os/accessing-edgar-data), including a descriptive User-Agent and contact information. The client defaults to eight requests per second; local configuration does not replace checking upstream access requirements.

## A-share adapters and synthetic replacements

The code includes `AShareClient` for Eastmoney financial endpoints and `CninfoClient` for announcement metadata. The former uses HTTP directly; it does not import or bundle the AKShare package. AKShare endpoint documentation is not a license to redistribute underlying provider data.

The source-first release excludes 92 original Eastmoney snapshots, six original cninfo snapshots and the generated real-data A-share evaluation file because their redistribution permission was not evidenced. Synthetic test inputs replace their offline test role. Synthetic company names, figures and announcement payloads are invented test data, not financial statements or investment evidence. The synthetic HTML receipt is similarly a rendering example, not a real issuer report.

Live recording and A-share dataset-generation commands may request upstream data. They are not necessary for the synthetic tests; check the provider's access and reuse terms before running them or sharing their outputs. Never assume that small cached responses or generated questions are automatically exempt from those terms. The official cninfo API is at https://webapi.cninfo.com.cn/; this project's AJAX adapter is not that authenticated service.

## Evidence limits

Legacy US/CN lookup filters use dates such as `filed` and `NOTICE_DATE`. Explicit SEC catalog/cutoff evaluation is separate and needs trusted timestamp and byte provenance; it rejects CN items. No current snapshot should be backdated into a strict replay catalog. Neither the synthetic replacements nor the retained SEC fixtures establish a real-data PIT benchmark or live provider reliability.

See [third-party notices](../THIRD_PARTY_NOTICES.md) for dependency and data-license separation.
