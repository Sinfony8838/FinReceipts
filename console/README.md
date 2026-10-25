# FinReceipts 财报核验工作台

Vue 3 / TypeScript 控制台。采用紧凑的商用财务工作台布局，以任务和财报证据为中心，机器结论、执行状态与人工决定分别呈现。

## 本机开发

在仓库根目录执行：

```powershell
npm --prefix console ci
npm --prefix console run dev
```

打开终端显示的本机地址。Vite 将 `/api` 代理到现有 Compose 入口 `http://127.0.0.1:8090`，无需在浏览器保存 API Token。启动控制台本身不会启动 Docker 或模型服务。

控制台使用 V2 平台的 `/api/v2` 服务；旧版 Python `/api` 不能替代该接口。服务端与部署说明见 `docs/V2_START.md`。离线浏览器验收使用内存夹具，可独立于服务端运行。

```powershell
npm --prefix console run build
```

此命令先运行 TypeScript 检查，再输出生产静态文件到 `console/dist`。构建不会更新正在运行的 Compose 容器。

## 布局与组件

- `src/style.css` 集中定义颜色、字体、间距和交互状态。白色主区、暖灰 `#F7F7F5` 导航、墨色 `#202321` 文字、深绿 `#255C48` 主操作；状态颜色保持独立语义。
- 宽屏采用导航与任务主区两栏，批次压缩为侧栏列表；表格将公司、指标和财年分列。空间不足时自动改为紧凑任务条目，三类状态均独立显示；字号变大后行高随内容增长。
- 默认正文 15px，辅助文字 13–14px。登录页和工作台右上角的「字号」提供标准、较大、大号三档，覆盖全站及弹层；选择只保存在本浏览器，不随退出登录清除。字号使用相对单位，支持浏览器缩放；本地存储不可用时仍可在当前页面调节。
- 任务搜索覆盖当前已加载任务的公司、指标（含中文名）、财年与编号；状态标签也只筛选已加载任务。页面明确标注范围，不将本地筛选计数当成组织总量。
- `src/views` 包含居中登录表单、任务台账、预算分区与团队设置。预算摘要共用分隔区域，设置采用标签与内容布局，减少卡片嵌套。
- `src/components` 包含统一 SVG 图标、状态标签、原生模态弹层、新建批次和证据审阅抽屉。弹层支持焦点循环、Escape 与关闭后的焦点恢复。
- `src/useWorkspace.ts` 管理页面状态、分页页链刷新、身份与查询版本、SSE 订阅和幂等提交恢复；`src/api.ts` 保留原 `/api/v2` 与 CSRF/Cookie 契约。

未确认的提交继续使用原正文与幂等键。只有服务端明确返回 `422`、确认请求未被接纳时，才解除恢复状态以允许修正表单；网络错误与服务异常保留原提交。JSON 清单也会按现有接口字段约束作本地校验。

## 离线浏览器验收

调用者需提供 Node.js 与 Playwright（可通过 `NODE_PATH` 指向其 `node_modules`，无需给应用增加运行依赖）：

```powershell
node scripts/v2_ui_acceptance.cjs runs/ui-acceptance-new
```

输出目录必须不存在。脚本自启本机静态服务，所有 `/api/v2` 请求由浏览器内存夹具响应，并阻止其他外部地址；不会读取 `.env`、访问 Compose 数据库或调用模型。输出包含页面截图、布局检查、交互断言、搜索与状态筛选、颜色对比、原生 200% 缩放及源码/构建文件 SHA-256。

改造前对比可使用 `--baseline --dist=原静态文件目录`。验收截图中的组织、任务和数值均为测试夹具，不能作为真实财务研究或模型效果证据。

前端 CI 会独立构建控制台，并在 Chromium 中运行上述离线验收，保留截图与报告。真实 Compose 集成与模型评测属于独立业务回归，不由此脚本运行；报告将两者单列为 `UNRUN`。

商用软件布局参考（2026-10-25 查阅）：

- [Mercury Transactions](https://mercury.com/blog/updated-transactions-page)：表格为中心，工具栏集中筛选，减少内容外层容器。
- [Ramp Expense Management](https://ramp.com/expense-management)：费用核对与审批的主次布局、紧凑操作和按需展开的详情。
- [Expensify Spend](https://help.expensify.com/articles/new-expensify/reports-and-expenses/Using-Reports-in-New-Expensify)：类型与状态筛选、可扫描的费用列表。

这些参考用于信息层级和布局；页面保留 FinReceipts 品牌、原有权限及核验业务契约。
