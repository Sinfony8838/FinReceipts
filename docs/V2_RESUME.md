# 项目经历：Java 后端 / AI 后端两种写法

项目名称：**FinReceipts——研究团队财报批量核验平台**。项目性质：个人工程项目，本机多副本部署与受控实测。不要写成已有企业客户的生产系统。

## Java 后端方向

技术栈：Java 21、Spring Boot、Spring Security、Spring JDBC、MySQL、Redis、RabbitMQ、Vue 3、Docker Compose。

- 实现研究员批量提交、异步核验、异常处理、人工复核和审核版本导出的业务闭环；采用组织成员角色、共享会话、CSRF 和带作用域的 API Token，所有查询由服务端绑定租户。
- 基于 MySQL 事务与 Outbox 解耦任务接纳和消息发布，使用任务版本、执行租约与 fencing token 处理重复消息、取消竞争及迟到提交；模型调用结果不明进入 UNKNOWN 并保留预算占用。
- 将 API 速率、租户待执行数、租户并发、模型并发和金额预算分开控制；通过租户轮转、短事务、有界连接池、租户索引及游标分页实现可控调度与查询。
- 使用真实 MySQL/Redis/RabbitMQ 的 Testcontainers 集成测试，以及双 API、四 Worker 的 Compose 验收；已验证 100 个并发同键请求只生成一个批次、跨租户隔离与事件断线补读。容量数字引用独立实测报告。

## AI 后端方向

技术栈：Python、MiniMax/DeepSeek API、SEC XBRL、确定性核验、Java 协调服务、RabbitMQ、OpenTelemetry。

- 将直接指标、比率、同比计算与模型研究表述分路执行；按目标冻结公司、年度、指标、单位及财报来源，模型只访问当前任务允许的工具和证据范围。
- 适配 MiniMax-M3.1-Flash 与 deepseek-flash，持久化原生 thinking/tool 消息块，保留工具调用配对；模型、策略、证据和实现指纹随任务冻结，最多两轮修订，每次实际 HTTP 尝试独立预留与记账。
- 构建按公司隔离的 12 条开发样本和 24 条冻结样本，保存逐条输出、证据、模型信息、耗时与用量；MiniMax 冻结集数字目标契约由初稿 18/24 达到有限修订后 24/24，其中 6 条为缺证据拒绝，不能表述为“财务分析准确率 100%”。
- 在预先选定的 12 条冻结子集上完成 DeepSeek 对照，最终目标契约 12/12；全部评测共 110 次实际 HTTP 尝试，保守估算费用 0.574998 元。该结果说明小样本数值输出与拒绝契约，不代表独立人工研究质量评估。

## 面试时讲透的一条路径

以“研究员提交 AAPL 年度净利率核验”为例：说明 API 如何认证、幂等键绑定谁、事务写哪些表；接着解释 Outbox 为什么允许重复发布、Worker 为什么只能执行当前 revision；再说明 reserve 与真正 HTTP 之间的崩溃为什么无法证明未收费；最后演示证据页、人工驳回/接受及审核版本导出。

面试官追问高并发时，先给出实测硬件、容器限制、负载、P95 和错误分布，再讨论瓶颈。不要用语言切换、线程数或容器数推导吞吐。面试官追问高可用时，明确当前是本机多副本，数据库、Redis 和 RabbitMQ 仍为单实例。

| JD 能力 | 源码/入口 | 可核对证据 |
|---|---|---|
| 事务、一致性、并发控制 | PlatformService、Scheduler、Flyway migrations | PlatformIntegrationTest；故障报告 |
| 认证与多租户 | Security、Api.identity、成员管理 | HTTP 隔离/CSRF/幂等验收 |
| 缓存与消息队列 | RateControl、Outbox、RabbitMQ consumer | MQ/Redis 重启和重复投递测试 |
| 可观测性与排障 | PlatformMetrics、OTel、Grafana | 本机容量与故障原始记录 |
| Agent 工程 | platform/engine.py、models.py、worker.py | 协议测试、冻结评测逐条记录 |
| 业务交付 | Vue 证据页、复核队列、审核导出 | Edge 页面验收与截图 |

实际岗位 JD、个人任职时间、团队人数与本人职责范围尚未提供；这些字段需要按真实经历填写，不能从项目源码推断。
