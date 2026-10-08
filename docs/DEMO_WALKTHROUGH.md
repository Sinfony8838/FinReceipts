# Three-minute local demo / 三分钟本地演示

[English README](../README.md) · [中文说明](../README.zh-CN.md)

This demo uses the fake responder and bundled SEC replay fixtures. It requires no model key and makes no live model call. Installing dependencies may require network access. Run it from a source checkout after the README installation steps; standalone wheels do not include the fixtures. No investment advice.

本演示使用固定回答和随附 SEC 缓存，不需要模型密钥，不调用真实模型。安装依赖可能联网。请先按 README 安装，再从源码根目录执行；单独的 wheel 不包含测试缓存。

## 1. Start locally / 本地启动

```sh
FINRECEIPTS_HOST=127.0.0.1 FINRECEIPTS_LLM_PROVIDER=fake \
  FINRECEIPTS_OFFLINE=1 FINRECEIPTS_CACHE_DIR=tests/fixtures/sec finreceipts-api
```

Open http://127.0.0.1:8000. Check http://127.0.0.1:8000/health: `provider` should be `fake` and `offline` should be `true`. Keep the server bound to loopback. Stop with Ctrl+C.

打开页面后，先确认 health 接口的 `provider` 为 `fake`、`offline` 为 `true`。仅在本机回环地址运行，结束时按 Ctrl+C。

## 2. Show a correction / 展示一次修订

1. Click **AAPL FY2023**. This fills the Apple revenue question, ticker `AAPL`, fiscal year `2023`, and clears `as_of`.
2. Choose **baseline**, then **Run once**. The canned answer says `$390.0 billion`. Both occurrences of the figure are unsupported: `numbers=2`, `supported=0`, `unsupported=2`. Baseline still audits; it does not revise.
3. Change the mode to **verify**, feedback to **receipts**, then click **Run with SSE**. The expected nodes are `answer → verify → revise → answer → verify`.
4. The final canned answer says `$383.3 billion`; both occurrences are supported, with one revision. Hover or keyboard-focus a green number to inspect the concept, raw value, period and filing link.
5. Explain that `$383.3 billion` matches the recorded `$383,285,000,000` within tolerance. A link identifies the original filing for human review; it does not certify every sentence.

中文讲解顺序：先点 **AAPL FY2023**，用 **baseline / Run once** 展示两处无依据数字；再切到 **verify / receipts / Run with SSE**，展示一次修订后两处匹配。悬停或用键盘聚焦绿色数字，查看原始值、指标、期间与申报链接。强调“两处”是同一数字在正文和 `ANSWER` 行各出现一次，不是两个独立事实。

### API equivalent / 等价 API 请求

With the local server running, use another terminal:

```sh
curl --fail --silent --show-error http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"What was Apple\u0027s revenue for fiscal 2023?","ticker":"AAPL","fiscal_years":[2023],"mode":"verify","feedback":"receipts"}'
```

Expected fields: `revisions: 1`, `escalated: false`, and `audit_summary` with `numbers: 2`, `supported: 2`, `contradicted: 0`, `unsupported: 0`. The JSON also contains the answer, per-attempt audits, evidence and rendered HTML. These expectations describe the canned demo, not a live-provider benchmark.

## 3. Show the verifier without a model / 单独核验一句话

```sh
FINRECEIPTS_OFFLINE=1 FINRECEIPTS_CACHE_DIR=tests/fixtures/sec \
  finreceipts audit --ticker AAPL --years 2023 \
  --text 'Revenue was $383.3 billion.' --html /tmp/finreceipts-aapl.html
```

Expected: one supported number and exit status 0. Open the generated local HTML file to inspect its receipt. Changing the text to `$390.0 billion` produces an unsupported result and exit status 1; that is the audit result, not an installation failure. The audit command does not call an LLM.

预期：一处数字匹配，退出码为 0；可打开生成的 HTML 查看收据。把数字改为 `$390.0 billion` 后，预期为 unsupported、退出码 1，表示未找到匹配依据。此命令不调用模型。

## What to say / 讲解词

**English, about 45 seconds:**

“FinReceipts explores how financial answers can carry inspectable numeric evidence. Here the fake model starts with a deliberately wrong Apple revenue figure. The verifier checks extracted numbers against cached SEC XBRL facts, then feeds the audit into a bounded revision loop. The final number has a receipt showing the selected metric, period, raw value and filing link. This local demo proves the workflow is wired together. The answers are scripted, and a green receipt only means a match under these rules. It does not establish the truth of all prose, historical availability, or investment suitability.”

**中文，约 45 秒：**

“FinReceipts 研究的是，怎样让金融回答里的数字附上可以检查的依据。这里先用固定回答故意写错 Apple 的营收，再用确定性核验器查询缓存中的 SEC XBRL 事实，把核验结果送回有次数上限的修订流程。修订后的数字附有指标、期间、原始值和申报文件链接。这段本地演示说明流程已经接通，但回答是预先写好的。绿色只表示按当前规则匹配到事实，不保证整段话都对，也不证明某个历史时点已知，更不构成投资建议。”

## Troubleshooting and boundaries / 排错与边界

- **Cache miss:** run from the repository root and confirm `tests/fixtures/sec/` exists. The included companyfacts cover AAPL and NVDA only. Do not expect every company in the US dataset to replay offline.
- **SOCKS proxy import error:** the HTTP client can read proxy settings even in replay mode. If a configured SOCKS proxy requires `socksio`, use an environment with its approved proxy dependencies (the optional `httpx[socks]` extra supplies SOCKS support). Keep required organizational proxy and network controls in place.
- **Cascade error:** the UI does not supply a large-model identifier. Cascade needs `FINRECEIPTS_LARGE_MODEL` or an API `escalate_model` value. This walkthrough deliberately uses verify mode.
- **Persistent red numbers:** NVIDIA's canned demo deliberately retains an unsupported `80%` margin. Revision limits can be exhausted; completion is not a guarantee that all extracted numbers are supported.
- **Date controls:** the UI's `as_of` is a legacy date filter. It is not the explicit trusted-catalog historical-availability workflow described in [PIT controls](pit-redteam.md).
- **No receipt:** unsupported means no supporting match was found within the available evidence and rules, not proof that the number is false. Some claims are outside extraction and metric coverage entirely.

For validation scope and installation caveats, read [reproducibility](reproducibility.md). The demo's API and CLI outputs were checked against the bundled fixtures; no real-model benchmark is implied.

## Optional provenance demonstration / 可选来源追溯演示

The [SEC snapshot walkthrough](sec-snapshots.md) adds an independent no-model exercise: capture the bundled JSON as a new local observation, validate its byte hashes and bound catalog, then explain why a historical cutoff must refuse it. Live capture is a separate opt-in command requiring an authorized SEC contact header. Neither path backdates current data to its filing date.

[SEC 快照流程](sec-snapshots.md)可展示字节哈希、采集时点及 catalog 绑定；本地导入只标记为本次观测，不能包装成历史可得性证明。
