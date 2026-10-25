<script setup lang="ts">
import { computed, ref } from 'vue'
import { metricLabel } from '../format'
import BaseDialog from './BaseDialog.vue'
import UiIcon from './UiIcon.vue'
const props = defineProps<{ busy: boolean; error: string; pending: boolean }>()
const emit = defineEmits<{ close: []; submit: [body: { title: string; items: unknown[] }] }>()
const title = ref('年度财务指标核验'), ticker = ref('AAPL'), year = ref(2023)
const kind = ref('metric'), calculation = ref('direct'), metric = ref('revenue'), denominator = ref('revenue')
const question = ref(''), mode = ref<'form' | 'json'>('form'), json = ref('')
const validation = ref<Record<string, string>>({})
const metrics = ['revenue', 'net income', 'operating income', 'gross profit', 'total assets', 'total liabilities', 'cash and cash equivalents', 'net cash provided by operating activities', 'diluted EPS', 'research and development expense']
const modelNote = computed(() => kind.value === 'research' ? '研究表述会调用模型，费用记入组织用量。' : '确定性取数与计算不消耗模型费用。')
function itemError(item: Record<string, unknown>) {
  if (!['metric', 'research'].includes(String(item.kind)) || typeof item.kind !== 'string') return '任务类型 kind 只能为 metric 或 research。'
  if (typeof item.ticker !== 'string' || !/^[A-Z0-9.\-]{1,16}$/.test(item.ticker)) return '股票代码 ticker 需为 1–16 位大写字母、数字、点或连字符。'
  if (!item.target || typeof item.target !== 'object' || Array.isArray(item.target)) return 'target 需要是核验目标对象。'
  const target = item.target as Record<string, unknown>
  if (typeof target.kind !== 'string' || !['direct', 'ratio', 'yoy_growth'].includes(target.kind)) return 'target.kind 只能为 direct、ratio 或 yoy_growth。'
  if (typeof target.metric !== 'string' || !target.metric.trim() || target.metric.length > 64) return 'target.metric 需要为 1–64 字的指标名称。'
  if (typeof target.fiscal_year !== 'number' || !Number.isInteger(target.fiscal_year) || target.fiscal_year < 1990 || target.fiscal_year > 2100) return 'target.fiscal_year 需为 1990–2100 之间的整数。'
  if (target.denominator != null && (typeof target.denominator !== 'string' || target.denominator.length > 64)) return 'target.denominator 需为不超过 64 字的字符串，或 null。'
  if (item.question != null && (typeof item.question !== 'string' || item.question.length > 12000)) return 'question 需为不超过 12,000 字的字符串，或 null。'
  return ''
}
function submit() {
  if (props.busy) return
  if (props.pending) { emit('submit', { title: '', items: [] }); return }
  const errors: Record<string, string> = {}
  if (!title.value.trim() || title.value.length > 120) errors.title = '请输入 1–120 字的批次名称。'
  let items: unknown[] = []
  if (mode.value === 'json') {
    try {
      const parsed = JSON.parse(json.value)
      if (!Array.isArray(parsed) || parsed.length < 1 || parsed.length > 100) errors.json = '请提供包含 1–100 个任务的 JSON 数组。'
      else if (parsed.some(item => !item || typeof item !== 'object' || Array.isArray(item))) errors.json = '数组中的每一项都需要是任务对象。'
      else {
        for (let index = 0; index < parsed.length; index++) {
          const problem = itemError(parsed[index])
          if (problem) { errors.json = `第 ${index + 1} 项：${problem}`; break }
        }
        if (!errors.json) items = parsed
      }
    } catch { errors.json = 'JSON 格式无效，请检查引号、逗号和方括号。' }
  } else {
    if (!/^[A-Z0-9.\-]{1,16}$/.test(ticker.value.trim().toUpperCase())) errors.ticker = '请输入 1–16 位字母、数字、点或连字符。'
    if (!Number.isInteger(year.value) || year.value < 1990 || year.value > 2100) errors.year = '财年应为 1990–2100 之间的整数。'
    if (question.value.length > 12000) errors.question = '研究问题不能超过 12,000 字。'
    items = [{ kind: kind.value, ticker: ticker.value.trim().toUpperCase(), target: { kind: calculation.value, metric: metric.value, fiscal_year: year.value, denominator: calculation.value === 'ratio' ? denominator.value : null }, question: kind.value === 'research' && question.value.trim() ? question.value.trim() : null }]
  }
  validation.value = errors
  if (!Object.keys(errors).length) emit('submit', { title: title.value.trim(), items })
}
</script>

