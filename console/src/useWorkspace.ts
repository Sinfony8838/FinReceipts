import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError, login, logout, refreshCsrf } from './api'
import type { BatchBody, PendingSubmission, Row, View } from './types'

export function useWorkspace() {
  const me = ref<Row | null>(null), view = ref<View>('batches')
  const batches = ref<Row[]>([]), tasks = ref<Row[]>([]), quota = ref<Row | null>(null)
  const selectedBatch = ref(''), selected = ref<Row | null>(null), showCreate = ref(false)
  const error = ref(''), notice = ref(''), busy = ref(false), loading = ref(false), initializing = ref(true)
  const pending = ref<PendingSubmission | null>(null), token = ref('')
  const nextBatch = ref(''), nextTask = ref(''), updatedAt = ref('')
  const canSubmit = computed(() => ['ADMIN', 'ANALYST'].includes(me.value?.role))
  const queued = computed(() => quota.value?.states?.filter((s: Row) => ['QUEUED', 'READY', 'RUNNING'].includes(s.status))
    .reduce((sum: number, state: Row) => sum + state.count, 0) || 0)
  let epoch = 0, queryVersion = 0, listRequest = 0, detailRequest = 0, batchPages = 1, taskPages = 1
  let events: EventSource | null = null, timer: ReturnType<typeof setInterval> | undefined, polling = false
  const storageKey = () => `fr:v2:pending:${me.value?.tenant_id}:${me.value?.id}`
  const message = (e: unknown) => e instanceof Error ? e.message : String(e)

  function closeDetails() { detailRequest++; events?.close(); events = null; selected.value = null }
  function reset() {
    queryVersion++; listRequest++; closeDetails()
    batches.value = []; tasks.value = []; quota.value = null; selectedBatch.value = ''
    nextBatch.value = ''; nextTask.value = ''; batchPages = 1; taskPages = 1
    token.value = ''; pending.value = null; showCreate.value = false; view.value = 'batches'
    updatedAt.value = ''; notice.value = ''; error.value = ''; loading.value = false
  }
  function restorePending() {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey()) || 'null')
      pending.value = value && typeof value.key === 'string' && value.body ? value : null
    } catch { pending.value = null }
  }
  async function run(fn: (identity: number) => Promise<void>, success = '') {
    if (busy.value) return
    const identity = epoch
    busy.value = true; error.value = ''; notice.value = ''
    try { await fn(identity); if (identity === epoch && success) notice.value = success }
    catch (e) { if (identity === epoch) error.value = message(e) }
    finally { if (identity === epoch) busy.value = false }
  }
  async function readPages(path: string, count: number, valid: () => boolean) {
    let cursor = '', items: Row[] = [], seen = new Set<string>()
    for (let page = 0; page < count; page++) {
      const data = await api(path + (cursor ? (path.includes('?') ? '&' : '?') + 'before=' + encodeURIComponent(cursor) : ''))
      if (!valid()) return null
      for (const row of data.items) if (!seen.has(row.id)) { seen.add(row.id); items.push(row) }
      cursor = data.next_cursor || ''
      if (!cursor) break
    }
    return { items, next_cursor: cursor }
  }
  async function refreshData(silent = false) {
    if (!me.value) return
    const identity = epoch, query = queryVersion, request = ++listRequest
    const valid = () => identity === epoch && query === queryVersion && request === listRequest
    const lists = view.value === 'batches' || view.value === 'review'
    if (!silent) loading.value = true
    try {
      const [b, t, q] = await Promise.all([
        lists ? readPages('/batches', batchPages, valid) : null,
        lists ? readPages(`/tasks?batch_id=${encodeURIComponent(selectedBatch.value)}&review=${view.value === 'review'}`, taskPages, valid) : null,
        api('/quota'),
      ])
      if (!valid()) return
      if (b) { batches.value = b.items; nextBatch.value = b.next_cursor }
      if (t) { tasks.value = t.items; nextTask.value = t.next_cursor }
      quota.value = q
      updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    } catch (e) { if (valid()) throw e }
    finally { if (valid()) loading.value = false }
  }
  async function enter(username: string, password: string) {
    if (busy.value) return
    const identity = ++epoch
    reset(); busy.value = true
    try {
      await login(username, password)
      const user = await api('/me')
      if (identity !== epoch) return
      me.value = user; restorePending(); await refreshData()
    } catch (e) { if (identity === epoch) error.value = message(e) }
    finally { if (identity === epoch) busy.value = false }
  }
  async function leave() {
    if (busy.value) return
    const identity = ++epoch
    closeDetails(); token.value = ''; busy.value = true; error.value = ''
    try { await logout(); if (identity === epoch) { me.value = null; reset() } }
    catch (e) { if (identity === epoch) error.value = message(e) }
    finally { if (identity === epoch) busy.value = false }
  }
  async function navigate(next: View) {
    if (busy.value || view.value === next) return
    closeDetails(); error.value = ''; notice.value = ''; queryVersion++; view.value = next
    token.value = ''; selectedBatch.value = ''; tasks.value = []; nextTask.value = ''; taskPages = 1
    await run(() => refreshData())
  }
  async function selectBatch(id: string) {
    if (busy.value) return
    queryVersion++; selectedBatch.value = id; view.value = 'batches'
    tasks.value = []; nextTask.value = ''; taskPages = 1
    await run(() => refreshData())
  }
  async function more(kind: 'batches' | 'tasks') {
    if (busy.value || !(kind === 'batches' ? nextBatch.value : nextTask.value)) return
    const previous = kind === 'batches' ? batchPages : taskPages
    if (kind === 'batches') batchPages++; else taskPages++
    await run(async identity => {
      try { await refreshData() }
      catch (e) {
        if (identity === epoch) {
          if (kind === 'batches') batchPages = previous; else taskPages = previous
        }
        throw e
      }
    })
  }
  function applyDetail(data: Row, id: string) {
    if (selected.value?.id === id && Number(data.revision) >= Number(selected.value.revision)) selected.value = data
  }
  async function inspect(id: string) {
    await run(async identity => {
      closeDetails(); const request = ++detailRequest
      const detail = await api('/tasks/' + encodeURIComponent(id))
      if (identity !== epoch || request !== detailRequest) return
      selected.value = detail
      events = new EventSource('/api/v2/tasks/' + encodeURIComponent(id) + '/events')
      for (const name of ['task_scheduled', 'step_claimed', 'step_committed', 'task_completed', 'task_failed', 'task_cancelled', 'retry_requested', 'review_recorded', 'lease_expired']) {
        events.addEventListener(name, () => {
          if (identity !== epoch || request !== detailRequest || selected.value?.id !== id) return
          api('/tasks/' + encodeURIComponent(id)).then(data => {
            if (identity === epoch && request === detailRequest) applyDetail(data, id)
          }).catch(() => {})
        })
      }
    })
  }
  async function create(body?: BatchBody) {
    await run(async identity => {
      if (!pending.value) {
        if (!body || !Array.isArray(body.items) || body.items.length < 1 || body.items.length > 100) throw new Error('每批需要 1 至 100 个任务')
        const submission = { key: crypto.randomUUID(), body }
        try { localStorage.setItem(storageKey(), JSON.stringify(submission)) }
        catch { throw new Error('浏览器无法保存提交记录，请允许本地存储后重试。任务尚未提交。') }
        pending.value = submission
      }
      const original = pending.value
      let result: Row
      try { result = await api('/batches', original.body, { 'Idempotency-Key': original.key }) }
      catch (e) {
        // A validation rejection confirms non-admission. Unknown outcomes must keep the exact submission.
        if (identity === epoch && e instanceof ApiError && e.status === 422) {
          localStorage.removeItem(storageKey()); pending.value = null
        }
        throw e
      }
      if (identity !== epoch) return
      localStorage.removeItem(storageKey()); pending.value = null; showCreate.value = false
      queryVersion++; view.value = 'batches'; selectedBatch.value = result.batch_id; taskPages = 1
      tasks.value = []; await refreshData()
      notice.value = '批次已提交，可在任务列表查看执行进度。'
    })
  }
  async function action(name: string, extra: Row = {}) {
    await run(async identity => {
      const task = selected.value
      if (!task) return
      await api(`/tasks/${encodeURIComponent(task.id)}/${name}`, { expected_revision: task.revision, ...extra })
      if (identity !== epoch) return
      const data = await api('/tasks/' + encodeURIComponent(task.id))
      if (identity !== epoch) return
      applyDetail(data, task.id); await refreshData()
    }, '操作已记录。')
  }
  async function retry() {
    const unknown = selected.value?.status === 'UNKNOWN'
    if (unknown && !confirm('上一次模型请求可能已经执行并收费。保留未知预算占用，并确认可能重复调用后重试？')) return
    await action('retry', { acknowledge_possible_duplicate: unknown })
  }
  async function download() {
    await run(async identity => {
      const data = await api('/batches/' + encodeURIComponent(selectedBatch.value) + '/export')
      if (identity !== epoch) return
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
      const link = document.createElement('a')
      link.href = url; link.download = 'finreceipts-approved.json'; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    }, '已导出人工接受的结果版本。')
  }
  async function generateToken() {
    await run(async identity => {
      token.value = ''
      const result = await api('/tokens', { scope: 'submit', days: 7 })
      if (identity === epoch) token.value = result.token
    }, '令牌已生成，请妥善保存。离开接入页面后将不再显示。')
  }
  async function addMember(body: Row) { await run(async () => { await api('/members', body) }, '组织成员已创建。') }

  onMounted(async () => {
    const identity = epoch
    try {
      await refreshCsrf()
      const user = await api('/me')
      if (identity !== epoch) return
      me.value = user; restorePending(); await refreshData()
    } catch (e) { if (identity === epoch && !(e instanceof ApiError && e.status === 401)) error.value = message(e) }
    finally { initializing.value = false }
    timer = setInterval(() => {
      if (me.value && !busy.value && !loading.value && !polling && !document.hidden) {
        const identity = epoch, query = queryVersion
        polling = true
        refreshData(true).catch(e => { if (identity === epoch && query === queryVersion) error.value = message(e) })
          .finally(() => { polling = false })
      }
    }, 5000)
  })
  onUnmounted(() => { epoch++; clearInterval(timer); events?.close() })
  return { me, view, batches, tasks, quota, selectedBatch, selected, showCreate, error, notice, busy, loading,
    initializing, pending, token, nextBatch, nextTask, updatedAt, canSubmit, queued, enter, leave, navigate,
    selectBatch, more, inspect, closeDetails, create, action, retry, download, generateToken, addMember }
}
