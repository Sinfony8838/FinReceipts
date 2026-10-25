# V2 接口契约

默认入口 `http://127.0.0.1:8090`。登录后的 OpenAPI 文档位于 `/swagger-ui/index.html`，JSON 位于 `/v3/api-docs`。浏览器和批处理使用同一业务接口，内部协议经独立 coordinator 仅在 Compose 网络暴露。

## 认证与错误

浏览器先 GET `/api/v2/csrf`，按照返回的 `header` 名称携带 `token`，然后 POST form-urlencoded `/api/v2/login`（username/password）。登录后再次获取 CSRF；Spring Session Redis 的 `FRSESSION` 供两个 API 副本共享。退出使用 POST `/api/v2/logout`。本机 HTTP 演示使用非 Secure Cookie；HTTPS 部署需设 `SECURE_COOKIE=true` 并配置可信反向代理。

登录用户可 POST `/api/v2/tokens`，正文 `{"scope":"submit","days":1}`；Token 仅返回一次，数据库只存 SHA-256。`read` 只能查询，`submit` 可提交/取消/重试。复核和成员管理要求交互会话，Token 不授予复核权。所有业务租户从认证成员记录获取，不接受请求正文的 tenant_id。

业务错误使用 JSON `code`：422 参数错误；401 未认证；403 角色/作用域/CSRF 不符；404 对象在当前租户不可见；409 正文、版本、租约或未知调用确认冲突；429 速率/配额超限；503 资源不可用。数据库或安全过滤链故障可能返回 500，应记录为服务异常，不能按成功接纳处理。对超时提交可用原幂等键及相同正文查询式重试，不能随意生成新键。

## 业务 API

| 方法与路径（前缀 `/api/v2`） | 正文/参数与语义 |
|---|---|
| POST `/batches` | `Idempotency-Key` 必填，1–100 items，202 返回 batch_id；同用户/租户/键同正文返回原对象，异正文 409 |
| GET `/batches` | before、limit（1–100）；items 与 next_cursor |
| GET `/tasks` | batch_id、before、limit、review；只读摘要；review=true 过滤待复核已完成任务 |
| GET `/tasks/{id}` | 证据、结果、尝试用量与复核记录 |
| POST `/tasks/{id}/cancel` | expected_revision；取消不承诺撤销供应商收费 |
| POST `/tasks/{id}/retry` | expected_revision；UNKNOWN 还需 acknowledge_possible_duplicate=true |
| GET `/tasks/{id}/events` | SSE，Last-Event-ID 或 after；事件 ID 为该任务 revision；只读、可补读 |
| POST `/tasks/{id}/review` | expected_revision、decision=ACCEPTED/REJECTED、reason；不能审核自己提交的任务 |
| GET `/batches/{id}/export` | 只返回已接受结果版本；未接受结果不会隐含交付 |
| GET `/quota` | 租户预算、占用和任务状态统计 |
| POST `/members` | 管理员添加成员：username、password（至少 12 字符）、role |
| POST `/tokens` | 交互会话签发 read/submit Token，1–90 天 |
| POST `/archives` | 旧导出原文字节，只读归档；使用配套导入脚本先验证事件链 |

提交示例（Token 从本机环境变量读取，不写入命令历史）：

```python
import os, uuid, httpx

response = httpx.post(
    "http://127.0.0.1:8090/api/v2/batches",
    headers={
        "Authorization": "Bearer " + os.environ["FINRECEIPTS_V2_TOKEN"],
        "Idempotency-Key": str(uuid.uuid4()),
    },
    json={
        "title": "AAPL 年报核验",
        "items": [
            {
                "kind": "metric",
                "ticker": "AAPL",
                "target": {"kind": "direct", "metric": "revenue", "fiscal_year": 2023},
            },
            {
                "kind": "research",
                "ticker": "AAPL",
                "question": "简述净利率，并引用证据。",
                "target": {
                    "kind": "ratio",
                    "metric": "net income",
                    "denominator": "revenue",
                    "fiscal_year": 2023,
                },
            },
        ],
    },
)
response.raise_for_status()
print(response.json())
```

目标 kind 支持 direct、ratio、yoy_growth；任务 kind 支持 metric、research。后者才可能调用模型。当前正式验收为 SEC 年度证据，旧 A 股模式沿用原边界。

## Worker `/internal/v1`，协议版本 1

MQ 消息为 `{"protocol_version":1,"task_id":"...","revision":2,"traceparent":"..."}`，不是完整证据。每次领取从 Java 读取权威快照。所有内部请求带独立 `Authorization: Bearer <WORKER_SECRET>`，公共 Nginx 拒绝 `/internal/`。

| POST 路径 | 必需字段 |
|---|---|
| `/tasks/{id}/claim` | expected_revision、worker_id；返回 revision、lease_token、step、request、profile、checkpoint、evidence |
| `/tasks/{id}/heartbeat` | lease_token |
| `/tasks/{id}/reserve` | lease_token、attempt_id、reserve_micros；必须在每次真正发送 HTTP 前成功 |
| `/tasks/{id}/settle` | lease_token、attempt_id、actual_micros、usage；迟到计费可记录但不能改变任务状态 |
| `/tasks/{id}/commit` | lease_token、expected_revision、next_step、checkpoint、evidence、result、machine_verdict |
| `/tasks/{id}/fail` | lease_token、code、unknown |
| `/source/permit` | 空对象；全 Worker 的 SEC 速率窗口 |
| `/source/{sha256}/lock`、`unlock` | lease_token；只允许持有者释放 |

人工结论不写入 Worker commit；Worker 无权指定租户、预算上限或最终交付版本。切换模型会创建新的内容哈希配置，不修改已发布配置。历史归档不进入 claim。
