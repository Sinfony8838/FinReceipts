# 故障恢复与并发一致性

FinReceipts 的可靠性验证围绕财务证据的存储与任务执行展开：缓存刷新失败时保留上一份完整数据，
多个进程竞争时只允许一个状态变更胜出，模型调用后提交失败时保留已预留的预算。
这些场景使用真实临时文件、SQLite 数据库和独立进程；模型响应使用 FakeLLM。

## 已复现的问题与修复

2026-10-18，从 `265ca8e` 开始，工作区初始干净。
修改前全量离线测试为 **643 passed / 1 skipped**。

| 问题 | 修改前证据 | 修复 |
|---|---|---|
| 缓存刷新直接截断正式文件，序列化失败会破坏已缓存事实 | 新增的两个序列化失败用例均出现 JSONDecodeError；首次写入失败也留下坏缓存 | 每个写入者使用同目录独立临时文件，完整关闭 gzip、flush、fsync 后替换正式文件 |
| 读请求可能看到正在写入的 gzip | 暂停写入并并发读取，旧实现出现 EOFError | 只有完整文件进入正式路径；失败时删除本次临时文件，旧文件字节保持不变 |
| 数据库争用返回通用 503，缺少重试指引 | 持有真实 SQLite 写锁，新 API 测试因缺少 Retry-After 失败 | 按 SQLite 错误码识别 StoreBusy，默认等待从 10 秒改为 1 秒，API 提供 Retry-After；要求先刷新状态 |
| Windows 并发替换偶发拒绝访问 | 第一轮全量回归出现 WinError 5，664 passed / 1 failed / 1 skipped | 只对本地 replace 的 Windows 错误 5 / 32 / 33 最多尝试 5 次，累计退避 150 ms；不重复序列化、抓取或模型调用 |

缓存修改涉及 [JsonCache.put](../src/finreceipts/tools/http_cache.py)，数据库修改涉及
[RunStore.connection](../src/finreceipts/runtime/store.py)、
[RunService.advance](../src/finreceipts/runtime/service.py) 与 [API 错误映射](../src/finreceipts/app/runs.py)。

同目录替换避免跨文件系统移动；不完整文件不作为缓存命中。
同一 URL 的两个成功写入者采用最后一次替换胜出，未实现抓取请求合并或上游版本新旧比较。
Windows 短暂占用经过有界重试仍失败时，写入报错并保留旧文件，不退回直接覆盖。
进程被强制终止可能留下 `.tmp` 文件；读取忽略这些文件，不自动清理可能仍属于其他写入者的文件。
该方案未证明断电、网络文件系统或目录元数据持久化行为。

## 故障测试矩阵

| 企业常见情形 | 故障注入方式 | 断言 |
|---|---|---|
| 首次录制或刷新时序列化失败 | 不可序列化对象 | 无坏缓存发布；旧文件逐字节不变 |
| 写入途中有读取请求 | Event 暂停实际写流 | 另一缓存实例读到上一完整版本 |
| 刷盘或替换失败 | fsync / replace 抛出 OSError | 保留旧字节，清理本次临时文件 |
| 并发刷新同一 URL | Barrier 同步两个写入者 | 最终为其中一份完整 payload，无混写 |
| Windows 短暂或持续文件占用 | 注入错误 5 / 32 / 33，并检查调用与退避次数 | 短暂占用只重试 replace；持续占用有界退出；非 Windows 权限错误直接报错 |
| 缓存写到一半进程死亡 | 子进程 os._exit(23) | 旧文件可读，后续写入可恢复，遗留临时文件不被读取 |
| 重复请求同时抵达多个 worker | 四个 spawn 进程使用同一幂等键 | 同请求得到同一 run_id，只产生一条 created 事件 |
| 同键不同请求竞争 | 四进程提交不同正文 | 一个成功、三个冲突，不混用请求 |
| 多 worker 同时推进 | 四进程领取同一 revision | 一个成功、三个冲突，无预算额外消耗 |
| 状态已更新但事件写入失败 | SQLite trigger 拒绝 INSERT | 状态和事件全部回滚，再次领取成功 |
| 状态已更新但进程突然退出 | SQL trace 在事件 INSERT 前 os._exit(29) | 重建 Store 后与原导出完全相同 |
| 导出期间发生其他写入 | 建立读快照后另一连接取消任务 | 导出保持同一版本，后续读取能看到取消 |
| 模型调度前数据库被占用 | 真实连接持有 BEGIN IMMEDIATE | 503 和 Retry-After；事件及预算不变，模型调用为零；释放锁后推进成功 |
| 模型返回后无法提交 | FakeLLM 返回前另一连接占用写锁 | 保留 running 与已用预算，未提交回答；租约到期后显式恢复，耗尽预算不再调用模型 |
| 存储故障被误认为可重试争用 | SQLITE_FULL / IOERR / CORRUPT 注入 | 不转换为 StoreBusy；API I/O 错误无争用重试头且不泄露原始异常 |