<template>
  <BaseDialog title="新建核验批次" @close="emit('close')">
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <form novalidate @submit.prevent="submit">
      <template v-if="pending">
        <div class="pending-note"><UiIcon name="lock" :size="24" /><div><h3>原提交尚未确认</h3><p>恢复时会沿用已经保存的任务内容与幂等键，防止重复创建批次。确认前无法修改原提交。</p></div></div>
        <div class="form-footer"><button type="button" class="secondary" @click="emit('close')">稍后处理</button><button class="primary" :disabled="busy">{{ busy ? '正在确认提交…' : '恢复原提交' }}</button></div>
      </template>
      <template v-else>
        <p class="form-intro">选择年度财报指标，提交后跟踪证据、执行与复核结果。</p>
        <fieldset :disabled="busy">
          <label for="batch-title">批次名称<input id="batch-title" v-model="title" maxlength="120" :aria-invalid="!!validation.title" :aria-describedby="validation.title ? 'batch-title-error' : undefined" autocomplete="off"><small v-if="validation.title" id="batch-title-error" class="field-error">{{ validation.title }}</small></label>
          <div class="mode-tabs" role="tablist" aria-label="任务录入方式"><button type="button" role="tab" :aria-selected="mode === 'form'" :class="{ active: mode === 'form' }" @click="mode = 'form'">单项表单</button><button type="button" role="tab" :aria-selected="mode === 'json'" :class="{ active: mode === 'json' }" @click="mode = 'json'">批量 JSON 清单</button></div>
          <div v-if="mode === 'form'" class="form-body">
            <p class="section-label">核验对象</p>
            <div class="field-grid"><label for="batch-ticker">股票代码<input id="batch-ticker" v-model="ticker" maxlength="16" spellcheck="false" :aria-invalid="!!validation.ticker" :aria-describedby="validation.ticker ? 'batch-ticker-error' : undefined"><small v-if="validation.ticker" id="batch-ticker-error" class="field-error">{{ validation.ticker }}</small></label><label for="batch-year">财年<input id="batch-year" v-model.number="year" type="number" min="1990" max="2100" step="1" :aria-invalid="!!validation.year" :aria-describedby="validation.year ? 'batch-year-error' : undefined"><small v-if="validation.year" id="batch-year-error" class="field-error">{{ validation.year }}</small></label></div>
            <label for="batch-metric">指标<select id="batch-metric" v-model="metric"><option v-for="item in metrics" :key="item" :value="item">{{ metricLabel(item) }}</option></select></label>
            <p class="section-label">执行方式</p>
            <div class="field-grid"><label for="batch-kind">任务类型<select id="batch-kind" v-model="kind"><option value="metric">确定性取数 / 计算</option><option value="research">研究表述与核验</option></select></label><label for="batch-calculation">计算类型<select id="batch-calculation" v-model="calculation"><option value="direct">直接指标</option><option value="ratio">比率</option><option value="yoy_growth">同比增长</option></select></label></div>
            <label v-if="calculation === 'ratio'" for="batch-denominator">分母指标<select id="batch-denominator" v-model="denominator"><option v-for="item in metrics" :key="item" :value="item">{{ metricLabel(item) }}</option></select></label>
            <label v-if="kind === 'research'" for="batch-question">研究问题 <span class="optional">可选</span><textarea id="batch-question" v-model="question" rows="3" maxlength="12000" placeholder="例如：解释该年度营收变化，并列出证据。" :aria-invalid="!!validation.question" :aria-describedby="validation.question ? 'batch-question-error' : undefined" /><small v-if="validation.question" id="batch-question-error" class="field-error">{{ validation.question }}</small></label>
            <div class="form-note"><UiIcon :name="kind === 'research' ? 'file' : 'lock'" :size="16" /><span>任务使用冻结的年度证据。{{ modelNote }}</span></div>
          </div>
          <div v-else class="form-body"><label for="batch-json">任务数组 <span class="optional">最多 100 项</span><textarea id="batch-json" v-model="json" class="json-input" rows="10" spellcheck="false" placeholder='[{"kind":"metric","ticker":"AAPL","target":{"kind":"direct","metric":"revenue","fiscal_year":2023}}]' :aria-invalid="!!validation.json" :aria-describedby="validation.json ? 'batch-json-error' : undefined" /><small v-if="validation.json" id="batch-json-error" class="field-error">{{ validation.json }}</small></label><p class="form-note">清单中的研究表述任务会调用模型；确定性计算不消耗模型费用。</p></div>
        </fieldset>
        <div class="form-footer"><button type="button" class="secondary" @click="emit('close')">取消</button><button class="primary" :disabled="busy">{{ busy ? '正在确认提交…' : '提交批次' }}</button></div>
      </template>
    </form>
  </BaseDialog>
