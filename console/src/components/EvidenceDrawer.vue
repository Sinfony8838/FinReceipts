<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Row } from '../types'
import { metricLabel, money, sourceLink } from '../format'
import BaseDialog from './BaseDialog.vue'
import StatusBadge from './StatusBadge.vue'
import UiIcon from './UiIcon.vue'
const props = defineProps<{ task: Row; me: Row; busy: boolean; error: string }>()
const emit = defineEmits<{ close: []; action: [name: string, extra?: Row]; retry: [] }>()
const reason = ref(''), reasonError = ref('')
const request = computed(() => props.task.request || {})
const target = computed(() => request.value.target || {})
const evidence = computed(() => props.task.result?.target_audit?.evidence)
const facts = computed<Row[]>(() => evidence.value?.facts || [])
const attempts = computed<Row[]>(() => props.task.attempts || [])
const reviews = computed<Row[]>(() => props.task.reviews || [])
const canSubmit = computed(() => ['ADMIN', 'ANALYST'].includes(props.me.role))
const canReview = computed(() => ['ADMIN', 'REVIEWER'].includes(props.me.role) && props.task.creator_id !== props.me.id)
const awaitingReview = computed(() => props.task.status === 'COMPLETED' && props.task.review_status === 'PENDING')
const number = (value: unknown) => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 8 }).format(Number(value))
const amount = (value: unknown) => money(Number(value || 0), 4)
const date = (value: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : ''
watch(() => props.task.id, () => { reason.value = ''; reasonError.value = '' })
function review(decision: 'ACCEPTED' | 'REJECTED') {
  if (!reason.value.trim()) { reasonError.value = '请填写复核理由，再记录决定。'; return }
  if (reason.value.length > 2000) { reasonError.value = '复核理由不能超过 2,000 字。'; return }
  reasonError.value = ''
  emit('action', 'review', { decision, reason: reason.value.trim() })
}
</script>

<template>
  <BaseDialog :title="`${request.ticker || task.ticker} · FY${target.fiscal_year || task.fiscal_year}`" drawer @close="emit('close')">
    <template #header><div><p class="receipt-meta">任务 {{ task.id?.slice(0, 8) }}</p><h2 class="receipt-title">{{ request.ticker || task.ticker }} <span>· FY{{ target.fiscal_year || task.fiscal_year }}</span></h2></div></template>
    <p v-if="error" class="drawer-error" role="alert">{{ error }}</p>
    <div class="receipt-statuses"><div><span class="small-label">执行状态</span><StatusBadge :value="task.status" /></div><div><span class="small-label">机器结论</span><StatusBadge :value="task.machine_verdict || 'PENDING'" /></div><div><span class="small-label">人工审核</span><StatusBadge :value="task.review_status || 'PENDING'" /></div></div>
    <div class="receipt-grid">
      <div class="receipt-main">
        <section class="receipt-section answer-section"><div class="section-heading"><h3>核验结果</h3></div><div class="metric-title">{{ metricLabel(target.metric || task.metric || '') }}<span v-if="target.kind && target.kind !== 'direct'">{{ target.kind === 'ratio' ? '比率' : '同比增长' }}</span></div><p v-if="task.error_code" class="drawer-error">{{ task.error_code }}</p><blockquote v-if="task.result">{{ task.result.answer }}</blockquote><div v-else class="evidence-empty"><UiIcon name="clock" :size="24" /><p>任务尚未产生最终结果。</p></div><p class="boundary-note">{{ task.result ? '当前核验覆盖指定数字、单位和期间；研究文字仍需人工判断。' : '执行状态、机器核验与人工审核分别记录。' }}</p></section>
        <section class="receipt-section"><div class="section-heading"><h3>年度财报证据</h3><span class="section-count">{{ facts.length }} 项</span></div><p v-if="evidence?.formula" class="formula"><span>计算方式</span>{{ evidence.formula }}</p><div v-if="facts.length" class="table-scroll"><table class="facts"><thead><tr><th>指标 / 报告期间</th><th>披露数值</th><th>来源</th></tr></thead><tbody><tr v-for="(fact, index) in facts" :key="index"><td><b>{{ fact.label || fact.concept }}</b><small>{{ fact.start ? fact.start + ' → ' : '' }}{{ fact.end }}</small></td><td class="fact-value" :title="String(fact.value)">{{ number(fact.value) }}<small>{{ fact.unit }}</small></td><td><a v-if="sourceLink(fact.source_url)" :href="sourceLink(fact.source_url)" target="_blank" rel="noopener noreferrer">{{ fact.form }} 原始报告<UiIcon name="external" :size="12" /></a><span v-else class="source-missing">来源链接不可用</span><small>披露：{{ fact.filed }}</small></td></tr></tbody></table></div><div v-else class="missing-evidence"><UiIcon name="alert" :size="19" /><p>当前没有足够的目标证据，不能据此认定数字通过核验。</p></div></section>
        <details class="audit-details"><summary><UiIcon name="file" :size="16" /><span>完整核验记录与证据摘要</span><UiIcon name="chevron" :size="14" /></summary><div class="details-body"><p class="small-label">证据摘要</p><p class="digest">{{ task.evidence_digest || '尚未生成' }}</p><pre>{{ JSON.stringify(task.result?.target_audit || task.evidence || {}, null, 2) }}</pre></div></details>
        <details class="audit-details"><summary><UiIcon name="quota" :size="16" /><span>模型调用台账</span><span class="attempt-count">{{ attempts.length }} 次</span><UiIcon name="chevron" :size="14" /></summary><div class="details-body"><div v-for="attempt in attempts" :key="attempt.id" class="attempt"><div><StatusBadge :value="attempt.status" /><small>{{ attempt.id?.slice(0, 8) }}</small></div><dl><div><dt>调用前预留</dt><dd>{{ amount(attempt.reserved_micros) }}</dd></div><div><dt>计费用量</dt><dd :class="{ 'unknown-amount': attempt.actual_micros === null }">{{ attempt.actual_micros === null ? '未知，保留占用' : amount(attempt.actual_micros) }}</dd></div></dl></div><p v-if="!attempts.length" class="boundary-note">此任务没有模型调用。</p><p v-else class="boundary-note">用量为系统的保守估算，不等同于供应商对账单。</p></div></details>
      </div>
      <aside class="review-column">
        <section class="review-panel"><div class="section-heading"><UiIcon name="review" :size="17" /><h3>人工复核</h3></div><form v-if="canReview && awaitingReview" class="review-form" novalidate @submit.prevent="review('ACCEPTED')"><p class="review-intro">核对左侧财报证据，记录对当前结果版本的决定。</p><label for="review-reason">复核理由<textarea id="review-reason" v-model="reason" rows="6" maxlength="2000" placeholder="记录核对的依据与判断。" :disabled="busy" :aria-invalid="!!reasonError" :aria-describedby="reasonError ? 'review-reason-error' : undefined" /></label><p v-if="reasonError" id="review-reason-error" class="field-error" role="alert">{{ reasonError }}</p><button class="review-accept" :disabled="busy"><UiIcon name="check" :size="16" />{{ busy ? '正在记录…' : '接受并纳入交付' }}</button><button type="button" class="review-reject" :disabled="busy" @click="review('REJECTED')">驳回当前结果</button><p class="boundary-note">接受只记录人工决定，原机器核验结论保持不变。</p></form><div v-else class="review-empty"><StatusBadge :value="task.review_status || 'PENDING'" /><p v-if="task.creator_id === me.id && awaitingReview">请由另一位复核人员审核。提交者不能审核自己的任务。</p><p v-else-if="awaitingReview && !canReview">此任务等待管理员或复核员审核。</p><p v-else-if="task.status !== 'COMPLETED'">任务执行完成后，可以进行人工复核。</p><p v-else>此结果的人工决定已独立记录。</p></div></section>
        <section v-if="reviews.length" class="review-history"><h3>复核记录</h3><div v-for="record in reviews" :key="record.created_at" class="review-record"><StatusBadge :value="record.decision" /><p>{{ record.reason }}</p><small>{{ date(record.created_at) }}</small></div></section>
        <section v-if="canSubmit && ['QUEUED', 'READY', 'RUNNING', 'FAILED', 'UNKNOWN'].includes(task.status)" class="task-controls"><h3>任务操作</h3><p v-if="task.status === 'UNKNOWN'" class="unknown-warning">上次模型请求结果不明，可能已经执行并收费。重试会保留未知预算占用，并可能重复调用。</p><div class="control-buttons"><button type="button" :disabled="busy" @click="emit('action', 'cancel')">取消任务</button><button v-if="['FAILED', 'UNKNOWN'].includes(task.status)" type="button" :disabled="busy" @click="emit('retry')"><UiIcon name="refresh" :size="14" />确认后重试</button></div></section>
        <div class="receipt-footnote"><UiIcon name="lock" :size="15" /><p>财报证据与核验结果绑定当前任务版本，人工决定单独保留。</p></div>
      </aside>
    </div>
  </BaseDialog>
</template>

<style scoped>
.receipt-meta { margin: 0 0 7px; color: var(--muted, #657386); font-size: 12px; font-variant-numeric: tabular-nums; }
.receipt-title { margin: 0; color: var(--ink, #1e2b40); font-size: 23px; letter-spacing: -.5px; }
.receipt-title span { color: var(--muted, #657386); font-size: 19px; font-weight: 400; }
.drawer-error { padding: 12px 14px; margin: 0 0 20px; border: 1px solid #edd6d4; color: var(--danger, #bd4e4e); background: #fcf2f1; border-radius: 6px; font-size: 14px; line-height: 1.7; overflow-wrap: anywhere; }
.receipt-statuses { display: grid; grid-template-columns: repeat(3, 1fr); padding: 0 0 24px; margin-bottom: 26px; border-bottom: 1px solid var(--line, #e3e8f0); gap: 20px; }
.receipt-statuses > div { display: flex; flex-direction: column; gap: 8px; }
.small-label { margin: 0; color: var(--muted, #657386); font-size: 12px; font-weight: 500; letter-spacing: .3px; }
.receipt-grid { display: grid; grid-template-columns: minmax(0, 1fr) 274px; gap: 30px; }
.receipt-main, .review-column { min-width: 0; }
.receipt-section { padding-bottom: 25px; margin-bottom: 25px; border-bottom: 1px solid var(--line, #e3e8f0); }
.section-heading { display: flex; align-items: center; gap: 9px; margin-bottom: 19px; }
.section-heading h3 { margin: 0; color: var(--ink, #1e2b40); font-size: 14px; font-weight: 650; }
.section-count { color: var(--muted, #657386); margin-left: auto; font-size: 12px; }
.metric-title { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; color: var(--ink, #1e2b40); font-size: 20px; font-weight: 650; }
.metric-title > span { padding: 3px 7px; color: var(--muted, #657386); background: var(--canvas, #f4f6f9); border-radius: 4px; font-size: 12px; font-weight: 500; }
blockquote { margin: 0; padding: 18px 20px; border-left: 2px solid var(--primary, #325ed1); border-radius: 0 6px 6px 0; color: var(--ink, #1e2b40); background: #f2f5fc; font-size: 15px; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; }
.boundary-note { color: var(--muted, #657386); font-size: 12px; line-height: 1.8; margin: 13px 0 0; }
.formula { display: flex; flex-direction: column; gap: 6px; padding: 12px 15px; margin: 0 0 17px; border: 1px solid var(--line, #e3e8f0); border-radius: 6px; font-family: Consolas, monospace; font-size: 14px; overflow-wrap: anywhere; }
.formula > span { color: var(--muted, #657386); font: 12px 'Segoe UI', 'Microsoft YaHei', sans-serif; }
.table-scroll { overflow-x: auto; }
.facts { width: 100%; border-collapse: collapse; text-align: left; font-size: 14px; }
.facts th { padding: 10px 8px; border-bottom: 1px solid var(--line, #e3e8f0); color: var(--muted, #657386); background: var(--canvas, #f4f6f9); font-size: 12px; font-weight: 500; white-space: nowrap; }
.facts td { padding: 15px 8px; vertical-align: top; border-bottom: 1px solid var(--line, #e3e8f0); }
.facts td:first-child { min-width: 145px; }
.facts b { font-weight: 600; overflow-wrap: anywhere; }
.facts small { display: block; margin-top: 5px; color: var(--muted, #657386); font-size: 12px; white-space: nowrap; }
.fact-value { font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 600; }
.facts a { display: inline-flex; align-items: center; gap: 4px; color: var(--primary, #325ed1); font-size: 14px; text-decoration: none; white-space: nowrap; }
.facts a:hover { text-decoration: underline; }
.source-missing { color: var(--muted, #657386); font-size: 12px; }
.evidence-empty { display: flex; align-items: center; gap: 12px; padding: 22px 18px; background: var(--canvas, #f4f6f9); border-radius: 6px; color: var(--muted, #657386); }
.evidence-empty p { margin: 0; font-size: 14px; }
.missing-evidence { display: flex; align-items: flex-start; gap: 10px; padding: 15px; border-radius: 6px; background: #fbf5e8; color: var(--warning, #9b691b); }
.missing-evidence svg { margin-top: 2px; }
.missing-evidence p { margin: 0; font-size: 14px; line-height: 1.8; }
.audit-details { margin-bottom: 12px; border: 1px solid var(--line, #e3e8f0); border-radius: 6px; }
.audit-details summary { display: flex; align-items: center; gap: 8px; padding: 14px; color: var(--muted, #657386); font-size: 14px; cursor: pointer; list-style: none; }
.audit-details summary::-webkit-details-marker { display: none; }
.audit-details summary > svg:last-child { margin-left: auto; transition: transform .15s; }
.audit-details[open] summary > svg:last-child { transform: rotate(90deg); }
.attempt-count { margin-left: auto; color: var(--muted, #657386); font-size: 12px; }
.audit-details summary .attempt-count + svg { margin-left: 2px; }
.details-body { padding: 3px 14px 15px; }
.digest { margin: 7px 0 13px; color: var(--muted, #657386); font: 12px/1.7 Consolas, monospace; overflow-wrap: anywhere; }
pre { margin: 0; padding: 15px; max-height: 330px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--ink, #1e2b40); background: var(--canvas, #f4f6f9); border: 0; border-radius: 5px; font: 12px/1.8 Consolas, monospace; }
.attempt { padding: 12px 0; border-bottom: 1px solid var(--line, #e3e8f0); }
.attempt > div { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.attempt small { color: var(--muted, #657386); font: 12px Consolas, monospace; }
.attempt dl { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin: 14px 0 0; }
.attempt dt { color: var(--muted, #657386); font-size: 12px; margin-bottom: 4px; }
.attempt dd { margin: 0; color: var(--ink, #1e2b40); font-size: 14px; font-variant-numeric: tabular-nums; }
.attempt dd.unknown-amount { color: var(--warning, #9b691b); }
.review-column { padding-left: 26px; border-left: 1px solid var(--line, #e3e8f0); }
.review-panel .section-heading { color: var(--primary, #325ed1); }
.review-intro, .review-empty p { color: var(--muted, #657386); font-size: 14px; line-height: 1.8; margin: 0 0 18px; }
.review-empty p { margin-top: 14px; }
.review-form label { display: flex; flex-direction: column; gap: 9px; color: var(--ink, #1e2b40); font-size: 14px; font-weight: 600; }
.review-form textarea { width: 100%; padding: 12px; border: 1px solid var(--line, #e3e8f0); border-radius: 6px; color: var(--ink, #1e2b40); background: var(--surface, #fff); font-size: 14px; line-height: 1.8; font-family: inherit; font-weight: 400; resize: vertical; }
.review-form textarea:focus { outline: 2px solid var(--primary, #325ed1); outline-offset: 2px; }
.review-form textarea[aria-invalid='true'] { border-color: var(--danger, #bd4e4e); }
.field-error { margin: 7px 0; color: var(--danger, #bd4e4e); font-size: 12px; line-height: 1.7; }
.review-accept, .review-reject { display: flex; justify-content: center; align-items: center; gap: 8px; width: 100%; padding: 11px; margin-top: 12px; border: 1px solid var(--line, #e3e8f0); border-radius: 6px; font: inherit; font-size: 14px; cursor: pointer; }
.review-accept { background: var(--primary, #325ed1); color: #fff; border-color: var(--primary, #325ed1); }
.review-reject { background: var(--surface, #fff); color: var(--danger, #bd4e4e); }
.review-history, .task-controls { border-top: 1px solid var(--line, #e3e8f0); padding-top: 22px; margin-top: 25px; }
.review-history h3, .task-controls h3 { margin: 0 0 16px; color: var(--ink, #1e2b40); font-size: 14px; font-weight: 650; }
.review-record { margin-bottom: 16px; }
.review-record p { margin: 10px 0 6px; color: var(--ink, #1e2b40); font-size: 14px; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; }
.review-record small { color: var(--muted, #657386); font-size: 12px; }
.unknown-warning { color: var(--warning, #9b691b); background: #fbf5e8; padding: 12px; border-radius: 6px; font-size: 14px; line-height: 1.8; }
.control-buttons { display: flex; gap: 8px; flex-wrap: wrap; }
.control-buttons button { display: flex; align-items: center; gap: 5px; min-height: 40px; padding: 8px 10px; border: 1px solid var(--line, #e3e8f0); background: var(--surface, #fff); color: var(--muted, #657386); border-radius: 5px; font: inherit; font-size: 14px; cursor: pointer; }
button:disabled { opacity: .6; cursor: wait; }
.receipt-footnote { display: flex; gap: 8px; color: var(--muted, #657386); margin-top: 30px; }
.receipt-footnote svg { margin-top: 3px; }
.receipt-footnote p { margin: 0; font-size: 12px; line-height: 1.8; }
@media (max-width: 950px) { .receipt-grid { grid-template-columns: 1fr; } .review-column { padding: 25px 0 0; border-left: 0; border-top: 1px solid var(--line, #e3e8f0); } .review-form textarea { min-height: 110px; } }
@media (max-width: 680px) { .review-form textarea { font-size: 16px; } .review-accept, .review-reject, .control-buttons button { min-height: 44px; } }
@media (max-width: 460px) { .receipt-statuses { gap: 8px; } .small-label { font-size: 12px; } .receipt-statuses :deep(.status-badge) { font-size: 12px; padding: 5px 6px; gap: 4px; } .metric-title { font-size: 18px; } .facts td:first-child { min-width: 135px; } }
@media (max-width: 680px) {
  .table-scroll { overflow: visible; }
  .facts, .facts tbody, .facts tr, .facts td { display: block; width: 100%; min-width: 0; }
  .facts thead { display: none; }
  .facts tr { padding: 18px 0; border-bottom: 1px solid var(--line); }
  .facts tr:first-child { border-top: 1px solid var(--line); }
  .facts td, .facts td:first-child { padding: 6px 0; border: 0; min-width: 0; }
  .facts small { white-space: normal; overflow-wrap: anywhere; }
  .fact-value { white-space: normal; overflow-wrap: anywhere; font-size: 15px; }
  .fact-value::before, .facts td:last-child::before { display: block; margin-bottom: 4px; color: var(--muted); font-size: 12px; font-weight: 400; }
  .fact-value::before { content: '披露数值'; }
  .facts td:last-child::before { content: '原始来源'; }
  .facts a { min-height: 44px; padding: 8px 0; }
}
@media (prefers-reduced-motion: reduce) { .audit-details summary > svg:last-child { transition: none; } }
</style>
