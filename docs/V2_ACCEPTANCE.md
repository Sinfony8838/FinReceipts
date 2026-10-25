# FinReceipts 2.0 阶段验收：2026-10-24

用户要求先暂时收尾并交接给 zcode。**业务实现与下列验收已有证据；容量目标尚未全部完成，不能宣称全计划验收通过。** 接续步骤见 [ZCODE_HANDOFF.md](ZCODE_HANDOFF.md)。

| 项目 | 结果 | 原始证据 |
|---|---|---|
| Python 回归 | 996 passed / 1 skipped，133.80 秒 | `runs/v2-regression-final.log` |
| Java / 真实中间件 | 16 passed，0 failure，73.89 秒 | `server/target/surefire-reports/io.finreceipts.PlatformIntegrationTest.txt` |
| 双 API 公开接口 | PASS：100 并发同键、正文冲突、跨租户任务/事件/导出、CSRF、规则/研究/缺证据、复核不改机器结论、SSE 补读 | `runs/v2-acceptance-20261024-222805.json` |
| Edge 业务闭环 | PASS：提交、证据、复核、已审核导出 | `runs/v2-browser-1792852158170/` |
| Edge 异常流程 | PASS：JSON 批量、部分失败、取消、驳回、驳回结果不进入交付 | `runs/v2-browser-cases-1792852911946/` |
| 旧记录只读导入 | PASS：合成旧格式运行的原始字节与事件链保留，跨租户拒绝，不能当任务恢复 | `runs/v2-archive-*.json` |
| 实际 Compose 故障 | PASS：API1 停止、MQ 中断、Redis 重启、MySQL 只读、租户配额隔离、Worker 退出后 UNKNOWN/保留预算 | `runs/v2-faults-20261024-222735.json` |
| 监控 | 双 API Prometheus target UP；Grafana health OK；UNKNOWN 两副本均可见 | `runs/v2-observability-report.json` |
| 真实模型 | MiniMax 开发 12/12、冻结 24/24；DeepSeek 对照 12/12，均为最终数字目标契约，含缺证据拒绝 | [评测与局限](V2_EVALUATION.md) |
| 纯规则路径 | 36 条：27 SUPPORTED、9 UNSUPPORTED，模型 HTTP/token 为 0 | `runs/v2-rules-evaluation.json` |

复核是针对结果版本的持久决定；人工接受不能改写机器判断。模型数据共 48 个模型-样本组合、110 次真实 HTTP 尝试，保守费用估算 ¥0.574998。工具错误结果 26/82，包含证据缺失与范围拒绝；不能写成“从未出错”或“研究准确率 100%”。

## 容量结果及尚未执行项

环境是 Windows 11 build 26200、18 逻辑处理器、25,196,617,728 字节内存、Docker Desktop/WSL 2；所有服务在同一台开发机，运行时还有其他桌面活动。每 API 限制 1.5 CPU / 768 MiB，每 Worker 0.5 CPU / 256 MiB；MySQL 2 CPU / 1 GiB，Redis 1 CPU / 384 MiB，RabbitMQ 1 CPU / 512 MiB。具体镜像 ID 与资源上限在每轮 JSON 中，不能外推为跨机器高可用。

| 场景 | 实测 | 判定 |
|---|---|---|
| 10 万摘要，Windows httpx，200 RPS × 600 秒 | 120,000 请求；113,326 成功；6,674 次 429；非预期错误 0；成功 P95 656 ms；按计划发起时间计 P95 23,783 ms；最大客户端积压 27,467 ms | **未达标**；`v2-load-query-20261024-224128.json` |
| 同库，Node HTTP 客户端，200 RPS × 10 秒 | 2,000/2,000 成功；拒绝/异常 0；P95 23.18 ms；最大客户端积压 34.32 ms | **短测通过，不能替代十分钟验收**；`v2-load-query-20261024-224310.json` |
| Node 200 RPS × 600 秒复测 | 运行中抽样曾见约 200 成功 RPS，随后进程中断，没有生成完整结果 | **INTERRUPTED，不能计为通过** |
| 200 并发批次 × 5 项，目标接纳 P95 ≤ 500 ms | 尚未运行 | **UNRUN** |
| 1,000 条 SSE，恢复与完整性 | 尚未运行；此前仅业务级断线补读通过 | **UNRUN** |
| 延迟桩 5 项/秒 × 600 秒、队列稳定性 | 尚未运行 | **UNRUN** |
| 提高延迟/负载，过载拒绝及排空恢复 | 尚未运行 | **UNRUN** |

首轮存在明显客户端调度积压，但这一现象不能单独证明服务端已经满足目标。新客户端短测只用于诊断。查询是有认证的首屏 30 条摘要，使用真实 MySQL/Redis，不加载逐项事件链；没有访问真实模型或 SEC。十万行是单独播种的 CANCELLED 合成摘要，不能当真实业务量。

原始文件保存在 `runs/`；便于随源码交付的副本和摘要在 [evidence/v2-20261024](evidence/v2-20261024/manifest.json)。大容量原始样本使用 gzip，manifest 记录解压前原文 SHA-256 与副本 SHA-256。失败、中断与通过记录分开保留。

## 当前代码与运行界面

本轮业务 UI 证据绑定运行镜像 `sha256:2744a87aafbd447d2f396e3a42187f140676271769dae37f5328543ba7684718`。另一个会话正在重构 Vue 设计；本次 Git 检查点保留该镜像对应的基础工作台，新设计仍作为本地未提交改动，后续应单独验收合并。

Docker 数据已迁往 `D:\FinReceipts\.cache\docker-data\DockerDesktopWSL`。后续启动又出现与 [Docker 官方仓库问题 #554](https://github.com/docker/desktop-feedback/issues/554) 相同的 AF_UNIX 残留错误；本机证据只确认路径访问失败与启动退出，不宣称已经定位 Windows 内核根因。恢复脚本只保留重命名运行目录，不动 VHDX、镜像和数据卷。见 [运行手册](V2_OPERATIONS.md)。