</template>

<style scoped>
.form-intro { margin: 0 0 18px; color: var(--muted, #666b66); font-size: 0.9375rem; line-height: 1.8; }
fieldset { margin: 0; border: 0; padding: 0; min-width: 0; }
label { position: relative; display: flex; flex-direction: column; gap: 6px; margin: 0 0 15px; color: var(--ink, #202321); font-size: 0.9375rem; font-weight: 600; }
input, select, textarea { width: 100%; min-width: 0; padding: 8px 10px; min-height: 38px; border: 1px solid var(--line, #e5e7e3); border-radius: 6px; font: inherit; color: var(--ink, #202321); background: var(--surface, #fff); font-weight: 400; line-height: 1.6; }
textarea { resize: vertical; }
input:focus, select:focus, textarea:focus { outline: 2px solid var(--primary, #255c48); outline-offset: 2px; }
[aria-invalid='true'] { border-color: var(--danger, #bd4e4e); }
.field-error { color: var(--danger, #bd4e4e); font-size: 0.875rem; line-height: 1.6; font-weight: 400; }
.form-error { padding: 12px 14px; border: 1px solid #edd6d4; color: var(--danger, #bd4e4e); background: #fcf2f1; border-radius: 6px; font-size: 0.9375rem; line-height: 1.7; overflow-wrap: anywhere; }
.field-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.mode-tabs { display: flex; gap: 20px; margin: 0 0 18px; border-bottom: 1px solid var(--line, #e5e7e3); }
.mode-tabs button { padding: 9px 0; min-height: 38px; margin-bottom: -1px; border: 0; border-bottom: 2px solid transparent; border-radius: 0; color: var(--muted, #666b66); background: transparent; font: inherit; font-size: 0.9375rem; cursor: pointer; }
.mode-tabs .active { color: var(--primary, #255c48); border-bottom-color: var(--primary, #255c48); font-weight: 650; }
.section-label { margin: 18px 0 13px; color: var(--ink, #202321); font-size: 0.9375rem; font-weight: 650; }
.optional { position: absolute; right: 0; color: var(--muted, #666b66); font-size: 0.875rem; font-weight: 400; }
.form-note { display: flex; align-items: flex-start; gap: 9px; padding: 12px 14px; margin: 4px 0 0; border-radius: 6px; background: var(--canvas, #f7f7f5); color: var(--muted, #666b66); font-size: 0.875rem; line-height: 1.8; }
.form-note svg { margin-top: 2px; }
.json-input { font-family: Consolas, monospace; font-size: 0.9375rem; }
.form-footer { display: flex; justify-content: flex-end; align-items: center; gap: 12px; margin-top: 20px; padding-top: 18px; border-top: 1px solid var(--line, #e5e7e3); }
.primary, .secondary { display: inline-flex; justify-content: center; align-items: center; gap: 12px; min-height: 38px; padding: 8px 14px; border: 1px solid var(--line, #e5e7e3); border-radius: 6px; font: inherit; font-size: 0.9375rem; cursor: pointer; }
.primary { background: var(--primary, #255c48); color: #fff; border-color: var(--primary, #255c48); }
.secondary { background: var(--surface, #fff); color: var(--muted, #666b66); }
button:disabled, fieldset:disabled { opacity: .65; cursor: wait; }
.pending-note { display: flex; gap: 12px; padding: 16px; border: 1px solid var(--line, #e5e7e3); border-radius: 6px; background: var(--canvas, #f7f7f5); }
.pending-note svg { margin-top: 3px; color: var(--primary, #255c48); }
.pending-note h3 { margin: 0 0 8px; font-size: 1rem; }
.pending-note p { margin: 0; color: var(--muted, #666b66); font-size: 0.9375rem; line-height: 1.8; }
@media (max-width: 680px) { input, select, textarea, .json-input { font-size: 1rem; min-height: 44px; } .mode-tabs button { min-height: 44px; } .primary, .secondary { min-height: 44px; } }
@media (max-width: 420px) { .field-grid { grid-template-columns: 1fr; gap: 0; } .form-footer button { flex: 1; } }
</style>
