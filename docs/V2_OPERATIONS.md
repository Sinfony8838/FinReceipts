# 故障演练与现场演示

运行环境、账号和启动命令见 [V2_START.md](V2_START.md)。这里使用本机 `finreceipts-v2`、离线 SEC 样本和 fake 模型；不应把故障脚本指向存有真实业务的环境。演练不会删除数据卷，也不会重置预算。

## 十分钟业务演示

1. 用 `alpha.analyst` 登录，提交 AAPL 2023 年收入、净利率和同比；再用 JSON 清单提交 `AAPL` 与离线夹具中不存在的 `NOFIXTURE`。前者有来源证据，后者明确失败，批次可部分完成。
2. 打开任务证据页，核对金额、单位、年度、来源、执行版本和事件。直接指标不调用模型；研究任务显示模型尝试及预算记录。缺证据结果不能显示 SUPPORTED。
3. 切换 `alpha.reviewer`，填写理由后接受或驳回；查看机器结论与人工决定各自保留。导出只包含已接受版本，被驳回的任务不会进入交付包。
4. 用 `beta.analyst` 登录：不能读取 alpha 的任务、事件或交付包。alpha 超过自己的待执行配额时，beta 仍能提交。
5. 打开 Grafana 查看队列、UNKNOWN、预算占用和指标更新时间；若任务为 UNKNOWN，先查看尝试与事件，再决定是否明确接受可能重复调用的风险。取消任务不代表供应商撤销收费。

## 可重放的故障脚本

```powershell
.venv\Scripts\python.exe scripts/v2_acceptance.py
.venv\Scripts\python.exe scripts/v2_faults.py
.venv\Scripts\python.exe scripts/v2_archive_check.py
```

`v2_faults.py` 依次停止本项目的 API1、RabbitMQ、Redis，短时将本项目 MySQL 设置为只读，临时压低 alpha 配额，并结束一个带 25 秒模型桩延迟的独立测试 Worker。脚本在 finally 中恢复服务。原始 JSON 保存每项检查及失败状态。其他项目的容器与 SQLite 不参与操作。

| 注入点 | 应观察到的结果 | 本期证据层级 |
|---|---|---|
| API1 停止 | 另一副本继续使用 Redis 会话处理请求 | Compose 实际停止与 HTTP 验证 |
| 事务提交后 RabbitMQ 不可用 | Outbox 保留，恢复后任务继续 | Compose 实际停止与恢复 |
| 发布成功、确认/消费 ACK 丢失 | 可重复投递，但旧 revision 不能再次领取 | 真实 RabbitMQ 的集成测试中模拟确认丢失 |
| Redis 停止 | 新请求返回服务不可用，不能绕过共享限流 | Compose 实际停止与恢复 |
| MySQL 只读 | 新提交失败且事务无半成品；已接纳任务恢复后完成 | Compose 实际只读与恢复 |
| Worker 预留调用后退出 | 到期进入 UNKNOWN；预算预留继续保留 | 实际结束测试 Worker；fake 调用，不访问供应商 |
| 调用前租约到期 | revision 递增后重新排队 | MySQL 集成测试 |
| 模型已结算、检查点未提交 | 仍需 UNKNOWN 确认，不能假设响应已安全保存 | MySQL 集成测试 |
| 已提交结果重复提交、取消后迟到提交 | 相同提交复用；过期提交冲突，取消状态保留 | MySQL 集成测试 |
| 上游限流或网络超时 | 明确可重试拒绝有次数上限；超时进入 UNKNOWN | Python 协议故障注入，非真实供应商事故 |

这张表区分实际进程/中间件故障、真实数据库上的边界模拟和协议故障注入，不能把三者都写成生产故障演练。

## 排障顺序

- 先看批次任务状态、error_code、事件 revision，再看 attempts 中的 RESERVED/UNKNOWN/SETTLED。UNKNOWN 不自动重试，不通过改数据库清空占用。
- 接纳成功但长时间排队：查看租户待执行/并发配额、Outbox、RabbitMQ 连接和 Redis 可用性。排队本身不预留模型预算。
- 证据缺失：确认 ticker、年度、指标及当前离线/在线数据模式。`evidence_unavailable` 是来源准备失败；UNSUPPORTED 是已有核验结果中的证据不足，两者有不同含义。
- 交付文件复制滞后：检查 artifact_copies 与复制失败指标。SQL 保存权威原文和摘要，复制器恢复后补写持久卷。
- Docker 磁盘异常：先检查 C/D 盘空间和 `docker info`。本机 Docker 数据位于 `D:\FinReceipts\.cache\docker-data\DockerDesktopWSL`，该目录是运行数据，不能按缓存删除。迁移使用 Docker 设置，避免 factory reset 或 `down -v`。

## 压测重放

先确认 `.env.v2` 与已发布配置都是 fake。负载脚本会拒绝非 fake 配置，真实 SEC 和模型不参与。

```powershell
# 只在新的验收环境播种一次；不会删除已有任务。
.venv\Scripts\python.exe scripts/v2_load.py seed --rows 100000
.venv\Scripts\python.exe scripts/v2_load.py query --query-client node --rps 200 --duration 600
.venv\Scripts\python.exe scripts/v2_load.py sse --connections 1000 --duration 65
.venv\Scripts\python.exe scripts/v2_load.py burst
# 修改模型桩延迟前先等已接纳任务执行完，以免重启造成 UNKNOWN。
$env:FAKE_DELAY_SECONDS='0.2'
docker compose --env-file .env.v2 -f compose.v2.yml up -d --no-deps worker1 worker2 worker3 worker4
.venv\Scripts\python.exe scripts/v2_load.py queue --rps 5 --duration 600
.venv\Scripts\python.exe scripts/v2_drain.py <本轮负载报告的绝对路径>
```

每轮报告记录硬件、容器 CPU/内存上限、镜像 ID、工作区源码摘要、成功/限流/异常分布以及调度延迟。`v2_drain.py` 只观察本轮已接纳的合成任务，不取消或删除任务；完成吞吐与 API 接纳延迟分开统计。查询场景读取 10 万条摘要中的首屏 30 条，不能外推为任意复杂查询的性能。
