# 构建包与隔离安装验收

日常开发使用 editable 安装，不能证明交付后的 wheel 可独立导入或提供页面资源。
本检查从源码构建 sdist，再由 sdist 构建 wheel；在新虚拟环境安装实际 wheel，
从没有源码的工作目录调用安装后的 API 与 CLI 入口。不会发布到 PyPI。

## 复现

需要 Python 3.11+ 和 `uv`。构建与依赖安装可能访问网络；应用部分只使用 FakeLLM
及 sdist 中的三份 SEC 回放夹具。当前本地验收环境是 Windows / Python 3.12.11。

```sh
python scripts/package_install_smoke.py --out runs/package-install-check
```

`uv` 不在 PATH 时传入 `--uv /absolute/path/to/uv`。输出目录必须不存在；失败目录保留，
重试换新目录。脚本不会操作现有 `.venv`、运行库或缓存，也不会自动删除证据。

## 检查内容

- 对照构建前源码清单，逐文件校验 wheel 包内容和 sdist 源码的 SHA-256；检查意外混入的
  运行目录、虚拟环境、Git 元数据和环境配置文件。这个清单检查不是通用秘密扫描器。
- 故意删除 wheel 中的 `runs.js`，确认缺失资源被拒绝，避免只依据构建退出码报告成功。
- 从 `uv.lock` 导出带哈希的 web 运行依赖，以 `--require-hashes` 安装；wheel 使用
  `--no-deps` 安装，随后执行 `uv pip check`。不依赖当前开发环境已有的模块。
- 用新环境的 `python -I` 运行探针，确认模块位于该环境内，已安装包文件与 wheel 清单一致。
  清除继承的 Python 路径、应用配置与常见模型密钥，明确指定新运行库和夹具目录。
- 通过实际回环 HTTP 检查首页、工作台和全部静态文件。静态响应校验原始字节；HTML 路由
  按其文本读取行为比较内容，允许 Windows 换行规范化。这不是浏览器交互或视觉验收。
- 检查发行元数据、Python 包版本和 OpenAPI 版本一致；调用真实安装入口创建并完成任务，
  幂等重试获得相同任务，保存原始导出并校验摘要。
- 结束本次启动的服务进程，再启动同一安装包；原导出字节保持一致，历史列表仍能找到任务。
  Windows 同时结束脚本启动器及其子进程。该实验不替代 Docker 重建或电源故障演练。
- 调用安装后的 `finreceipts audit`，验证明确年度的收入文本取得 SEC 回放证据。
  当前使用 `Apple FY2023 revenue was $383.285 billion.`；HTTP `/api/audit` 同时检查
  该句通过、替换为 `Another Issuer` 后拒绝，并保存 `subject-period-audit.json`。
- 通过实际 HTTP 检查数值分析的字符/数量上限与数值溢出返回 422、没有部分结果；验证长空白
  无数字输入可正常返回，执行名额归零。记录在 `numeric-input-errors.json`，边界见
  [请求限制](REQUEST_LIMITS.md#数值分析的计算边界)。

## 证据与边界

`summary.json` 记录结果、构建前源码哈希及产物哈希；`probe.json` 记录实际导入路径、
Python/包版本、实现编号、任务与导出摘要。`archive-inventory.json` 保存包文件清单。
各构建/安装命令日志、服务日志、CLI 输出、wheel、sdist 和原始导出均留在输出目录。
虚拟环境只供本地复核，CI 上传证据时排除该目录。

wheel 包含应用及静态资源，不包含演示数据和示例报告；本脚本显式从 sdist 提取 SEC 夹具，
不把夹具或源码目录加入导入路径。独立安装使用者仍需配置数据缓存，详见 README。

运行依赖使用锁文件，但构建后端仍遵循 `pyproject.toml` 的版本范围，且构建/安装可使用网络
与本机缓存。这不是无网络安装证明、逐字节可重复构建、所有最低依赖版本或全部可选依赖的认证。
应用设置为 offline 不等于操作系统禁止网络；没有真实模型效果或生产吞吐结论。

2026-10-18 Windows 本地验收 **PASS**，全仓回归 **871 passed / 1 skipped**。
完整本地记录位于 `runs/package-install-20261018/`。早期轮次保留了 HTML 换行
比较，以及不满足现有文本范围规则的 CLI 用例失败；最终使用
`Revenue in 2023 was $383.285 billion.`。当时的 `Apple FY2023 revenue ...` 拒绝行为
在后续独立修复中扩展为受支持的明确语法，见[文本范围边界](ARCHITECTURE.md#text-scope-boundary)。
该修复的新验收位于 `runs/subject-period-20261018/`；没有改写前一轮安装包或失败记录。

## 当前实现的安装后浏览器补验

`19be01f` 的本地 sdist/wheel 在新环境通过 API/CLI、静态资源、数值错误及重启导出校验。
之后使用该环境的 Python 启动真实 Edge 演练：两种终止失败返回 409 且原导出不变、
调整问题后可另建任务、普通中断能恢复完成，桌面和手机宽度均通过。运行实现摘要与
`2101f7d` 的 939 passed / 1 skipped 一致；本轮未重跑未改动的全仓测试。

以下 PowerShell 命令接续上文已经成功构建的输出目录；需另备 Playwright 与 Edge。
`PYTHONSAFEPATH`、禁止用户 site 及清除路径覆盖用于避免源码目录影响导入。

```powershell
$env:FINRECEIPTS_TEST_PYTHON = (Resolve-Path runs/package-install-check/venv/Scripts/python.exe).Path
$env:PYTHONPATH = $null
$env:PYTHONHOME = $null
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONSAFEPATH = '1'
node scripts/browser_permanent_failure.cjs runs/package-install-check/installed-browser
```

本次实际导入路径位于新环境的 `site-packages`，sdist 中 212 个源码文件与构建时干净工作区
逐字节一致。证据位于 `runs/current-package-20261018/build-evidence.json`。
wheel 沿用项目声明版本 `0.1.0`，仅是本地候选产物，没有替换已发布版本或上传包。

Windows/Linux CI job 已配置，只有远程任务实际通过后才能增加相应平台结论。
