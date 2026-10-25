# 给 zcode 的任务交接

## 目标与当前边界

继续完成 FinReceipts 2.0 的剩余验收，定位是“具备 AI Agent 工程能力的 Java 后端开发”。主链路已经落地：批次提交 → 冻结证据 → 异步执行 → 确定性核验/有限修订 → 人工复核 → 审核版本导出。不要另起产品、改成通用 RAG 或只做 UI 原型。

仓库：`https://github.com/Sinfony8838/FinReceipts`，本机 `D:\FinReceipts`。本次阶段提交合并后以远端 main 为基线；从本地接手时先看 `git status`，仍有另一个会话的 UI 改动，**不要 reset、clean、stash 覆盖，也不要直接 pull 覆盖工作区**。

阅读顺序：[V2_ACCEPTANCE.md](V2_ACCEPTANCE.md) → [V2_ARCHITECTURE.md](V2_ARCHITECTURE.md) → [V2_START.md](V2_START.md) → [V2_OPERATIONS.md](V2_OPERATIONS.md)。实现进度和真实模型边界分别在 V2_IMPLEMENTATION / V2_EVALUATION；Java 与 AI 后端简历文本在 V2_RESUME。

## 优先处理的剩余任务

1. **复核运行环境。** Docker Desktop 4.79.0 / Engine 29.5.3 / WSL 3.0.3。已迁盘，数据在 `D:\FinReceipts\.cache\docker-data\DockerDesktopWSL`，虽位于 `.cache`，实际包含数据库、镜像和卷，禁止清理。AF_UNIX 残留导致 Desktop 重启失败曾复发；Docker 完全退出后，可运行 `scripts/start_docker_windows.ps1 -LocalData C:\Users\zcyxn\AppData\Local -RepairRuntime`，只重命名保留两个运行目录，不删卷。`docker info` 成功后再恢复 Compose。正常启动不需要 RepairRuntime。
2. **完成容量复测。** 库内已有 100,000 条合成摘要，先 COUNT 核对，别再次盲目播种。首轮 httpx 200 RPS/10 分钟未达标（P95 656 ms，6,674 次 429，客户端最大积压 27.467 秒）；Node 10 秒诊断 P95 23.18 ms、2,000 全成功。Node 完整十分钟复测中断，无结果，不能算通过。先给压测端增加周期落盘/中断状态，再用 `v2_load.py query --rps 200 --duration 600` 重测；近期 Node 客户端扩展了 burst 模式，先做小规模健全性检查。
3. **分别运行其余场景。** `v2_load.py sse --connections 1000 --duration 65`；`v2_load.py burst`（200 并发，每批 5 项）；四 Worker 设置 `FAKE_DELAY_SECONDS=0.2` 后 `v2_load.py queue --rps 5 --duration 600`；提高模型桩延迟和提交速率验证过载及恢复。它们目前均 UNRUN。不要同时运行不同负载以免混淆归因。`v2_drain.py <本轮报告绝对路径>` 会只观察本轮任务到终态并统计实际吞吐/等待时间，不取消任务。
4. **检查容量瓶颈再优化。** 分开统计成功、429、非预期错误、客户端计划/实际发起延迟、API 接纳和完整任务吞吐。若并发提交慢，检查租户行锁、单事务 SQL 次数和 MySQL 等待；若 SSE 慢，检查每连接轮询 SQL、连接配额和超时；不得通过删除认证、持久化、限流或预算来“达标”。保留所有失败轮次与配置/源码/镜像 ID。
5. **接入新 UI。** 另一个会话“优化 FinReceipts UI 设计”修改了 `console/src`，拆分页面与抽屉，并维护 `scripts/v2_ui_acceptance.cjs`。本次阶段提交只包含原已验证工作台；新 UI 和其文档留在本地，需核对该会话最新结果，再独立提交。离线夹具 UI 通过不等于真实 Compose 业务通过。改版后的 `v2_browser.cjs` 可能已使用新选择器；旧运行镜像与它不能直接混测。
6. **补齐交付。** 新测得数据写入 V2_ACCEPTANCE、V2_IMPLEMENTATION、V2_RESUME，运行 `v2_evidence.py` 更新副本/摘要；检查 README 截图与实际部署界面一致。按仓库 CI 验证，再提交后续 PR。不要填生产用户量、可用性承诺或未实测提升比例。

