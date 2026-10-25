<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Row } from '../types'
import { metricLabel } from '../format'
import UiIcon from '../components/UiIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'

const props = defineProps<{ view: 'batches' | 'review'; batches: Row[]; tasks: Row[]; selectedBatch: string; nextBatch: string; nextTask: string; loading: boolean; busy: boolean }>()
const emit = defineEmits<{ selectBatch: [id: string]; moreBatches: []; moreTasks: []; inspect: [id: string]; download: [] }>()
const query = ref('')
const status = ref('all')
const scopeTasks = computed(() => props.view === 'review' ? props.tasks.filter(t => t.status === 'COMPLETED' && t.review_status === 'PENDING') : props.tasks)
const tabs = computed(() => [
  { id: 'all', label: props.view === 'review' ? '待复核' : '全部任务', count: scopeTasks.value.length },
  { id: 'active', label: '进行中', count: scopeTasks.value.filter(t => ['QUEUED', 'READY', 'RUNNING'].includes(t.status)).length },
  { id: 'completed', label: '已完成', count: scopeTasks.value.filter(t => t.status === 'COMPLETED').length },
  { id: 'attention', label: '需关注', count: scopeTasks.value.filter(t => ['FAILED', 'UNKNOWN'].includes(t.status)).length },
])
const visibleTasks = computed(() => scopeTasks.value.filter(task => {
  const matchesStatus = status.value === 'all' || (status.value === 'active' && ['QUEUED', 'READY', 'RUNNING'].includes(task.status)) || (status.value === 'completed' && task.status === 'COMPLETED') || (status.value === 'attention' && ['FAILED', 'UNKNOWN'].includes(task.status))
  const needle = query.value.trim().toLowerCase()
  return matchesStatus && (!needle || [task.ticker, task.metric, metricLabel(task.metric), task.fiscal_year, task.id].some(value => String(value).toLowerCase().includes(needle)))
}))
const chosenBatch = computed(() => props.batches.find(batch => batch.id === props.selectedBatch))
watch(() => [props.view, props.selectedBatch], () => { query.value = ''; status.value = 'all' })
function selectFromEvent(event: Event) { emit('selectBatch', (event.target as HTMLSelectElement).value) }
</script>

