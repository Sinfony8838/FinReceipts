<script setup lang="ts">
import { computed } from 'vue'
import { useWorkspace } from './useWorkspace'
import { money, text } from './format'
import type { View } from './types'
import UiIcon from './components/UiIcon.vue'
import CreateBatchDialog from './components/CreateBatchDialog.vue'
import EvidenceDrawer from './components/EvidenceDrawer.vue'
import LoginView from './views/LoginView.vue'
import TaskWorkspace from './views/TaskWorkspace.vue'
import QuotaView from './views/QuotaView.vue'
import SettingsView from './views/SettingsView.vue'
import TextSizeControl from './components/TextSizeControl.vue'

const {
  me, view, batches, tasks, quota, selectedBatch, selected, showCreate, error, notice,
  busy, loading, initializing, pending, token, nextBatch, nextTask, updatedAt, canSubmit, queued,
  enter, leave, navigate, selectBatch, more, inspect, closeDetails, create, action, retry,
  download, generateToken, addMember,
} = useWorkspace()
const navigation: { id: View; label: string; icon: string }[] = [
  { id: 'batches', label: '批次与任务', icon: 'batches' },
  { id: 'review', label: '待复核任务', icon: 'review' },
  { id: 'quota', label: '用量与配额', icon: 'quota' },
  { id: 'settings', label: '团队与接入', icon: 'settings' },
]
const current = computed(() => navigation.find(item => item.id === view.value)!)
function batchDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '日期未知' : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}
</script>

<template>
  <div v-if="initializing" class="session-loading" role="status"><span class="brand-mark">fr</span><p>正在连接工作台…</p></div>
  <LoginView v-else-if="!me" :busy="busy" :error="error" @login="enter" />
  <div v-else class="workspace">
    <a class="skip-link" href="#main">跳至工作内容</a>
    <aside class="sidebar" aria-label="工作台导航">
      <div class="brand"><span class="brand-mark" aria-hidden="true"><UiIcon name="receipt" :size="19" /></span><strong>FinReceipts</strong></div>
      <div class="tenant"><span class="tenant-mark" aria-hidden="true">{{(quota?.name || '组织').slice(0, 1)}}</span><div><b>{{quota?.name || '组织工作空间'}}</b><small>团队工作空间</small></div></div>
      <nav aria-label="主要功能">
        <button v-for="item in navigation" :key="item.id" :class="['nav-button', { active: view === item.id }]"
          :aria-label="item.label" :title="item.label" :aria-current="view === item.id ? 'page' : undefined" :disabled="busy" @click="navigate(item.id)">
          <UiIcon :name="item.icon" /><span>{{item.label}}</span>
        </button>
      </nav>
      <section v-if="view === 'batches' || view === 'review'" class="sidebar-batches" aria-labelledby="batch-list-heading">
        <div class="batch-list-heading"><h2 id="batch-list-heading">研究批次</h2><span>{{batches.length}}</span></div>
        <div class="batch-items"><button v-for="batch in batches" :key="batch.id" :class="['batch-card', { chosen: view === 'batches' && selectedBatch === batch.id }]" :aria-pressed="view === 'batches' && selectedBatch === batch.id" :title="`${batch.title} · ${batch.id.slice(0,8)}`" :disabled="busy" @click="selectBatch(batch.id)"><UiIcon name="file" :size="15" /><span class="batch-copy"><strong>{{batch.title}}</strong><small>{{batchDate(batch.created_at)}}<span>·</span>{{batch.item_count}} 项任务</small><span class="visually-hidden">{{batch.id.slice(0,8)}}</span></span></button></div>
        <p v-if="!batches.length && !loading" class="batch-empty">尚无研究批次</p>
        <button v-if="nextBatch" class="batch-more" :disabled="busy || loading" @click="more('batches')">{{loading ? '正在加载…' : '加载更多批次'}}<UiIcon name="chevron-down" :size="13" /></button>
      </section>
      <div class="identity"><div class="avatar" aria-hidden="true">{{me.username.slice(0,1).toUpperCase()}}</div><div class="identity-copy"><b>{{me.username}}</b><small>{{text(me.role)}}</small></div><button class="icon-button logout-button" :disabled="busy" aria-label="退出登录" title="退出登录" @click="leave"><UiIcon name="logout" /></button></div>
    </aside>
    <main id="main" class="main-content" tabindex="-1">
      <div class="workspace-topbar"><span>工作空间<span class="breadcrumb-divider">/</span>{{current.label}}</span><span v-if="updatedAt" class="sync-time"><i aria-hidden="true"></i>更新于 {{updatedAt}}</span></div>
      <div class="page-body">
      <header class="page-header"><div><h1>{{current.label}}</h1><p v-if="view === 'batches' || view === 'review'" class="workspace-summary" aria-label="组织摘要"><span>已载入 <b>{{batches.length}}</b> 个批次</span><span>组织待处理 <b>{{quota ? queued : '—'}}</b></span><span>已计用量 <b>{{quota ? money(quota.spent_micros) : '—'}}</b></span></p><p v-else>{{view === 'quota' ? '组织预算、调用用量与资源上限' : '组织成员与批处理 API 接入'}}</p></div><div class="page-controls"><TextSizeControl /><button v-if="canSubmit && (view === 'batches' || view === 'review')" class="primary create-button" :disabled="busy" @click="error='';showCreate=true"><UiIcon name="plus" :size="16" />新建核验批次</button></div></header>
      <div v-if="error && !showCreate && !selected" class="feedback error" role="alert"><span>{{error}}</span><button class="icon-button" aria-label="关闭提示" @click="error=''"><UiIcon name="close" :size="16" /></button></div>
      <div v-if="notice && !showCreate && !selected" class="feedback success" role="status"><UiIcon name="check" /><span>{{notice}}</span><button class="icon-button" aria-label="关闭提示" @click="notice=''"><UiIcon name="close" :size="16" /></button></div>
      <div v-if="pending" class="pending-notice"><UiIcon name="lock" /><div><b>有一笔提交尚未确认</b><p>恢复会使用原始任务内容和幂等键，避免重复创建。</p></div><button :disabled="busy" @click="create()">{{busy?'正在确认…':'确认原提交'}}</button></div>
      <TaskWorkspace v-if="view === 'batches' || view === 'review'" :view="view" :batches="batches" :tasks="tasks" :selected-batch="selectedBatch" :next-batch="nextBatch" :next-task="nextTask" :loading="loading" :busy="busy" @select-batch="selectBatch" @more-batches="more('batches')" @more-tasks="more('tasks')" @inspect="inspect" @download="download" />
      <QuotaView v-else-if="view === 'quota' && quota" :quota="quota" />
      <SettingsView v-else-if="view === 'settings'" :me="me" :busy="busy" :error="error" :token="token" @generate-token="generateToken" @add-member="addMember" />
      <div v-else class="empty loading-placeholder" role="status">正在载入组织信息…</div>
      </div>
    </main>
    <CreateBatchDialog v-if="showCreate" :busy="busy" :error="error" :pending="!!pending" @close="showCreate=false;error=''" @submit="create" />
    <EvidenceDrawer v-if="selected" :task="selected" :me="me" :busy="busy" :error="error" @close="closeDetails();error=''" @action="action" @retry="retry" />
  </div>
</template>
