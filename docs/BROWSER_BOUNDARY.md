# 本机服务的浏览器访问边界

FinReceipts 仍是单一信任域的本机研究工具，没有登录或租户隔离。本轮处理一个更具体的
问题：用户打开本地工作台时，其他网站不应借浏览器向它发起会执行模型的请求，或访问任务数据。
只绑定回环地址、没有配置 CORS，并不能替代服务端的请求来源检查。

## 发现与实现

修复前两个负例均返回 200：任意 `Host` 可以读取 `/health`，携带
`Sec-Fetch-Site: cross-site` 的 GET 可以进入 `/api/ask/stream`。后者会执行模型，
即使攻击页面不能通过 JavaScript 读取响应，也可能消耗调用资源。

新增纯 ASGI 中间件，在准入、路由解析和请求体读取之前执行检查，外层诊断仍为拒绝响应分配
`X-Request-ID`。不把原始 Host、Origin、Referer、路径参数或问题文本加入诊断日志。

| 检查 | 行为 |
| --- | --- |
| 所有 HTTP 请求的 Host | 默认只接受 `localhost`、`127.0.0.1`、`[::1]`；错误主机或格式返回 400 `untrusted_host` |
| `/api`、`/api/*` 和 `/health` 的 Fetch Metadata | 拒绝 `cross-site`、`same-site` 和未知值；同站不同端口也不能当成同源 |
| Origin 存在 | 必须与请求协议、主机、端口匹配；拒绝 `null`、路径、多个来源和畸形值 |
| Origin 缺失、Referer 存在 | 检查 Referer 的来源部分；不记录其路径或查询内容 |
| 重复的安全相关请求头 | 拒绝歧义，不能用两个 Host/Origin 的解析差异绕过检查 |
| 其他网站打开首页或工作台链接 | 允许正常页面导航；API 仍独立执行上述检查 |

来源拒绝返回 403 `cross_origin_denied`。应用响应添加 `nosniff`、禁止嵌入框架、
不发送 Referer 和同源资源策略；API 与健康响应另加 `Cache-Control: no-store`。
CLI 启动的 Uvicorn 不信任 `X-Forwarded-*`，避免客户端自行改变来源匹配所用的协议。

配置是**精确主机名**，不支持通配符、URL、端口或后缀规则：

```powershell
$env:FINRECEIPTS_ALLOWED_HOSTS = 'localhost,127.0.0.1,[::1]'
$env:FINRECEIPTS_HOST = '127.0.0.1'
finreceipts-api
```

端口来自实际请求的 Host，来源比较会规范化 HTTP/HTTPS 的默认端口，但不会忽略不同端口。
测试客户端也使用真实的默认允许主机，没有给生产白名单添加 `testserver` 例外。
Compose 原有发布地址已经是 `127.0.0.1`；本轮只补白名单配置传递，未运行 Docker 容器验收。

## 验证记录

2026-10-18，新增 **49 项**测试。首轮 API/SSE/准入/诊断回归 **104 passed**；
最终全仓离线回归 **794 passed / 1 skipped**，跳过仍为 Windows 符号链接权限。
测试既检查响应，也检查运行库、导出、模型调用次数与准入计数没有变化。
提交前的额外负例发现意外 500 响应可能漏加安全头，已将响应头处理移到诊断外层，
并验证该错误路径也具有 `no-store` 和禁止框架嵌入策略。

独立真实 HTTP + Edge 实验包含：

- 相同主机不同端口产生 `same-site`，`localhost` 到 `127.0.0.1` 产生 `cross-site`。
  两种来源分别尝试 GET SSE、GET 导出和 POST 取消，共 **6 次**服务端 403。
- 6 次拒绝前后的执行计数完全相同，已有任务的导出内容未变。
- 同源 POST SSE 正常完成；原生 HTTP 伪造 Host 得到 400，伪造转发协议得到 403。
- 重跑原工作台验收：创建、推进、刷新不重调、修订、对照复核、事件补读、导出和取消通过。
  桌面 1440×1050、手机 390×844，无 JavaScript pageerror，手机无横向溢出。

```powershell
.venv\Scripts\python.exe -m pytest tests\test_browser_boundary.py -q
# Playwright + 本机 Edge；NODE_PATH 可指向已有 Playwright 安装。
node scripts\browser_boundary_smoke.cjs runs\browser-boundary-new
```

浏览器脚本启动自己的回环服务和测试页面，使用新运行库、FakeLLM 与离线 SEC 夹具，
退出时只终止自己启动的服务。原始证据位于 `runs/browser-boundary-20261018/`，
最终结果为 `browser-final/summary.json` 与 `full-final.xml`，通过 `evidence.json` 绑定源码和提交。

保留了前三次浏览器尝试：CORP 隔离了 no-cors 响应，普通响应等待超时；Playwright 普通请求
对象没有提供实际自动附加的 Fetch Metadata；Node fetch 没有按测试预期发送自定义 Host。
最终改用 Chromium 网络额外信息、带请求 ID 的服务端拒绝日志，以及原生 HTTP 发包。
因此没有把“浏览器报错”直接当成“服务端成功拦截”，也没有用未生效的测试输入证明防护。

## 不覆盖的访问控制

这些请求头由浏览器控制，但原生程序可以伪造。为保留 CLI/脚本和健康检查兼容性，完全不带
浏览器来源头的请求仍允许进入。旧浏览器或剥离全部来源头的代理不在本轮浏览器防护保证内。
此机制不是身份认证、CSRF token、租户授权、网络防火墙或 API key；知道 run ID 的本机程序
仍可读取任务。实际 DNS 切换攻击未演练，验证的是拒绝非白名单 Host 的服务端路径。

不要因配置了允许主机就把服务发布到公网。多人共享需要独立的认证、逐任务授权、TLS 和
凭证生命周期设计；反向代理还需明确可信代理及外部来源规则。当前 CLI 不自动接受转发头，
HTTPS 代理前端直接套用本机 HTTP 配置会被来源检查拒绝。不能为“跑通”而加入通配符或绕过校验。

实现参考：[Fetch Metadata](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Fetch_metadata)、
[Sec-Fetch-Site](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Sec-Fetch-Site)、
[同源策略](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy)。