<template>
  <section class="task-workspace" aria-label="核验任务工作区" :aria-busy="loading">
    <div class="tasks-heading">
      <div v-if="view === 'batches'" class="task-tabs" role="group" aria-label="筛选已加载任务">
        <button v-for="tab in tabs" :key="tab.id" :class="{ selected: status === tab.id }" :aria-pressed="status === tab.id" @click="status = tab.id">{{tab.label}}<span>{{tab.count}}</span></button>
      </div>
      <div v-else class="review-heading"><UiIcon name="review" :size="16" /><h2>等待人工复核</h2><span>{{scopeTasks.length}}</span></div>
      <button v-if="view === 'batches' && selectedBatch" class="download-button" :disabled="busy" @click="emit('download')"><UiIcon name="download" :size="15" /><span>导出已审核结果</span></button>
    </div>
    <div class="task-toolbar">
      <label class="task-search"><UiIcon name="search" :size="16" /><input v-model="query" type="search" aria-label="搜索已加载任务" placeholder="搜索公司、指标或任务编号" /></label>
      <div v-if="view === 'batches'" class="batch-filter"><label class="visually-hidden" for="batch-picker">研究批次</label><select id="batch-picker" :value="selectedBatch" :disabled="busy" @change="selectFromEvent"><option value="">全部批次</option><option v-for="batch in batches" :key="batch.id" :value="batch.id">{{batch.title}}</option><option v-if="selectedBatch && !chosenBatch" :value="selectedBatch">所选批次 · {{selectedBatch.slice(0,8)}}</option></select><button v-if="nextBatch" class="compact-batch-more" :disabled="busy || loading" @click="emit('moreBatches')">更多批次</button></div>
      <span class="loaded-note">{{loading ? '正在更新…' : '筛选当前已加载任务'}}</span>
    </div>
    <div class="task-table-scroll"><table class="task-table" aria-label="核验任务"><thead><tr><th scope="col">公司 / 任务</th><th scope="col">核验指标</th><th scope="col">财年</th><th scope="col">执行状态</th><th scope="col">机器结论</th><th scope="col">人工复核</th><th scope="col" class="attempt-column">模型尝试</th><th scope="col"><span class="visually-hidden">操作</span></th></tr></thead><tbody><tr v-for="task in visibleTasks" :key="task.id"><td><div class="task-identity"><span class="company-mark" aria-hidden="true">{{String(task.ticker).slice(0,1)}}</span><div><strong>{{task.ticker}}</strong><span class="task-id">{{task.id.slice(0,8)}}</span></div></div></td><td class="task-metric" :title="task.metric">{{metricLabel(task.metric)}}</td><td class="task-year">{{task.fiscal_year}}</td><td><StatusBadge :value="task.status" /></td><td><StatusBadge :value="task.machine_verdict" /></td><td><StatusBadge :value="task.review_status" /></td><td class="attempt-column attempt-count">{{task.dispatches}}<span> 次</span></td><td class="task-action"><button :disabled="busy" @click="emit('inspect',task.id)">查看证据<UiIcon name="chevron" :size="13" /></button></td></tr></tbody></table></div>
    <div class="mobile-tasks"><article v-for="task in visibleTasks" :key="task.id" class="mobile-task"><div class="mobile-task-heading"><div class="task-identity"><span class="company-mark" aria-hidden="true">{{String(task.ticker).slice(0,1)}}</span><div><strong>{{task.ticker}}<span class="mobile-year">FY{{task.fiscal_year}}</span></strong><span class="task-id">{{task.id.slice(0,8)}}</span></div></div><button :disabled="busy" @click="emit('inspect',task.id)">查看证据<UiIcon name="chevron" :size="13" /></button></div><p class="mobile-metric">{{metricLabel(task.metric)}}<span>模型尝试 {{task.dispatches}} 次</span></p><dl class="mobile-statuses"><div><dt>执行状态</dt><dd><StatusBadge :value="task.status" /></dd></div><div><dt>机器结论</dt><dd><StatusBadge :value="task.machine_verdict" /></dd></div><div><dt>人工复核</dt><dd><StatusBadge :value="task.review_status" /></dd></div></dl></article></div>
    <div v-if="!visibleTasks.length" class="tasks-empty" role="status"><UiIcon :name="query || status !== 'all' ? 'search' : view === 'review' ? 'check-circle' : 'receipt'" :size="25" /><h3>{{loading ? '正在读取任务…' : query || status !== 'all' ? '没有匹配的任务' : view === 'review' ? '目前没有待复核任务' : '这里还没有核验任务'}}</h3><p>{{query || status !== 'all' ? '试试其他关键词或状态，筛选仅针对已加载任务。' : view === 'review' ? '执行完成且等待审核的任务会显示在这里。' : '选择研究批次，或新建批次开始核验。'}}</p><button v-if="query || status !== 'all'" @click="query = ''; status = 'all'">清除筛选</button></div>
    <div class="task-pagination"><span>已显示 {{visibleTasks.length}} / {{scopeTasks.length}} 项<span class="pagination-scope"> · 当前已加载</span></span><button v-if="nextTask" :disabled="busy || loading" @click="emit('moreTasks')">{{loading ? '正在加载…' : '加载后续任务'}}<UiIcon name="chevron-down" :size="13" /></button><span v-else class="pagination-end">已加载全部</span></div>
    <p class="register-help">执行状态、机器结论与人工复核分别记录。仅人工接受的结果可导出。</p>
  </section>
</template>

