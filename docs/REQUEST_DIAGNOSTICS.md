# 请求诊断与错误信息保护

FinReceipts 为每次 HTTP 请求生成独立编号，将界面错误、HTTP 响应和服务日志关联起来。
供应商异常使用固定错误码与提示，日志只记录排障所需的元数据。
普通问答失败、流式问答中途失败和持久化任务冲突都经过实际接口验证。

## 修改的原因

此前 `/api/ask` 和 `/api/ask/stream` 直接返回 `str(exc)[:500]`。
截断不会脱敏：注入包含私密提示、模拟密钥和供应商 URL 的异常后，两个测试都确认原文出现在响应中。
修复后，响应只提供稳定的错误码、说明和请求编号，不再暴露异常文本。

错误分类测试还复现了两条误报：供应商解析的 KeyError 被报成 404，ValueError 被报成 422。
现在只有 RunNotFound 与 EvidenceUnavailable 两种明确的业务异常分别对应这些状态，
供应商解析故障返回安全的 503 `run_failed`，不会错误要求用户修改合法输入。

另一个排障问题来自流式响应：HTTP 200 表示响应头已经发送，不代表工作流成功。
现在流式错误会设置 `outcome=error` 与 `error_code`；耗时记录覆盖流的生命周期，
中间块直接发送，不为日志读取或缓存请求体、响应体。

## 接口约定

所有 HTTP 响应携带服务端生成的 `X-Request-ID`，格式为 32 位十六进制字符串。
调用方传入的同名请求头不被信任，也不作为本服务的关联编号。
它只是排障标识，不是权限凭据、任务编号或跨服务追踪上下文。

HTTP 错误响应保留 `detail`，新增 `code` 与 `request_id`。例如：

```json
{
  "detail": "answer generation failed; no raw provider error is exposed",
  "code": "ask_failed",
  "request_id": "0123456789abcdef0123456789abcdef"
}
```

SSE 的 `error` 事件携带同样字段，另外包含 `event: "error"`。
浏览器将请求编号显示在错误提示下面，固定错误码对应中文处理提示。
参数验证错误返回 422，不回显 Pydantic 的输入值或上下文；GET 流式请求的非法财年也按 422 处理。

| 错误码 | 含义与处理 |
|---|---|
| `invalid_request` | 检查字段类型、取值范围与核验设置 |
| `evidence_unavailable` | 请求范围没有可用事实；调整公司/财年或检查已录制的数据 |
| `cascade_unconfigured` | 未配置升级模型；改用其他模式或配置模型后再试 |
| `ask_failed` / `audit_failed` | 生成或证据读取失败；凭请求编号定位路由与时间，在受控环境复现 |
| `run_conflict` | 版本或任务状态冲突；先查询当前状态，配置变化时导出原任务并重新创建 |
| `run_not_found` | 任务不存在；检查任务编号 |
| `integrity_failed` | 数据完整性校验失败；保留数据库副本，停止推进并排查 |
| `storage_busy` | 带 `Retry-After: 1`；先等待并刷新状态，不能盲目重放整个模型节点 |
| `run_failed` / `internal_error` | 未分类的任务或应用异常；不自动判断为可重试 |
| `stream_failed` | 响应开始后的未处理异常；日志标记失败，不发送第二组 HTTP 响应头 |
| `http_error` | 其他 HTTP 错误，例如路由未找到 |

未处理的应用异常在诊断中间件处转换为安全响应，不向 ASGI 服务器抛出包含原文的异常堆栈。
已经开始的流不能修改 HTTP 状态；已知问答错误发送 SSE error，未知流错误结束响应并在日志标记失败。
客户端断开与取消单独记录为 `disconnected`，不会伪装成工作流成功。

## 日志字段与启动

使用 `finreceipts-api` 或 `python -m finreceipts.app` 启动时，
`finreceipts.requests` logger 向 stderr 输出每请求一行 JSON。
日志配置传给 Uvicorn，供子进程初始化使用；不修改其他应用 logger 的处理方式。

| 字段 | 内容 |
|---|---|
| `event` / `time` | 固定事件类型 `http_request`、UTC 时间 |
| `request_id` | 服务端生成的关联编号 |
| `method` / `route` | 受限 HTTP 方法和路由模板，例如 `/api/runs/{run_id}`；未解析路由为 `unmatched` |
| `status` | 实际 HTTP 状态，流失败时可能仍为 200 |
| `outcome` / `error_code` | `ok`、`error`、`disconnected`，以及已知错误分类 |
| `duration_ms` | 从中间件进入到响应结束或失败的耗时，非模型 token 首字延迟 |

