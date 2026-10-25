<script setup lang="ts">
import {computed} from 'vue'
import type {Row} from '../types'
import {money,text} from '../format'
import UiIcon from '../components/UiIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
const props=defineProps<{quota:Row}>()
const spent=computed(()=>Number(props.quota.spent_micros)||0)
const reserved=computed(()=>Number(props.quota.reserved_micros)||0)
const budget=computed(()=>Number(props.quota.budget_micros)||0)
const spentWidth=computed(()=>budget.value>0?Math.min(100,spent.value/budget.value*100):0)
const reservedWidth=computed(()=>budget.value>0?Math.min(100-spentWidth.value,reserved.value/budget.value*100):0)
const available=computed(()=>Math.max(0,budget.value-spent.value-reserved.value))
const states=computed<Row[]>(()=>Array.isArray(props.quota.states)?props.quota.states:[])
const unknown=computed(()=>states.value.find(state=>state.status==='UNKNOWN')?.count||0)
</script>

<template>
  <div class="quota-view">
    <div class="quota-summary" aria-label="组织预算概览">
      <section class="quota-number"><span>已计用量</span><strong>{{money(quota.spent_micros)}}</strong><p>按已记录 token 的保守估算</p></section>
      <section class="quota-number reserved-number"><span>预留与未知占用</span><strong>{{money(quota.reserved_micros)}}</strong><p>结果未确认的调用继续保留占用</p></section>
      <section class="quota-number"><span>组织预算上限</span><strong>{{money(quota.budget_micros,2)}}</strong><p>当前工作空间的模型调用额度</p></section>
    </div>
    <section class="budget-panel" aria-labelledby="budget-heading">
      <div class="section-heading"><h2 id="budget-heading">预算使用情况</h2><span class="available-amount">可用额度 <strong>{{money(available)}}</strong></span></div>
      <div class="budget-track" role="img" :aria-label="`预算上限 ${money(budget)}，已计用量 ${money(spent)}，预留与未知占用 ${money(reserved)}，可用额度 ${money(available)}`"><span class="spent-segment" :style="{width:spentWidth+'%'}"></span><span class="reserved-segment" :style="{width:reservedWidth+'%'}"></span></div>
      <div class="budget-legend"><span><i class="spent-dot" aria-hidden="true"></i>已计用量</span><span><i class="reserved-dot" aria-hidden="true"></i>预留与未知占用</span><span><i class="available-dot" aria-hidden="true"></i>可用额度</span></div>
      <p class="budget-note">调用前预留，确认后结算。系统记账与供应商最终账单可能存在差异。</p>
    </section>
    <div class="quota-details">
      <section class="resource-panel" aria-labelledby="resource-heading"><div class="section-heading"><h2 id="resource-heading">资源边界</h2></div><dl class="resource-list"><div><dt>待处理任务上限</dt><dd>{{quota.max_pending}}<span>项</span></dd></div><div><dt>组织同时执行上限</dt><dd>{{quota.max_running}}<span>项</span></dd></div></dl><div class="unknown-note"><UiIcon name="alert" :size="16"/><p><strong>{{unknown?'有 '+unknown+' 项调用结果未知':'关于未知调用'}}</strong><span>结果不明时保留预算占用。重试需明确确认，可能再次产生调用与费用。</span></p></div></section>
      <section class="state-panel" aria-labelledby="states-heading"><div class="section-heading"><h2 id="states-heading">任务状态分布</h2></div><div v-for="state in states" :key="state.status" class="quota-state-row"><StatusBadge :value="state.status"/><span :aria-label="`${text(state.status)} ${state.count} 项`">{{state.count}}<small> 项</small></span></div><p v-if="!states.length" class="no-states">当前组织还没有任务记录。</p></section>
    </div>
  </div>
</template>