<style scoped>
.task-workspace{min-width:0}.tasks-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:49px;border-bottom:1px solid var(--line)}.task-tabs{display:flex;gap:25px;min-width:0;align-self:stretch}.task-tabs button{position:relative;gap:7px;padding:12px 0;border:0;border-radius:0;background:transparent;color:var(--muted);font-size:0.9375rem;white-space:nowrap}.task-tabs button:hover{background:transparent;color:var(--ink)}.task-tabs button.selected{color:var(--ink);font-weight:600}.task-tabs button.selected:after{position:absolute;content:'';bottom:-1px;left:0;right:0;height:2px;background:var(--primary)}.task-tabs button>span,.review-heading>span{font-size:0.875rem;line-height:18px;min-width:19px;text-align:center;background:#f0f1ee;border-radius:4px;padding:0 5px;color:var(--muted);font-weight:500}.task-tabs button.selected>span{background:var(--primary-soft);color:var(--primary)}.download-button{font-size:0.9375rem;min-height:31px;padding:5px 9px;white-space:nowrap;flex-shrink:0}.review-heading{display:flex;align-items:center;gap:9px;color:var(--muted)}.review-heading h2{font-size:0.9375rem;color:var(--ink);margin:0;font-weight:600}.task-toolbar{display:flex;gap:10px;align-items:center;padding:16px 0}.task-search{display:flex;gap:8px;align-items:center;position:relative;color:var(--muted);width:280px;min-width:0}.task-search>svg{position:absolute;left:10px;pointer-events:none}.task-search input{font-size:0.9375rem;min-height:34px;height:34px;padding:6px 10px 6px 34px;border-color:var(--line);border-radius:5px}.batch-filter{display:flex;gap:8px;min-width:0;max-width:230px}.batch-filter select{font-size:0.9375rem;height:34px;min-height:34px;padding:5px 28px 5px 10px;border-color:var(--line);text-overflow:ellipsis}.compact-batch-more{display:none}.loaded-note{font-size:0.875rem;color:var(--muted);white-space:nowrap;margin-left:auto}.task-table-scroll{width:100%;overflow:auto}.task-table{width:100%;border-collapse:collapse;text-align:left;font-size:0.9375rem;white-space:nowrap}.task-table th{font-size:0.875rem;font-weight:500;color:var(--muted);background:#f7f8f6;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:10px 13px}.task-table th:first-child{border-radius:5px 0 0 5px}.task-table th:last-child{border-radius:0 5px 5px 0}.task-table td{padding:12px 13px;border-bottom:1px solid #eceeea;height:65px;vertical-align:middle}.task-table tbody tr:hover{background:#fafbf9}.task-identity{display:flex;gap:10px;align-items:center;min-width:115px}.company-mark{display:grid;place-items:center;width:30px;height:30px;flex:none;border:1px solid var(--line);background:#f6f7f4;color:#4c554b;border-radius:6px;font-size:0.9375rem;font-weight:650}.task-identity strong{font-size:0.9375rem;font-weight:600;line-height:1.5;display:block}.task-id{display:block;font-family:var(--mono);font-size:0.8125rem;line-height:1.5;color:var(--muted);margin-top:2px}.task-metric{max-width:230px;white-space:normal;overflow-wrap:anywhere;font-size:0.9375rem}.task-year{font-size:0.9375rem;color:var(--muted);font-variant-numeric:tabular-nums}.attempt-count{font-variant-numeric:tabular-nums}.attempt-count>span{font-size:0.875rem;color:var(--muted)}.task-action{text-align:right}.task-action button,.mobile-task-heading button{padding:4px 0;min-height:30px;border:0;background:transparent;font-size:0.9375rem;color:var(--muted);gap:5px;white-space:nowrap}.task-action button:hover,.mobile-task-heading button:hover{color:var(--primary);background:transparent}.task-pagination{display:flex;justify-content:space-between;align-items:center;gap:10px;min-height:55px;color:var(--muted);font-size:0.875rem}.task-pagination button{font-size:0.9375rem;min-height:30px;padding:5px 10px}.pagination-end{font-size:0.875rem}.register-help{margin:8px 0 0;font-size:0.875rem;color:var(--muted);line-height:1.7}.mobile-tasks{display:none}.tasks-empty{text-align:center;color:var(--muted);padding:65px 16px}.tasks-empty>svg{margin-bottom:14px;color:#70796e}.tasks-empty h3{font-size:0.9375rem;color:var(--ink);font-weight:550;margin:0 0 7px}.tasks-empty p{font-size:0.9375rem;margin:0;line-height:1.8}.tasks-empty button{font-size:0.9375rem;min-height:32px;margin-top:14px}
@media(max-width:1279px){.attempt-column{display:none}.task-table th,.task-table td{padding-left:10px;padding-right:10px}.task-tabs{gap:22px}}
@media(max-width:1050px){.company-mark{display:none}.task-identity{min-width:85px}.task-table th,.task-table td{padding-left:6px;padding-right:6px}.loaded-note{display:none}.task-tabs{gap:20px}.compact-batch-more{display:inline-flex;font-size:0.875rem;padding:5px 8px;white-space:nowrap}.batch-filter{max-width:330px}.batch-filter select{min-width:0;width:180px}}
@media(max-width:767px){.tasks-heading{flex-wrap:wrap;gap:0}.task-tabs{gap:22px}.task-tabs button{min-height:44px;font-size:0.9375rem;gap:5px}.download-button{margin:9px 0 10px;min-height:36px}.task-toolbar{flex-wrap:wrap;padding:12px 0;gap:8px}.task-search{width:100%}.task-search input{font-size:1rem;height:40px;min-height:40px}.batch-filter{max-width:100%;width:100%}.batch-filter select{font-size:1rem;height:40px;min-height:40px;flex:1;width:0}.compact-batch-more{display:inline-flex;min-height:40px;font-size:0.9375rem;flex-shrink:0;padding:7px 10px}.task-table-scroll{display:none}.mobile-tasks{display:block;border-top:1px solid var(--line)}.mobile-task{padding:16px 0;border-bottom:1px solid var(--line)}.mobile-task-heading{display:flex;justify-content:space-between;gap:10px;align-items:center}.mobile-task-heading button{min-height:40px;font-size:0.9375rem}.company-mark{display:grid;width:30px;height:32px}.task-identity{min-width:0;gap:8px}.task-identity strong{font-size:0.9375rem}.mobile-year{font-size:0.875rem;color:var(--muted);font-weight:400;margin-left:8px}.mobile-metric{font-size:0.9375rem;margin:10px 0 13px;display:flex;justify-content:space-between;gap:10px}.mobile-metric>span{color:var(--muted);font-size:0.875rem;white-space:nowrap}.mobile-statuses{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin:0}.mobile-statuses dt{font-size:0.8125rem;color:var(--muted);margin-bottom:6px}.mobile-statuses dd{margin:0}.task-pagination button{min-height:40px}.pagination-scope{display:none}.tasks-empty{padding:45px 12px}.register-help{font-size:0.875rem}}
.task-tabs { flex-wrap: wrap; column-gap: clamp(10px, 2vw, 25px); }
.task-search input,.batch-filter select { height: auto; min-height: 38px; line-height: 1.5; }
.mobile-metric { flex-wrap: wrap; }
.mobile-statuses { grid-template-columns: repeat(auto-fit,minmax(135px,1fr)); row-gap: 12px; }
@container workspace (max-width: 54rem) {
  .task-table-scroll { display: none; }
  .mobile-tasks { display: block; border-top: 1px solid var(--line); }
  .mobile-task { padding: 16px 0; border-bottom: 1px solid var(--line); }
  .mobile-task-heading { display: flex; justify-content: space-between; gap: 10px; align-items: center; }
  .mobile-task-heading button { min-height: 40px; }
  .mobile-year { font-size: .8125rem; color: var(--muted); font-weight: 400; margin-left: 8px; }
  .mobile-metric { font-size: .9375rem; margin: 10px 0 13px; display: flex; justify-content: space-between; gap: 10px; }
  .mobile-metric > span { color: var(--muted); font-size: .8125rem; }
  .mobile-statuses { display: grid; gap: 12px; margin: 0; }
  .mobile-statuses dt { font-size: .8125rem; color: var(--muted); margin-bottom: 6px; }
  .mobile-statuses dd { margin: 0; }
}
@media (max-width: 1279px) {
  :global(:root[data-text-size="xlarge"]) .task-table { white-space: normal; }
  :global(:root[data-text-size="xlarge"]) .task-table th { white-space: normal; }
  :global(:root[data-text-size="xlarge"]) .task-action button { white-space: normal; }
}
@media (max-width: 767px) {
  .task-search input,.batch-filter select { min-height: 44px; }
  .task-tabs { column-gap: 16px; row-gap: 0; }
}
</style>
