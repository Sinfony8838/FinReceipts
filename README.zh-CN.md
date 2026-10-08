# FinReceipts

[English](README.md) · Pre-alpha，本地研究软件 · [不构成投资建议](DISCLAIMER.md)

FinReceipts 是金融研究 Agent Harness：将**已提取、属于受支持类型的数值声明**与可用财务证据进行确定性匹配，以核验结果驱动答案修订、模型级联，并生成 HTML「收据」。它不能提取所有声明、核验全部文字，也不能保证整篇答案真实无误。

## 包含什么

- SEC XBRL 事实查询、数字提取与确定性匹配
- LangGraph 作答 → 核验 → 修订／升级模型流程
- 仅 flags 的反馈，以及额外提供检索数值的 receipts 反馈
- HTML 收据、FastAPI 接口与 SSE 事件
- 显式时间戳／目录评测控制，以及合成注入边界测试
- A股 HTTP 适配器及**合成**离线测试输入

支持收据表示在当前匹配规则下找到依据；未找到依据不等于证明数字错误。结果受指标覆盖、实体与期间理解、单位、容差及输入证据影响。

## 本地快速开始

声明支持 Python 3.11 及以上。在源码目录执行：

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev,web]"
FINRECEIPTS_OFFLINE=1 FINRECEIPTS_LLM_PROVIDER=fake pytest -m "not live"
FINRECEIPTS_HOST=127.0.0.1 FINRECEIPTS_LLM_PROVIDER=fake \
  FINRECEIPTS_OFFLINE=1 FINRECEIPTS_CACHE_DIR=tests/fixtures/sec finreceipts-api
# 打开 http://127.0.0.1:8000
```

安装可能访问软件包索引；以上是操作说明，不代表所有平台均完成干净安装验证。随附离线 SEC 缓存仅含 AAPL、NVDA 的 companyfacts，并不覆盖美国评测集中的全部公司。fake 模型仅用于演示，不是模型质量评测。

上述快速开始需要源码检出目录，其中提供 `examples/` 和 `tests/fixtures/sec/`。单独安装的 wheel 不包含示例报告或 SEC 测试缓存。仅使用 wheel 时，需通过 `FINRECEIPTS_CACHE_DIR` 配置有权使用的 SEC 缓存；如需静态示例报告，还需在工作目录自行提供 `examples/`。安装成功不代表已具备完整离线演示资源。

也提供本地 Docker 配置：

```sh
docker compose up --build
```

应用尚未完成生产认证与安全审查。请仅绑定本机回环地址，不要携带付费 API 凭据暴露到公网。验证边界见[可复现性说明](docs/reproducibility.md)，启用真实服务前请读[模型配置说明](docs/llm_access.md)。

## 接口与源码

- CLI：`finreceipts audit`、`eval`、`record`、`record-ashare`、`gen-eval`、`gen-eval-cn`
- HTTP：`GET /health`、`GET /`、`POST /api/ask`、`POST /api/audit`
- SSE：`POST` 或 `GET /api/ask/stream`
- 源码位于 `src/finreceipts/`：`tools/` 负责数据查询与缓存，`verify/`、`xbrl/` 负责核数，`agent/`、`llm/` 负责工作流，`pit/`、`redteam/` 负责可用性策略与合成边界检查，`evals/`、`report/`、`app/` 负责评测与本地展示

## 历史实验来源与限制

2026-10-06 至 07 的历史保存实验使用了美国数值问答集的 100 题、23 家公司，以及 MiniMax 中国大陆 Anthropic 兼容端点。这些实验早于后续修改与本次源码发布整理。**本源码版本不包含历史服务输出和结果文件，也不宣称重新执行了真实模型评测。**

历史 receipts 反馈运行记录为 100 题中答对 96 题。修订模型看到了核数器检索出的 XBRL 数值，因此是**证据辅助修正，不是纯模型自我修正**。仅 flags 反馈是另一个实验条件。历史 small baseline 与 flags-only 各有一次请求错误；准确率以全部 100 题为分母，含无依据数字的答案占比则以 99 个成功响应为分母。

历史 cascade 与 large baseline 的约 **¥0.69、¥0.73 是各自 100 题运行的人民币估算总成本，不是每题平均成本**。估算取决于当时的 token 记录与假定单价，不代表当前账单。历史实验中的“小／大”分别指 MiniMax-M3 关闭／开启 thinking，并非不同规模的独立模型。保留这些来源说明不等于验证当前实现的性能，也不等于复现了未随附的实验。

## 时点与注入边界

旧的日期过滤不证明真实历史公开时间。显式目录／截止时间控制需要独立可信的原始字节、时间戳与事实绑定。随附美国题集不是已验证的真实数据时点评测集。fake 模型合成边界测试不证明真实模型的抗注入能力。详见[评测控制](docs/eval-controls.md)和[时点／边界约定](docs/pit-redteam.md)。

## 数据与许可证

保留三个带来源说明的 SEC JSON fixtures。原东方财富／巨潮资讯抓取快照及其生成的 A股题集不随本版分发；合成 A股输入只验证本地逻辑，不反映公司业绩。实时抓取须另外遵守上游访问与复用条款，见[数据来源](docs/data_sources.md)。

项目代码保留 [Apache-2.0](LICENSE)。依赖与上游数据适用各自条款，依赖中包含 MPL 组件，见[第三方说明](THIRD_PARTY_NOTICES.md)。本版不分发依赖 wheelhouse 或容器镜像。

## 相关工作

数值 grounding 与引用核验已有先例，包括 [FinDVer](https://github.com/yilunzhao/FinDVer)、XBRL Agent、ALCE、Self-RAG、FActScore。本项目探索将同一个确定性数值核验器用于修订、路由与度量，不宣称首创引用或金融核验。