<style scoped>
.quota-view{max-width:1120px}.quota-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));padding:20px 0 24px;margin-bottom:28px;border-bottom:1px solid var(--line)}.quota-number{min-width:0;padding:0 28px;border-left:1px solid var(--line)}.quota-number:first-child{padding-left:0;border-left:0}.quota-number>span{font-size:0.875rem;color:var(--muted);line-height:1.6}.quota-number>strong{display:block;font-size:1.75rem;line-height:1.4;font-weight:500;letter-spacing:-.6px;font-variant-numeric:tabular-nums;margin:8px 0 6px;overflow-wrap:anywhere}.quota-number>p{font-size:0.875rem;line-height:1.7;color:var(--muted);margin:0}.reserved-number>strong{color:var(--warning)}.budget-panel{padding-bottom:28px;margin-bottom:28px;border-bottom:1px solid var(--line)}.section-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:18px}.section-heading h2{font-size:0.9375rem;font-weight:600;line-height:1.5;margin:0}.available-amount{font-size:0.875rem;color:var(--muted);font-variant-numeric:tabular-nums;text-align:right}.available-amount strong{font-size:0.9375rem;display:inline-block;margin-left:8px;font-weight:500;color:var(--ink)}.budget-track{height:8px;border-radius:3px;background:#eef0eb;display:flex;overflow:hidden}.budget-track>span{display:block;height:100%;flex-shrink:0}.spent-segment,.spent-dot{background:var(--primary)}.reserved-segment,.reserved-dot{background:#bf9250}.available-dot{background:#dfe3db}.budget-legend{display:flex;flex-wrap:wrap;gap:12px 24px;margin-top:12px;font-size:0.875rem;color:var(--muted)}.budget-legend>span{display:flex;align-items:center;gap:6px}.budget-legend i{width:7px;height:7px;border-radius:2px;display:block}.budget-note{font-size:0.875rem;line-height:1.8;color:var(--muted);margin:16px 0 0}.quota-details{display:grid;grid-template-columns:1fr 1fr;gap:48px;align-items:start}.resource-panel,.state-panel{min-width:0}.resource-list{padding:0;margin:0}.resource-list>div{display:flex;align-items:center;justify-content:space-between;gap:15px;padding:12px 0;border-top:1px solid var(--line)}.resource-list dt{font-size:0.9375rem;line-height:1.7;color:var(--muted)}.resource-list dd{margin:0;font-size:1.0625rem;font-weight:500;line-height:1.5;font-variant-numeric:tabular-nums}.resource-list dd>span{font-size:0.875rem;color:var(--muted);font-weight:400;margin-left:7px}.unknown-note{display:flex;align-items:flex-start;gap:9px;padding-top:18px;margin-top:8px;border-top:1px solid var(--line);color:var(--warning)}.unknown-note>svg{flex-shrink:0;margin-top:2px}.unknown-note p{font-size:0.875rem;line-height:1.8;margin:0}.unknown-note strong{display:block;font-weight:500;margin-bottom:4px}.unknown-note p>span{display:block;color:var(--muted)}.quota-state-row{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:10px 0;border-top:1px solid var(--line)}.quota-state-row>span{font-size:0.9375rem;font-variant-numeric:tabular-nums;font-weight:500}.quota-state-row small{font-size:0.875rem;color:var(--muted);font-weight:400;margin-left:3px}.no-states{font-size:0.9375rem;color:var(--muted);margin:0;padding:12px 0}
@media(max-width:1050px){.quota-number{padding:0 18px}.quota-number>strong{font-size:1.5625rem}.quota-details{gap:28px}}
@media(max-width:767px){.quota-summary{grid-template-columns:minmax(0,1fr);padding:0;margin-bottom:24px}.quota-number,.quota-number:first-child{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 14px;border-left:0;border-top:1px solid var(--line);padding:15px 0}.quota-number:first-child{border-top:0}.quota-number>strong{grid-column:2;grid-row:1/3;margin:0;font-size:1.5rem;align-self:center}.quota-number>p{max-width:210px}.quota-details{grid-template-columns:minmax(0,1fr);gap:28px}.budget-panel{margin-bottom:24px;padding-bottom:24px}.section-heading{gap:12px;margin-bottom:16px}.budget-legend{gap:10px 16px}.available-amount strong{margin-left:5px}}
</style>