不记录原始路径、查询字符串、请求/响应正文、鉴权头、外部传入的请求编号或异常文本。
CLI 同时关闭 Uvicorn 原始访问日志，避免 GET 问答中的问题进入 URL 日志。
若自行用 `uvicorn finreceipts.app.main:app` 启动，需配置 `finreceipts.requests` logger，
并使用 `--no-access-log`；自建反向代理及第三方 SDK 的日志需要分别检查。

## 排障步骤

1. 从界面错误提示或响应头复制请求编号。
2. 在服务日志中搜索这个编号，核对 `route`、`error_code`、`time` 和 `duration_ms`。
3. 对 `storage_busy` 或 `run_conflict`，先查询持久化任务状态，按[运行契约](RUNTIME.md)恢复。
4. 对供应商或未分类异常，保留当前任务证据，用离线替身或可控测试重现。
   不为获得更多细节而在共享日志里打印完整异常、模型消息或供应商响应。
5. 对 SSE，使用 `outcome` 与 error/done 事件判断请求结果，不能只统计 HTTP 200。

本轮没有引入完整 APM、指标后端、日志轮转或集中采集；日志接收端应自行制定访问与保留策略。
请求 `outcome=ok` 只表示 HTTP 处理完成；持久化任务是否 interrupted、是否通过核验，仍看任务状态与事件。
业务回答和用户主动导出的证据包仍可能包含原始输入；日志字段白名单不是通用内容脱敏或认证系统。

## 验证入口

2026-10-18，以 `25579b6` 为本轮基线。新增 **19 个展开后的测试用例**；
Windows CPython 3.12.11 最终离线回归为 **691 passed / 1 skipped，37.20 秒**。
跳过的是既有 Windows symlink 权限用例，不计作通过。
Ruff 静态检查与格式检查通过，126 个 Python 文件符合格式；JavaScript 语法与 Git 差异格式检查通过。

真实 Edge 验收通过：三个错误请求编号都对应唯一的服务日志，HTTP 状态、路由模板、
错误码与 `outcome=error` 一致；手机错误提示无横向溢出，JavaScript pageerror 为零。
另一次既有工作台验收覆盖创建、推进、刷新、核验修订、导出、移动端与取消，全部通过。

本地证据位于 `runs/request-diagnostics-20261018/`，最终 JUnit 为 `full-final.xml`，
错误界面及关联记录位于 `browser-final/`，工作台流程位于 `browser-v2/runtime-ui/`。
`evidence.json` 保存最终源码及结果文件的 SHA-256；忽略目录不随仓库分发，外部可通过脚本重验。
本轮未推送远端、未运行新远端 CI，也未调用真实模型。

[测试](../tests/test_request_diagnostics.py) 覆盖响应脱敏、16 个并发请求编号隔离、
路径与查询不入日志、参数值不回显、SSE 异常/断连/取消、流不缓冲及真实服务进程日志。
真实服务测试通过临时数据库与回环端口启动 CLI，发送带模拟敏感问题的 GET 流式请求，
确认服务日志只出现路由模板和对应请求编号。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_request_diagnostics.py tests/test_api.py tests/test_run_api.py -q
.venv\Scripts\python.exe -m pytest -m 'not live' -q
```

[浏览器验收脚本](../scripts/browser_diagnostics_smoke.cjs) 使用实际页面与 API，
检查一次性错误、SSE 错误、外部取消后的旧版本推进，以及刷新恢复界面。
需先启动 fake provider、离线 fixtures、未配置 large model 的本机服务，并提供 Playwright。

```powershell
$env:FINRECEIPTS_TEST_URL='http://127.0.0.1:8000'
node scripts/browser_diagnostics_smoke.cjs runs/my-new-diagnostics-ui
```

脚本拒绝覆盖已有输出目录。新增请求编号和错误字段不改变持久化状态机、预算计数或外部调用恢复语义。

实现依据：[Starlette ASGI 中间件](https://www.starlette.io/middleware/)、
[Uvicorn 日志设置](https://uvicorn.dev/settings/)。