## 已完成，避免重复消费或重做

- Java 21 / Spring Boot / JDBC / Security / Redis Session；MySQL 状态、Outbox、revision/租约/fencing、预算台账；RabbitMQ 持久投递、确认及去重；租户配额、角色、CSRF、作用域 Token、复核/导出。
- Python Worker 的 PREPARE/MODEL/VERIFY，完整 MiniMax thinking/tool 内容块，DeepSeek reasoning/tool 历史，工具范围控制、最多两轮修订、每 HTTP 尝试独立记账、UNKNOWN 保留预算。
- 两 API、四 Worker、真实 MySQL/Redis/RabbitMQ，Prometheus/Grafana/OTel、独立证据及导出持久卷。
- 本机回归：Python 996 passed / 1 skipped；真实 Testcontainers 16 passed；公开 HTTP/浏览器/故障/旧记录归档证据见验收表。
- 真实模型已完成，不需要为“可用性”再探测：MiniMax 开发 12、冻结 24；DeepSeek 预选冻结 12；110 次 HTTP，保守费用 ¥0.574998，未知预留 0。数字契约结果不是独立人工质量判断，原始结果保留，不因结果好/坏重抽样。
- 纯规则路径跑过全部 36 条：27 有证据数字、9 缺证据拒绝，模型调用 0。

## 数据、凭据与预算

`.env.v2` 已由用户本地保存凭据，gitignored。不要把任何值打印、复制到文档或提交。默认 `MODEL_PROVIDER=fake`、`FINRECEIPTS_OFFLINE=1`；压测必须保持这两项边界。旧 CLI/SQLite、`evals/agent_backend_v1` 和历史运行均保留。

本期总授权 **20 元**，不是每个代理各 20 元：评测与业务各 10 元上限。评测用 `runs/platform-v2-live/budget.jsonl`，业务用 MySQL provider_budget/attempts；不要删除日志、重建数据库或清空 UNKNOWN 释放额度。没有新增真实评测需求就不再调用付费模型。

故障演练留下一个明确的 fake UNKNOWN 任务 `7e365a09-60e9-412e-912e-642bfb8a0b61`，预留 1 微元仍在，用来展示调用不明后的保守占用。不要把它当成真实供应商账单或无故抹掉。负载任务和十万行播种均标注合成数据；它们不是生产用户使用量。

## 工具与安全操作

本机 Python：`.venv\Scripts\python.exe`；JDK：`.cache\v2-tools\jdk-21.0.12.1+1`；Maven：`.cache\v2-tools\apache-maven-3.9.11\bin\mvn.cmd`，本地仓库 `.cache\m2`。Testcontainers 的 Ryuk 0.12 缓存层曾在 C 盘满时损坏，本机验证使用 `TESTCONTAINERS_RYUK_CONTAINER_IMAGE=testcontainers/ryuk:0.13.0`。不要全局 prune 来解决这一历史问题。

已有实际服务入口 `http://127.0.0.1:8090/`；账号为 alpha/beta 的 admin/analyst/reviewer，密码读取 `.env.v2` 的 BOOTSTRAP_PASSWORD。停止用 `docker compose --env-file .env.v2 -f compose.v2.yml stop`，不要 `down -v`。这台机器还有其他项目与会话，禁止宽泛结束所有 Python/Node/Docker 进程。

Git 推送与合并的是阶段检查点；剩余事项以上述 UNRUN / INTERRUPTED 为准。最终接续目标是：完整容量与过载证据、新 UI 的真实业务回归，以及对这些实测结果负责的简历材料。
