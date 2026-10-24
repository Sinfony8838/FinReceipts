# FinReceipts 2.0 本机运行

运行环境：Docker Desktop 的 Linux/WSL 2 引擎。先执行 `docker info` 确认 Server 可用，并给 Docker 磁盘预留足够空间。项目独立使用 `finreceipts-v2` Compose 名称和数据卷，旧 CLI、SQLite 与历史评测保持原样。

```powershell
cd D:\FinReceipts
# 仅首次生成；已有文件会拒绝覆盖。
python scripts/v2_bootstrap.py
docker compose --env-file .env.v2 -f compose.v2.yml up -d --build
docker compose --env-file .env.v2 -f compose.v2.yml ps
```

打开 http://localhost:8090 。本机演示组织为 `alpha` 和 `beta`；各有 `.admin`、`.analyst`、`.reviewer` 账号。初始密码只从本机 `.env.v2` 的 `BOOTSTRAP_PASSWORD` 读取，不写入源码或报告。生产部署应另行设置 TLS、安全 Cookie、组织开户和独立密码；本机演示不代表生产可用性承诺。

默认 `MODEL_PROVIDER=fake`、`FINRECEIPTS_OFFLINE=1`，只读取随镜像保留的 AAPL/NVDA SEC 样本。直接指标、比率和同比使用规则路径；研究表述经过模型步骤与数字核验。任务完成后，由另一个复核账号接受或驳回，批次交付仅包含已接受版本。

真实业务模型可设置 `MODEL_PROVIDER=minimax`、`MODEL_ID=MiniMax-M3.1-Flash` 和 `MINIMAX_API_KEY`；DeepSeek 对应 `deepseek` / `deepseek-flash` / `DEEPSEEK_API_KEY`。改完配置需要重新 `up -d`；已经发布的任务配置不会改变。禁止在压力测试环境启用真实模型。默认输出上限 4096，MiniMax effort 为 medium，完整原生内容块随检查点保存。

需要访问其他 SEC 公司时，设置 `FINRECEIPTS_OFFLINE=0`、`FINRECEIPTS_CACHE_DIR=/data/sec` 和带真实联系方式的 `FINRECEIPTS_SEC_USER_AGENT`。跨 Worker 的抓取限速和同来源互斥通过 Java/Redis 协调，缓存位于独立卷。A 股仍使用原有 CLI 适配和验证边界，不属于新版 SEC 正式验收范围。

总计 20 元授权拆分为评测 10 元、业务 10 元。业务以 MySQL 台账为准；评测使用 `runs/platform-v2-live/budget.jsonl` 和独占锁。结果不明保留预留；不要删除锁或台账来重跑。旧实验结果存在时脚本不会覆盖或自动再次收费。保守估算值不等于供应商账单。

```powershell
docker compose --env-file .env.v2 -f compose.v2.yml --profile observability up -d
# Prometheus: http://localhost:9091
# Grafana: http://localhost:3001，用户名 admin，密码来自 BOOTSTRAP_PASSWORD
python scripts/v2_acceptance.py
python scripts/v2_model_report.py
```

监控面板含排队、执行、未知任务、预算占用、派发失败、租约恢复与指标更新时间。OpenTelemetry Collector 接收跨任务传播的 traceparent，完整审计仍在 MySQL 事件表。证据与交付包按 SHA-256 异步复制到 artifacts 卷；复制失败会重试并暴露指标，不改写 SQL 中的权威结果。

批处理接入：在“团队与接入”生成 `submit` API Token；令牌接口还支持 `read` 作用域。提交令牌可提交和管理本人任务，不能执行人工复核或创建成员；这两类操作需要浏览器会话。`POST /api/v2/batches` 必须携带 `Idempotency-Key`。登录后可以读取 `/v3/api-docs` 和访问 `/swagger-ui/index.html`；内部 Worker 接口不经公共代理暴露。

旧记录先导出为原格式 JSON，再设置 `FINRECEIPTS_V2_TOKEN` 并运行 `python scripts/v2_import_archive.py <export.json>`。脚本先用旧校验器验证事件链，服务器按原始字节只读保存；不会把归档恢复成新版可执行任务。

停止服务使用 `docker compose --env-file .env.v2 -f compose.v2.yml stop`。恢复使用 `up -d`。不要使用 `down -v` 或 Docker 的 factory reset 来排查普通启动问题，以免删除数据库与预算台账。