测试入口：[缓存故障](../tests/test_cache_resilience.py)、
[跨进程与事务故障](../tests/test_store_resilience.py)、[API](../tests/test_run_api.py)。
原有 [runtime 测试](../tests/test_runtime.py) 继续覆盖取消、租约 fencing、预算先预留、证据篡改和恢复。

## 排查与恢复

1. 收到 503 且带 `Retry-After`：等待建议间隔后 GET 当前任务，不直接循环执行 advance。
2. 如果仍为 `ready`：使用当前 `revision` 推进一步；旧 revision 得到 409 时重新读取。
3. 如果为 `running`：调用可能已发生。先等待租约到期，再明确确认可能重复外部调用后 resume。
   预留额度不会退还；恢复前应核对是否还有预算。
4. 如果为 `completed` 或 `cancelled`：查看结果或取消状态，不再推进。
5. 如果 503 没有争用重试头：按执行或存储错误排查，不假设一定会自行恢复。
6. 缓存刷新报错：先确认旧缓存可读及磁盘可用空间，再检查目录权限或文件占用。
   清理遗留临时文件前停止相关写入进程，并确认目标不是正式 `.json.gz` 事实文件。

`Retry-After` 是客户端等待提示，不能保证下一次成功。预算记录的是逻辑调度次数，
不能把 SDK 重试或供应商实际计费解释为恰好一次。

## 复现与验收

本地最终验收：Windows、CPython **3.12.11**、SQLite **3.50.4**；
**672 passed / 1 skipped，40.22 秒**。比修改前增加 29 个展开后的测试用例。
跳过的是需要 Windows symlink 权限的既有路径逃逸测试，不计作通过。
`ruff check .`、`ruff format --check .`（123 个 Python 文件）和 `git diff --check` 均通过。

本地原始记录保存在 `runs/enterprise-resilience-20261018/`：
`full.xml` 保留 Windows 并发失败，`full-fixed.xml` 保存最终回归，
`evidence.json` 记录源文件与测试结果 SHA-256。该目录属于忽略的本地证据，不随仓库分发；
外部复核使用以下命令重新运行。未进行本轮浏览器验收、真实模型评测或生产负载测试。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_cache_resilience.py tests/test_store_resilience.py tests/test_run_api.py tests/test_runtime.py -q
.venv\Scripts\python.exe -m pytest -m 'not live' -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
```

全部故障操作限制在 pytest 的临时目录，不修改既有运行数据库、SEC fixtures 或历史实验结果。
Windows 文件替换竞争额外执行了 50 轮同步双写，共 100 次成功发布，未出现混写或临时文件残留。
这是针对已复现竞争窗口的重复验证，不是吞吐量或容量基准。
CI 新增 Windows Python 3.12，与既有 Ubuntu Python 3.11 / 3.12 一起执行离线回归；
远端运行结果需在推送后单独确认。

## 技术讲述与后续门槛

可现场演示的问题是“缓存可重建，为什么还要保证写入完整？”：它是后续财务事实输入，
一次刷新失败不应破坏最后可用版本。先展示失败测试，再展示文件发布边界和同样用例通过。

另一条可追问的问题是“数据库忙了为什么不能直接重试整个 Agent 节点？”：外部模型调用
和本地事务之间没有共同提交点。示例明确保留未知调用的预算与租约，通过查询和显式恢复处理。
可展示状态、调用计数和事件链，不虚构生产流量、节省金额或服务可用性指标。

后续独立迭代按可验收问题推进：

- 可观测性：已补充[安全错误码、请求关联与日志验证](REQUEST_DIAGNOSTICS.md)；集中采集和指标后端仍待实现。
- 容量与背压：已实现[单进程准入、过载拒绝与断连实验](ADMISSION_CONTROL.md)；分布式协调、排队公平性与生产容量仍待验证。
- 安全边界：已补[本机 Host 与浏览器来源检查](BROWSER_BOUNDARY.md)。认证、逐任务授权和租户隔离尚未实现；当前仍为本地研究软件。
- 长期运维：已实现[在线备份、非覆盖恢复与故障演练](BACKUP_RESTORE.md)；版本迁移、异地存储及数据保留策略仍待实现。不得直接用真实历史记录做破坏测试。
- 容器存储：已补[运行库命名卷与重建验收门禁](CONTAINER_STORAGE.md)；本机引擎不可用，实际重建和卷权限验证仍为 `BLOCKED_UNRUN`。

实现依据：[Python 文件替换与 fsync](https://docs.python.org/3.12/library/os.html)、
[SQLite 错误码](https://sqlite.org/rescode.html)、[SQLite 事务](https://www.sqlite.org/lang_transaction.html)。
