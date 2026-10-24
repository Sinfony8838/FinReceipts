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

const {
  me, view, batches, tasks, quota, selectedBatch, selected, showCreate, error, notice,
  busy, loading, initializing, pending, token, nextBatch, nextTask, updatedAt, canSubmit, queued,
  enter, leave, navigate, selectBatch, more, inspect, closeDetails, create, action, retry,
  download, generateToken, addMember,
} = useWorkspace()
const navigation: { id: View; label: string; description: string; icon: string }[] = [
  { id: 'batches', label: '批次与任务', description: '跟踪研究任务，核对每一份财报证据。', icon: 'batches' },
  { id: 'review', label: '待复核任务', description: '结合原始证据，为具体结果记录人工决定。', icon: 'review' },
  { id: 'quota', label: '用量与配额', description: '查看组织的调用记账、预留占用与执行边界。', icon: 'quota' },
  { id: 'settings', label: '团队与接入', description: '管理组织成员，连接你的批处理工具。', icon: 'settings' },
]
const current = computed(() => navigation.find(item => item.id === view.value)!)
</script>

<template>
  <div v-if="initializing" class="session-loading" role="status"><span class="brand-mark">fr</span><p>正在连接工作台…</p></div>
  <LoginView v-else-if="!me" :busy="busy" :error="error" @login="enter" />
  <div v-else class="workspace">
    <a class="skip-link" href="#main">跳至工作内容</a>
    <aside class="sidebar" aria-label="工作台导航">
      <div class="brand"><span class="brand-mark" aria-hidden="true">fr</span><div><strong>FinReceipts</strong><span>财报研究助手</span></div></div>
      <div class="tenant"><span class="tenant-mark" aria-hidden="true">{{(quota?.name || '组织').slice(0, 1)}}</span><div><b>{{quota?.name || '组织工作空间'}}</b><small>团队工作空间</small></div></div>
      <nav aria-label="主要功能">
        <button v-for="item in navigation" :key="item.id" :class="['nav-button', { active: view === item.id }]"
          :aria-current="view === item.id ? 'page' : undefined" :disabled="busy" @click="navigate(item.id)">
          <UiIcon :name="item.icon" /><span>{{item.label}}</span>
        </button>
      </nav>
      <div class="sidebar-note"><UiIcon name="file" /><p>先证据，后结论。<small>机器核验与人工复核<br>分别记录，完整保留。</small></p></div>
      <div class="identity"><div class="avatar" aria-hidden="true">{{me.username.slice(0,1).toUpperCase()}}</div><div class="identity-copy"><b>{{me.username}}</b><small>{{text(me.role)}}</small></div><button class="icon-button logout-button" :disabled="busy" aria-label="退出登录" title="退出登录" @click="leave"><UiIcon name="logout" /></button></div>
    </aside>
    <main id="main" class="main-content" tabindex="-1">
      <header class="page-header"><div><h1>{{current.label}}</h1><p>{{current.description}}</p></div><button v-if="canSubmit && (view === 'batches' || view === 'review')" class="primary create-button" :disabled="busy" @click="error='';showCreate=true"><UiIcon name="plus" />新建核验批次</button></header>
      <div v-if="error && !showCreate && !selected" class="feedback error" role="alert"><span>{{error}}</span><button class="icon-button" aria-label="关闭提示" @click="error=''"><UiIcon name="close" :size="16" /></button></div>
      <div v-if="notice && !showCreate && !selected" class="feedback success" role="status"><UiIcon name="check" /><span>{{notice}}</span><button class="icon-button" aria-label="关闭提示" @click="notice=''"><UiIcon name="close" :size="16" /></button></div>
      <div v-if="pending" class="pending-notice"><UiIcon name="lock" /><div><b>有一笔提交尚未确认</b><p>恢复会使用原始任务内容和幂等键，避免重复创建。</p></div><button :disabled="busy" @click="create()">{{busy?'正在确认…':'确认原提交'}}</button></div>
      <div v-if="view === 'batches' || view === 'review'" class="workspace-summary" aria-label="组织摘要">
        <div><span>已载入批次</span><b>{{batches.length}}</b></div><div><span>组织待处理</span><b>{{quota ? queued : '—'}}</b></div><div><span>组织已计用量</span><b>{{quota ? money(quota.spent_micros) : '—'}}</b></div><span v-if="updatedAt" class="sync-time">更新于 {{updatedAt}}</span>
      </div>
      <TaskWorkspace v-if="view === 'batches' || view === 'review'" :view="view" :batches="batches" :tasks="tasks" :selected-batch="selectedBatch" :next-batch="nextBatch" :next-task="nextTask" :loading="loading" :busy="busy" @select-batch="selectBatch" @more-batches="more('batches')" @more-tasks="more('tasks')" @inspect="inspect" @download="download" />
      <QuotaView v-else-if="view === 'quota' && quota" :quota="quota" />
      <SettingsView v-else-if="view === 'settings'" :me="me" :busy="busy" :error="error" :token="token" @generate-token="generateToken" @add-member="addMember" />
      <div v-else class="empty loading-placeholder" role="status">正在载入组织信息…</div>
      <footer class="workspace-footer"><span>FinReceipts</span><span>数值核验有边界，研究结论需结合原始材料判断。</span></footer>
    </main>
    <CreateBatchDialog v-if="showCreate" :busy="busy" :error="error" :pending="!!pending" @close="showCreate=false;error=''" @submit="create" />
    <EvidenceDrawer v-if="selected" :task="selected" :me="me" :busy="busy" :error="error" @close="closeDetails();error=''" @action="action" @retry="retry" />
  </div>
</template>
