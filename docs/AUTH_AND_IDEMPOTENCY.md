# 本地身份、Token 认证与作用域幂等

默认 `FINRECEIPTS_AUTH_MODE=local`，身份固定为 `local`，保留本地演示。
启用 `token` 才会对所有 `/api/*` 请求检查 `Authorization: Bearer ...`。
`/health`、静态页面及捆绑示例仍可读取；它们不暴露用户任务。
Host 与 Origin 检查继续保留，其职责与身份认证不同。

## 配置

`FINRECEIPTS_AUTH_TOKENS_ENV` 指向另一个环境变量，该变量包含 JSON 对象：

```json
{"alice": "<token 的 SHA-256 小写十六进制摘要>", "bob": "<另一个摘要>"}
```

用户标识限 1–64 个字母、数字、点、下划线或连字符。`local` 为旧任务保留；
重复用户、重复摘要、明文 token、空映射和未知模式会使服务拒绝启动，不降级成匿名访问。
建议每个 token 使用至少 32 字节随机源。下例只在当前 PowerShell 内保留原始 token：

```powershell
$aliceToken = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$aliceDigest = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($aliceToken))).ToLowerInvariant()
$env:FINRECEIPTS_AUTH_MODE = 'token'
$env:FINRECEIPTS_AUTH_TOKENS_ENV = 'FIN_LOCAL_AUTH_DIGESTS'
$env:FIN_LOCAL_AUTH_DIGESTS = @{alice=$aliceDigest} | ConvertTo-Json -Compress
```

不要将原始 token 写进代码、命令参数、问题、报告或提交文件。客户端可以在内存中使用：

```powershell
$headers = @{Authorization="Bearer $aliceToken"}
Invoke-RestMethod http://127.0.0.1:8000/api/session -Headers $headers
```

`/api/session` 返回 `principal_id` 和 `auth_mode`。工作台与快速问答都提供令牌输入；
令牌只保存在当前文档内存，刷新或新开标签页需要重新输入。
私有 API 返回 `Cache-Control: no-store`，令牌不会被放进下载 URL。

## 权限与重试

任务列表、分页游标、详情、事件、导出、复核、推进、取消和恢复都按用户归属检查。
访问其他人的任务返回与不存在任务相同的 404；无效身份返回 401。
前端清空旧身份的结果、时间线和列表，不用另一身份重发待确认创建。

服务端唯一键为 `v2:SHA256(canonical([principal_id, "create_run", key]))`。
用户来自认证结果，操作来自服务端常量，原始键来自 `Idempotency-Key`。
不读取调用方自报的用户身份头。Token 轮换只要保留稳定用户标识，就保留幂等作用域。
同键同请求的响应继续使用原有 201 与任务内容；同键异请求为 409。

恢复记录仍使用原有标签页存储槽，以便识别旧记录。新版 `version: 2` 额外保存
`principal_id`、`operation`；目标模式还保存 target、工具开关和 cutoff。
旧 `version: 1` 记录只在 local 模式读取。无法确认身份、存储不可用或记录损坏时不自动换键重发。

## 显式迁移旧库

新库增加 `runs.owner_id` 和 `runs.operation`；原有唯一键列保存有作用域的摘要。
遇到旧 schema 时，RunStore 在执行 WAL 设置或修改表之前拒绝打开，并提示使用迁移命令。
迁移必须输出到不存在的新目录，不会原地更新现有运行库：

```powershell
uv run finreceipts runtime-migrate --database PATH_TO_OLD_DATABASE --out runs/migrated-copy-001
```

命令先建立一致的 SQLite 备份并校验，再仅在副本中增加字段和转换键。
旧任务归属 `local`。`runs.document`、`events.document`、run ID、revision、事件哈希和锚点均保留。
目录同时保留 `source-manifest.json` 与新 `manifest.json`，并支持 v1/v2 备份格式检查与恢复。

完成验证后，可以显式将 `FINRECEIPTS_RUN_DB` 指向新目录的 `runs.sqlite3`。
迁移仅改变存储格式与归属，不会绕过实现标识检查，也不会自动恢复或重做模型调用。
更早实现创建的任务仍可读取和导出，能否推进由原有兼容性约束决定。

本次开发只迁移了隔离夹具；没有迁移用户现有运行库。
这是一套最小 API 身份与任务归属控制，没有加入用户注册、角色管理、分布式配额或生产租户运维系统。

## 验证入口

- `tests/test_auth_runs.py`：身份、隔离、游标、重复键和目标 HTTP 契约。
- `tests/test_scoped_migration.py`：并发创建、旧库副本迁移、原始字节及导出不变。
- `tests/test_create_journal.cjs`：标签页存储、旧记录、新版记录与身份错配。
- `scripts/browser_agent_backend.cjs`：真实 Edge、FakeLLM、独立数据库；响应丢失后重启服务与刷新页面，
  独立查询 SQLite，验证用户切换、原始导出和服务停止。输出目录必须不存在。

这些是工程验收，不是用户规模、业务收益或真实模型准确率。
