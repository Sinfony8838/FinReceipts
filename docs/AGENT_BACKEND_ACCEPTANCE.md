# Agent 后端实战主线：2026-10-21 验收

新增目标核验、按认证用户隔离的幂等创建、显式旧库副本迁移、分组开发/冻结数据及实战手册。
新真实模型评测为 **UNRUN**；下列数值来自固定响应和人工注入故障的工程验收。

## 工程证据

- Python 全仓离线回归：**978 passed / 1 skipped**，101.37 秒；跳过为 Windows 符号链接权限。
- Node 恢复记录合同：**18 passed**，单列，不与 Python 计数相加。
- Ruff 检查、格式检查和 Git diff 空白检查通过。
- 真实 Edge **155.0.4283.62**、Node **v24.14.0**：
  创建响应丢失、服务重启、刷新及原身份确认后，同键、同正文、同 run ID；独立 SQLite 查询为一个任务，
  一个创建事件，零模型调度。跨用户导出为 404，旧身份列表迟到响应被丢弃，待确认请求不跨身份重发。
- 认证导出与原始 HTTP 响应逐字节相同，并通过事件链检查；令牌未进入浏览器存储，页面错误为零。
- 本轮最终及先前验收服务均已停止。原历史实验和用户现有运行库未修改。

浏览器原始输出在本机 `runs/agent-backend-20261021/browser-final-race/`；
全仓 XML 在 `runs/agent-backend-20261021/regression-final.xml`。
初始沙箱 socketpair 停滞、临时目录问题、测试替身的旧签名、表单默认值问题、下载脚本超时均保留原始记录；
没有把失败或未完成记录计作通过。第二轮下载脚本超时遗留服务已按日志 PID 核对身份后停止，见对应 cleanup.json。

## 冻结比较

开发集和冻结集各 20 条，同一基础目标及其变体不跨集合。只对开发集调整策略，锁定后首次运行冻结集。
策略源码标识：`0f624d5824c96bd896572c616b8bd89b2384aaaffadebb58a264571f977da41e`。

| 策略 | 冻结集契约目标完成 | 数值匹配/可作答 | 安全拒绝缺证据 | 共享初稿调用 | 修订增量 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 直接回答 | 8/20 | 4/16 | 4 | 32 | 0 |
| 核验但不修订 | 8/20 | 4/16 | 4 | 32 | 0 |
| 核验后有限修订 | 20/20 | 16/16 | 4 | 32 | 12 |

共享初稿不能三次相加。每个 split 实际为 **44 次脚本模型调度、16 次工具尝试、0 次外部 HTTP**。
开发集同口径结果亦为 8/20、8/20、20/20。全部 40 条案例不是 40 个业务用户或业务效果样本。
这说明预设响应下的契约执行符合预期，不构成真实模型准确率或泛化收益。

冻结集完整运行耗时 0.394560 秒，是本机脚本响应的组件验收时间，
不代表在线模型耗时或服务 SLA。逐条耗时、分布散点、P50/P95 及样本数在
[完整报告](../evals/agent_backend_v1/evidence/frozen-report.html)和证据 ZIP 内的 latencies.csv 中保留。
这是同两家公司快照上的内部集合，原始获取时间未知；解析和容差仍是共享限制。

## 三个失败案例：均为人工注入

### 1. wrong_unit

案例 `AAPL-2023-direct-net_income:wrong_unit`，原始回答 `ANSWER: 96995000000.0元`。目标核验原因：`unit_mismatch`。

### 2. time_confusion

案例 `AAPL-2023-direct-net_income:time_confusion`，原始回答 `ANSWER: 93736000000.0 USD`。目标核验原因：`value_mismatch`。

### 3. tool_timeout

案例 `AAPL-2023-direct-net_income:tool_timeout`，原始回答 `ANSWER: N/A`。目标核验原因：`no_final_number`。工具原始错误 JSON 带 `tool_timeout`；不是真实上游故障统计。

上述失败在直接回答和仅核验组保留；有限修订组通过目标证据反馈修正。
对照组不更改原始回答，不能将“发现错误”描述成“已经改正”。

## 原始结果与复算

[开发集证据 ZIP](../evals/agent_backend_v1/evidence/dev.zip) ·
[冻结集证据 ZIP](../evals/agent_backend_v1/evidence/frozen.zip) ·
[摘要](../evals/agent_backend_v1/evidence/frozen-summary.json) ·
[输入与策略锁](../evals/agent_backend_v1/)

将 ZIP 解压至一个新的空目录，再执行 `scripts/check_agent_backend_evidence.py --suite evals/agent_backend_v1 --output 解压目录`。
检查器不调用模型，核对哈希、原始响应绑定、评分、分母、调用次数及耗时分布；篡改答案负例会失败。
哈希不是来源认证或数字签名。已消费的冻结集不自动重跑；继续修改策略需另建数据版本。

源码工作树尚未提交，provenance 的 Git commit 是父提交；实现以源码摘要和本机
`runs/agent-backend-20261021/source-snapshot.zip` 中的文件摘要固定，不能只 checkout 父提交复现实验。
配置和包版本另见本机 acceptance.json 与 ZIP 内 provenance.json。

[开始实战练习](AGENT_BACKEND_PRACTICE.md) · [认证与迁移](AUTH_AND_IDEMPOTENCY.md) · [评测口径](AGENT_BACKEND_EVALUATION.md)
