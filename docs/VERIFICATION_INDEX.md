# 验证索引与交付边界

提交邮箱隐私保护导致公开提交号与原始本地编号不同；源码树未变，
对应关系见[公开提交映射](PUBLIC_COMMIT_MAP.md)。以下历史测试、构建编号保留原始值。

本页是 **2026-10-18、仓库提交 `19be01f`** 的快照；运行代码最近变更为 `2101f7d`。
记录按实际验证的代码状态区分，
不把不同提交的测试数量累加，也不把历史实验当成当前实现的重新验收。
源码回归指向 `2101f7d`，安装验收指向 `19be01f`；后续仅修改文档时保留这些来源，
改动实现后应重新生成相应证据。
这是本地分支验证，不代表远程主分支或已发布的 v0.1.0 包已包含这些改动。

## 已验证什么

| 范围 | 实际代码状态与证据 | 可以支持的结论 |
| --- | --- | --- |
| 当前全仓离线回归 | `2101f7d`；Windows / Python 3.12.11；939 passed、1 skipped；Ruff 通过 | 当前本地测试通过；跳过为 Windows 符号链接权限，不是失败清零或全平台通过 |
| 终止失败与普通中断 | `2101f7d`；真实 Edge 桌面/手机、真实回环 HTTP、独立 SQLite | 数值超限/溢出不可重复恢复；原导出与预算保留；普通中断能显式恢复；失败后可另建任务 |
| 异常文本与零值比较 | `45a7fc8`；24 项新增回归、两组各 1,000 个差分样本、独立进程超时负例 | 长空白回溯与重复前文扫描得到修复；超限整次拒绝；零值/溢出相对误差不产生非法 JSON |
| 最新独立安装包验收 | `19be01f`；sdist → wheel → 新环境；真实 API/CLI、静态资源、重启导出及安装后的 Edge 故障验收 | 安装包实现摘要与 `2101f7d` 一致；终止失败和普通中断恢复均已覆盖。依赖按锁文件与哈希安装，不是远程发布或字节级可重复构建 |
| 公司名与财年语序 | `77c0527`；40 项新增正负例、两家公司 SEC 回放、安装后的 HTTP/CLI | 支持明确的公司名在财年前语序；错公司、季度、分部和重复年度仍拒绝 |
| 历史任务与导出保存 | `9c50f9e`；分页/坏记录回归与真实 Edge | 有界发现、插入范围固定、损坏记录隔离；浏览器验收保存原始导出字节并重新校验 |
| 真实模型策略对照 | 2026-10-16 的[独立实验记录](WORKFLOW_EVALUATION.md) | 30 题、两家公司、三种反馈策略的样本结果；本轮没有重新调用真实模型 |

当前 Python 实现摘要为：

```text
e0e31894b13153891474b7d76d058b2fe72b338aef73789f76f744d1cb284a03
```

摘要覆盖项目定义的 Python 源码及核心依赖身份，不代表浏览器资源、全部环境或供应商状态。
静态资源与截图另由各轮证据清单绑定；换行、依赖或实现变化后不要复用旧结论。

## 可复现入口

依赖准备见 [README](../README.md)。以下命令不调用真实模型；构建/安装可能访问包索引。
使用新输出目录，避免覆盖已有记录。浏览器脚本需要 Playwright 和 Edge。

```sh
python -m pytest -m "not live" -q
python -m ruff check .
python -m ruff format --check .
python -m pytest tests/test_numeric_input_limits.py tests/test_permanent_run_failure.py -q
node scripts/browser_permanent_failure.cjs runs/review-terminal-failure
python scripts/package_install_smoke.py --out runs/review-package-install
```

最近几轮的本地原始证据位于以下忽略目录，不随 Git 仓库分发：

| 目录 | 关键记录 |
| --- | --- |
| `runs/permanent-failure-20261018/` | `evidence.json`、`full.xml`、`browser-final-v2/summary.json`、原始导出与截图 |
| `runs/current-package-20261018/` | `build-evidence.json`、sdist/wheel、实际安装路径、`installed-browser/summary.json`、原始导出与截图 |
| `runs/numeric-input-20261018/` | `evidence.json`、`full-final-v2.xml`、超时与差分记录、`installed-final-v2/summary.json` |
| `runs/subject-period-20261018/` | `evidence.json`、修改前失败、修改后测试与安装包验收 |
| `runs/run-history-20261018/` | `evidence.json`、分页和导出重序列化负例、最终原始字节校验 |

外部审查者应运行对应脚本生成自己的输出。`evidence.json` 是记录与哈希索引，不是独立第三方认证。
早期发布记录保留在 [VALIDATION](VALIDATION.md)；详细实现变化见[工程日志](ENGINEERING_LOG.md)。

## 仍缺什么

| 能力或交付环节 | 当前状态 | 下一项有效证据 |
| --- | --- | --- |
| 远程 CI | 已配置 Windows/Linux 测试、安装包和容器 job；本轮没有远程通过证据 | 在实际分支/PR 上执行，保留提交号、平台与产物 |
| Docker 实际重建 | `BLOCKED_UNRUN`；仅配置解析通过，本机 WSL 尚未安装 | 在可用引擎执行现有隔离重建脚本，验证卷、重建前后导出及预算 |
| 多用户服务 | 未实现身份认证和逐任务授权；仍是本机单一信任域 | 先确定访问主体与数据归属，再用越权负例验证读、写、导出和恢复 |
| 跨进程总容量与长任务续租 | 准入仅限单进程；现有 lease 无心跳续租 | 多进程负载、超时与旧 worker 隔离实验，不只增加部署副本 |
| 生产运维 | 有请求关联、安全日志、容量快照和本地备份演练；无生产告警/切换验收 | 目标环境中的指标、告警、恢复演练与实测容量 |
| 全文财务正确性 | 仅有界年度子句和数值核验；会对未知语法弃权 | 独立标注的覆盖集与错误归因，不能用增加单元测试代替模型质量评估 |

近期最直接的交付缺口是远程 CI 和实际容器验证，而不是增加框架名称。需要多人部署时，
认证授权应先于暴露网络入口。现有故障契约和证据绑定应作为后续改造必须保留的不变量。
