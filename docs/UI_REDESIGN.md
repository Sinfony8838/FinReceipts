# 界面改版与验收记录 · 2026-10-14

本轮在 `codex/evidence-runtime` 分支，从 `d0ebcf3fb59ee4d4abaa319130b4db2fe0e3d313`
继续改造展示层。API、证据、预算、幂等、恢复及 SSE 游标协议保持不变。
UI 独立保存为本地提交，本轮没有推送或合并原 PR。

## 发现的问题与改进

| 原有问题 | 改进 | 目的 |
|---|---|---|
| 深蓝薄荷配色、口号大标题、全大写英文眉题抢占首屏 | 白灰底、中性深色文字、少量蓝色，精简品牌导航 | 让研究结果成为视觉中心 |
| 两页样式与语言割裂 | 共享 CSS、主题与展示脚本，统一中文界面 | 跨页保持一致的操作习惯 |
| 高级选项和恢复确认一直展开 | 额度与修订上限折叠；恢复确认仅在可恢复状态出现 | 减少无关选项 |
| 完成状态容易被理解为全部正确 | 完成标记使用中性色，数字匹配和语句核验分别展示 | 准确表达核验边界 |
| 复核结果混在技术文本中 | 声明、数字匹配、语句核验、差异依据组成表格 | 便于比较和现场解释 |
| 原始 JSON、摘要占据主要区域 | 中文时间线，原始记录与完整摘要可展开，时间线限高滚动 | 保留证据，同时改善阅读 |
| 手机恢复任务后先看到大量设置 | 小于 960px 时结果优先，设置折叠为公司、财年和问题摘要 | 优先呈现当前任务 |
| 同页改变任务 hash 可能继续显示旧任务 | 监听任务地址变化并重读；跳过导航只移动焦点 | 地址、结果与恢复对象保持一致 |

设计参考：[Linear 布局与层级](https://linear.app/now/behind-the-latest-design-refresh)、
[Geist 颜色体系](https://vercel.com/geist/colors)。使用系统中文字体、细边框、6–8px 圆角、
14px 正文、22–24px 标题、36–40px 常规控件，不引入框架、在线字体或外部 CDN。

## 实现取舍与过程

1. 先保存两页改版前截图，再抽取 `ui.css`、`ui.js` 和页面脚本，保留原有元素 ID。
2. 主题脚本在样式表之前执行，默认浅色。选择在刷新与跨页后保留；跟随系统时响应系统变化；
   浏览器存储失败时仍可使用。深色采用中性黑灰，生成报告保持白色文档区域。
3. 只在展示层翻译状态、节点和错误摘要。模型回答、证据和原始记录保持原文。
   对照表按 claim span 对齐差异，没有差异的条目沿用两种核验共同的 verdict。
4. 原有请求继续使用幂等键、expected_revision、恢复确认和 Last-Event-ID。
   浏览器创建、修订和复核均实际调用本地接口。
5. 验收发现同页切换任务 hash 不刷新结果，补上处理。快捷跳转另行处理焦点，
   防止将任务编号覆盖成 `#main`。高级设置中的无效输入自动展开，避免无法定位。
6. 响应式测试最初在媒体事件尚未完成时立即断言，修正为等待实际展开状态，
   避免一帧重排造成误判。
7. 原生 200% 缩放使用隔离浏览器配置。初次将偏好写成数值没有生效，
   改为分区字典后，实际外窗 1440px、CSS 视口 703px、设备像素比 2、浏览器报告 zoom=2。
   格式依据 [Chromium 的缩放偏好实现](https://chromium.googlesource.com/chromium/src/+/caa5e0d1c9d58de7bbcf686857034253d0a26ac8/chrome/browser/ui/zoom/chrome_zoom_level_prefs.cc)。
8. Playwright 全页截图在原生缩放下会按 CSS 像素截窄，改用 CDP 内容尺寸截取完整文档，
   并重新查看画面，区分工具裁切与实际布局溢出。

## 本轮验收

| 检查 | 结果 |
|---|---|
| 布局 | **64 组通过**：45 组页面/主题/屏宽、12 组状态夹具/明暗/宽度、1 组手机报告、6 组原生缩放 |
| 屏宽 | 1440、1280、900、640、390px，无页面横向溢出或表单控件横向裁切 |
| 主题 | 默认浅色、明暗选择、刷新与跨页持久化、系统实时切换、存储不可用 |
| 真实接口 | 创建、推进、修订、刷新不重复调用、13 条事件补读、导出、对照复核、取消、额度耗尽 |
| 问答 | Apple 流式问答、NVIDIA 一次性返回，原始报告保留 |
| 状态来源 | 空态、完成、取消、额度耗尽来自真实接口；运行中、中断、租约过期和恢复交互来自明确标注的浏览器路由夹具 |
| 键盘 | 快捷跳转保留任务编号，可见焦点、Enter 展开、隐藏无效输入可定位 |
| 对比度 | 两种主题各 8 组正文/次要文字/链接/状态/按钮颜色对达到 4.5:1；不等于完整无障碍认证 |
| 浏览器错误 | 最终两套脚本 pageerror 均为 0 |
| 相关后端回归 | **42 passed**：runtime、run API、原 ask/SSE API、报告 |
| 静态检查 | 修改的 Python 测试 Ruff、git diff --check 通过 |

浏览器为本机 Edge，接口使用离线 SEC 数据与 fake 模型。UI 状态夹具不修改真实数据库，
不作为真实故障恢复证明；真实恢复、预算、并发和完整性由本轮 runtime 回归测试覆盖。
这些结果不代表真实模型能力、跨浏览器全面兼容或生产可用性。

复现（按 [RUNTIME.md](RUNTIME.md) 启动离线服务，并设置端口为 8791；Playwright 由测试环境提供）：

```powershell
node scripts/browser_runtime_smoke.cjs runs/ui-check-runtime-new
node scripts/browser_ui_acceptance.cjs runs/ui-check-matrix-new
.venv/Scripts/python.exe -m pytest tests/test_runtime.py tests/test_run_api.py tests/test_api.py tests/test_report.py -q
```

输出目录须为新目录，避免覆盖证据。本次最终记录在：
`runs/ui-20261014/runtime-v2/`、`runs/ui-20261014/matrix-v5/`、
`runs/ui-20261014/regression.xml`。失败和中间尝试保留在其他版本目录，不能作为最终通过证据。
可提交汇总见 [acceptance.json](assets/ui-20261014/acceptance.json)。

## 前后截图

改版前：[工作台](assets/ui-20261014/before-workbench.png)、[问答](assets/ui-20261014/before-ask.png)。

改版后浅色工作台：

![浅色工作台](assets/ui-20261014/workbench-light.png)

其他画面：[问答页](assets/ui-20261014/ask-light.png)、
[手机深色结果](assets/ui-20261014/workbench-mobile-dark.png)、
[原生 200% 缩放](assets/ui-20261014/workbench-zoom200.png)。
