# Agent 后端契约评测

新增评测使用固定 SEC 事实、分组数据和脚本响应，验证主链路的核验及故障处理。
**它是工程契约验收，新真实模型评测状态为 `UNRUN`。**
2026-10-16 保存的真实模型实验继续单独保留，不能把其成绩移植到新增目标模式上。

## 固定数据与开发顺序

数据位于 [`evals/agent_backend_v1`](../evals/agent_backend_v1/)。准备命令从原有 66 个候选目标中，
排除历史已实测的 30 个目标，再以种子 42 选取 8 个基础目标：直接数值 4 个、比例 2 个、同比 2 个。
每个目标有普通、错误单位、时间混淆、缺证据、工具超时五种情形，共 40 条。
原题及其全部派生变体作为一组，开发集与冻结集各 4 组、20 条。

答案键使用证据行上的 Decimal 算术独立复算，并核对保留原始事实中的数值、单位、期间、申报编号及日期。
运行时的数值评分仍共用通用数字解析与既有容差，因此不是独立构建的外部评测器。
两组仍来自同样两家公司的快照，不能据此主张跨公司泛化；快照原始获取时间仍未知。

开发时只运行 dev。确认策略与检查稳定后，用开发运行的摘要锁定源码/依赖标识及数据哈希，
再运行 frozen。首次冻结运行写入消费标记，拒绝重跑。锁定后实现变化也会拒绝运行。
若看过冻结结果后继续修改策略，应把旧集作为回归集，重新建立另一版冻结数据。
测试中的临时副本只运行开发样本，不提前执行真实冻结输出。

```powershell
# 准备与结果目录都必须是新路径；不要覆盖当前已保存的数据和结果。
uv run python scripts/agent_backend_eval.py prepare --suite runs/my-suite
uv run python scripts/agent_backend_eval.py run --suite runs/my-suite --out runs/my-dev --split dev
uv run python scripts/agent_backend_eval.py freeze --suite runs/my-suite --dev runs/my-dev
uv run python scripts/agent_backend_eval.py run --suite runs/my-suite --out runs/my-final --split frozen
uv run python scripts/check_agent_backend_evidence.py --suite runs/my-suite --output runs/my-final
```

## 三种策略与分母

| 策略 | 实际行为 | 调用归属 |
| --- | --- | --- |
| direct | 展示初始回答；评分器内部检查用于测量 | 共享初始生成 |
| audit_only | 同一回答增加核验，不更改原文 | 共享初始生成，新增模型调用为零 |
| bounded_revision | 未通过时提供目标证据反馈，最多两轮修订 | 共享初始生成 + 实际修订增量 |

工具开关、模型参数和初始提示相同。初始响应只生成一次，不能把三组的共享调用相加当作实际请求量。
提供检索事实后的修订应称为证据辅助修正，不能描述为纯模型自我纠错。
工具超时由 provider 边界注入，工具错误 JSON 及尝试次数保存到轨迹。
缺证据在前置检查拒绝，模型调用为零。

`success / total` 表示契约目标完成：数值正确，或按要求安全拒绝缺证据。
同时单列 `numeric_matched / answerable`、完成、拒绝、错误和未运行数量，避免把拒绝当作正确数值答案。
仅核验组的数值匹配与直接回答相同是预期结果，发现问题不等于已改正答案。

## 输出与复算

- `cases.json`、`manifest.json`、`facts.json.gz`：输入、分组、来源及 SHA-256。
- 每题 JSON 与 `results.json`：原始回答、当轮审计、修订轨迹及失败代码。
- `trace.jsonl`：模型和工具在尝试前落一条记录，随后记录结果或失败；真实运行另保存每次 HTTP 预留、响应与 usage。
- `summary.json`：每组成功数/总数、错误类型、调用数及耗时；`latencies.csv` 和 HTML 散点图保留原始分布。
- `provenance.json`、`SHA256SUMS.json`：源码、锁文件、依赖版本和输出摘要。

模型失败不能消失在分母里；已开始而用量未知的请求保留为未知，不算免费。
修订失败也不能抹掉直接回答组和仅核验组已完成的结果。
P50/P95 同时展示样本量与原始耗时，小样本分位数不代表稳定 SLA。
报告至少列三条不同的失败案例，并标注它们来自脚本注入；历史真实失败另见原实验报告。
摘要和哈希不是数字签名，不能防止持有全部文件的人重写整套证据。

只读检查器核对输入与输出哈希、分母、共享初稿、原始模型响应绑定、独立数值评分、物理调用数和耗时分布。
修改原始回答的负例会被拒绝，不能只检查汇总文件是否存在。

## 未来真实模型入口

只有显式传入 `--live-config` 才会创建真实模型客户端。配置模板中的模型、价格和预算没有默认授权值，
必须全部填写。继续使用现有环境变量指定 HTTPS Anthropic 兼容端点与密钥变量名称。
模型升级关闭，温度 0，最多输出 1,024 token，修订上限两轮；每次 HTTP 尝试先预留预算，重试另计。
未知结果保留预留；协议出现思考块、模型/参数超界或预算不足时停止。
价格来源和核对时间由运行人显式登记，报告给出估算而不是供应商账单承诺。

真实入口只选择普通问答，每个 split 为 4 题；它不会把脚本预设的错误回答伪装成模型响应。
错误单位、错期间、缺证据和工具超时的 40 条主链路验收仍在离线部分报告。
未来扩大真实模型研究时，应独立扩建题集、固定新配置和费用授权，再作比较。

```powershell
# 此命令会产生费用；本次开发没有执行。配置文件必须是自行填好的副本。
uv run python scripts/agent_backend_eval.py run --suite runs/my-suite --out runs/my-live-dev --split dev --live-config PATH_TO_EXPLICIT_CONFIG
```

现有 SDK 的底层重试逐 HTTP 尝试记录，不自动重新跑整题。真实运行与离线结果使用不同消费标记及新目录。
