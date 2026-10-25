# FinReceipts 财报核验工作台

Vue 3 / TypeScript 控制台。浅色金融蓝设计以任务和财报证据为中心，机器结论、执行状态与人工决定分别呈现。

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

- `src/style.css` 集中定义颜色、字体、间距和交互状态。冷白 `#F5F7FB`、墨蓝 `#172B4D`、钴蓝 `#2457D6` 是基础色；绿色、黄色与红色承担独立状态语义。
- `src/views` 包含登录、任务、配额与团队页面。宽屏为导航、批次列表与任务主区；小屏收起批次列表，平板与手机用纵向任务条目保留三类状态。
- `src/components` 包含统一 SVG 图标、状态标签、原生模态弹层、新建批次和证据审阅抽屉。弹层支持焦点循环、Escape 与关闭后的焦点恢复。
- `src/useWorkspace.ts` 管理页面状态、分页页链刷新、身份与查询版本、SSE 订阅和幂等提交恢复；`src/api.ts` 保留原 `/api/v2` 与 CSRF/Cookie 契约。

未确认的提交继续使用原正文与幂等键。只有服务端明确返回 `422`、确认请求未被接纳时，才解除恢复状态以允许修正表单；网络错误与服务异常保留原提交。JSON 清单也会按现有接口字段约束作本地校验。

## 离线浏览器验收

调用者需提供 Node.js 与 Playwright（可通过 `NODE_PATH` 指向其 `node_modules`，无需给应用增加运行依赖）：

```powershell
node scripts/v2_ui_acceptance.cjs runs/ui-acceptance-new
```

输出目录必须不存在。脚本自启本机静态服务，所有 `/api/v2` 请求由浏览器内存夹具响应，并阻止其他外部地址；不会读取 `.env`、访问 Compose 数据库或调用模型。输出包含页面截图、布局检查、交互断言、颜色对比、原生 200% 缩放及源码/构建文件 SHA-256。

改造前对比可使用 `--baseline --dist=原静态文件目录`。验收截图中的组织、任务和数值均为测试夹具，不能作为真实财务研究或模型效果证据。

前端 CI 会独立构建控制台，并在 Chromium 中运行上述离线验收，保留截图与报告。真实 Compose 集成与模型评测属于独立业务回归，不由此脚本运行；报告将两者单列为 `UNRUN`。

设计参考：[UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)、[frontend-design](https://github.com/anthropics/skills/tree/main/skills/frontend-design)。
