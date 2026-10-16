# Historical evaluation provenance / 历史评测来源

These are historical records, not current-version benchmark claims.

For the separate 2026-10-16 experiment, see [the workflow comparison](WORKFLOW_EVALUATION.md).
Its sample, model, source constraints and results are independent of the historical numbers below.

## English

Earlier saved experiments dated 2026-10-06–07 used 100 US numeric QA items across 23 companies and a mainland MiniMax Anthropic-compatible endpoint. These predate subsequent continuation and source-release edits. Their provider outputs and result artifacts are **not included in this source-first release**, and no fresh live-model benchmark is claimed here.

The historical receipts-feedback run recorded 96 correct answers out of 100. The reviser was given verifier-retrieved XBRL values: this is **evidence-fed correction, not pure model self-correction**. Flags-only feedback is a distinct condition. Historical small baseline and flags-only runs each had one request error: accuracy used all 100 items, while unsupported-answer fractions used 99 successful responses.

The historical cascade and large-baseline cost estimates of approximately **¥0.69 and ¥0.73 are total CNY costs for their respective 100-item runs**, not mean costs per item. They depend on historical token accounting and assumed prices, not current billing. In those experiments “small” and “large” referred to MiniMax-M3 without and with thinking, rather than independently sized models. These retained provenance notes do not establish current implementation performance or reproduce the omitted experiments.

## 中文

2026-10-06 至 07 的历史保存实验使用了美国数值问答集的 100 题、23 家公司，以及 MiniMax 中国大陆 Anthropic 兼容端点。这些实验早于后续修改与本次源码发布整理。**本源码版本不包含历史服务输出和结果文件，也不宣称重新执行了真实模型评测。**

历史 receipts 反馈运行记录为 100 题中答对 96 题。修订模型看到了核数器检索出的 XBRL 数值，因此是**证据辅助修正，不是纯模型自我修正**。仅 flags 反馈是另一个实验条件。历史 small baseline 与 flags-only 各有一次请求错误；准确率以全部 100 题为分母，含无依据数字的答案占比则以 99 个成功响应为分母。

历史 cascade 与 large baseline 的约 **¥0.69、¥0.73 是各自 100 题运行的人民币估算总成本，不是每题平均成本**。估算取决于当时的 token 记录与假定单价，不代表当前账单。历史实验中的“小／大”分别指 MiniMax-M3 关闭／开启 thinking，并非不同规模的独立模型。保留这些来源说明不等于验证当前实现的性能，也不等于复现了未随附的实验。
