<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {api,login,logout,refreshCsrf} from './api'
type Row=Record<string,any>
const me=ref<Row|null>(null),username=ref('alpha.analyst'),password=ref(''),error=ref(''),busy=ref(false)
const view=ref('batches'),batches=ref<Row[]>([]),tasks=ref<Row[]>([]),quota=ref<Row|null>(null)
const selectedBatch=ref(''),selected=ref<Row|null>(null),showCreate=ref(false),reason=ref(''),token=ref('')
const title=ref('年度财务指标核验'),ticker=ref('AAPL'),year=ref(2023),metric=ref('revenue'),kind=ref('metric')
const metricKind=ref('direct'),denominator=ref('revenue'),question=ref(''),jsonMode=ref(false),jsonInput=ref('')
const pending=ref<{key:string,body:unknown}|null>(null),nextBatch=ref(''),nextTask=ref('')
const memberName=ref(''),memberPassword=ref(''),memberRole=ref('ANALYST')
const metrics=['revenue','net income','operating income','gross profit','total assets','total liabilities','cash and cash equivalents','net cash provided by operating activities','diluted EPS','research and development expense']
const labels:Record<string,string>={QUEUED:'等待调度',READY:'等待执行',RUNNING:'正在执行',COMPLETED:'执行完成',FAILED:'执行失败',UNKNOWN:'调用结果未知',CANCELLED:'已取消',PENDING:'待处理',SUPPORTED:'证据支持',CONTRADICTED:'与证据矛盾',UNSUPPORTED:'缺少支持',ACCEPTED:'已接受',REJECTED:'已驳回',PREPARE:'准备证据',MODEL:'模型执行',VERIFY:'确定性核验',DONE:'完成'}
const text=(key:string)=>labels[key]||key
const evidence=computed(()=>selected.value?.result?.target_audit?.evidence)
const facts=computed(()=>evidence.value?.facts||[])
const number=(value:number)=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:8}).format(value)
function sourceLink(url:string){try{const u=new URL(url);return u.protocol==='https:'&&u.hostname==='www.sec.gov'?u.href:''}catch{return ''}}
const reviewTasks=computed(()=>tasks.value.filter(t=>t.status==='COMPLETED'&&t.review_status==='PENDING'))
const canSubmit=computed(()=>['ADMIN','ANALYST'].includes(me.value?.role))
const canReview=computed(()=>['ADMIN','REVIEWER'].includes(me.value?.role)&&selected.value?.creator_id!==me.value?.id)
let timer:ReturnType<typeof setInterval>|undefined,events:EventSource|null=null,epoch=0
const storageKey=()=>`fr:v2:pending:${me.value?.tenant_id}:${me.value?.id}`
async function guarded(fn:()=>Promise<void>){error.value='';busy.value=true;try{await fn()}catch(e){error.value=e instanceof Error?e.message:String(e)}finally{busy.value=false}}
async function load(append: 'batches'|'tasks'|false=false){
  const captured=epoch
  const [b,t,q]=await Promise.all([append==='tasks'?null:api('/batches'+(append==='batches'&&nextBatch.value?'?before='+nextBatch.value:'')),append==='batches'?null:api('/tasks?batch_id='+selectedBatch.value+'&review='+(view.value==='review')+(append==='tasks'&&nextTask.value?'&before='+nextTask.value:'')),api('/quota')])
  if(captured!==epoch)return
  if(b){batches.value=append==='batches'?[...batches.value,...b.items]:b.items;nextBatch.value=b.next_cursor}
  if(t){tasks.value=append==='tasks'?[...tasks.value,...t.items]:t.items;nextTask.value=t.next_cursor}quota.value=q
}
async function enter(){await guarded(async()=>{await login(username.value,password.value);password.value='';me.value=await api('/me');epoch++;restorePending();await load()})}
function restorePending(){try{pending.value=JSON.parse(localStorage.getItem(storageKey())||'null')}catch{pending.value=null}}
async function leave(){await logout();epoch++;me.value=null;selected.value=null;pending.value=null;token.value='';events?.close()}
async function selectBatch(id:string){selectedBatch.value=id;view.value='batches';await guarded(()=>load())}
async function inspect(id:string){
  await guarded(async()=>{selected.value=await api('/tasks/'+id);reason.value='';events?.close();const captured=epoch
    events=new EventSource('/api/v2/tasks/'+id+'/events')
    for(const kind of ['task_scheduled','step_claimed','step_committed','task_completed','task_failed','task_cancelled','retry_requested','review_recorded','lease_expired'])
      events.addEventListener(kind,()=>{if(captured===epoch&&selected.value?.id===id)api('/tasks/'+id).then(t=>{if(captured===epoch&&selected.value?.id===id)selected.value=t}).catch(()=>{})})
  })
}
async function create(){await guarded(async()=>{
  if(!pending.value){
    const items=jsonMode.value?JSON.parse(jsonInput.value):[{kind:kind.value,ticker:ticker.value.toUpperCase(),target:{kind:metricKind.value,metric:metric.value,fiscal_year:year.value,denominator:metricKind.value==='ratio'?denominator.value:null},question:question.value||null}]
    if(!Array.isArray(items)||items.length<1||items.length>100)throw new Error('每批需要 1 至 100 个任务')
    pending.value={key:crypto.randomUUID(),body:{title:title.value,items}};localStorage.setItem(storageKey(),JSON.stringify(pending.value))
  }
  const result=await api('/batches',pending.value.body,{'Idempotency-Key':pending.value.key})
  localStorage.removeItem(storageKey());pending.value=null;showCreate.value=false;selectedBatch.value=result.batch_id;await load()
})}
async function action(name:string,extra:Row={}){await guarded(async()=>{
  const t=selected.value;if(!t)return
  await api(`/tasks/${t.id}/${name}`,{expected_revision:t.revision,...extra})
  selected.value=await api('/tasks/'+t.id);await load()
})}
async function retry(){if(selected.value?.status==='UNKNOWN'&&!confirm('上一次模型请求可能已经执行并收费。保留未知预算占用，并确认可能重复调用后重试？'))return;await action('retry',{acknowledge_possible_duplicate:selected.value?.status==='UNKNOWN'})}
async function download(){await guarded(async()=>{const data=await api('/batches/'+selectedBatch.value+'/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='finreceipts-approved.json';a.click();URL.revokeObjectURL(url)})}
onMounted(async()=>{try{await refreshCsrf();me.value=await api('/me');epoch++;restorePending();await load()}catch{}timer=setInterval(()=>{if(me.value&&!document.hidden)load().catch(()=>{})},5000)})
onUnmounted(()=>{clearInterval(timer);events?.close()})
</script>

<template>
  <div v-if="!me" class="login-shell">
    <section class="intro"><div class="brand">F<span>R</span> / FINRECEIPTS</div><p class="eyebrow">FINANCIAL EVIDENCE WORKSPACE</p><h1>每一个数字，<br>都有据可查。</h1><p>从批量研究到人工复核，<br>让证据、执行过程与交付结果保持一致。</p><div class="intro-rule">01 提交任务 <span>→</span> 02 核验证据 <span>→</span> 03 复核交付</div></section>
    <form class="login-card" @submit.prevent="enter"><p class="eyebrow">团队工作台</p><h2>登录 FinReceipts</h2><p class="muted">使用管理员分配的组织账号</p><label>用户名<input v-model="username" autocomplete="username" required></label><label>密码<input v-model="password" type="password" autocomplete="current-password" required></label><p v-if="error" class="error" role="alert">{{error}}</p><button class="primary" :disabled="busy">{{busy?'正在登录…':'进入工作台 →'}}</button><small>本地部署账号由初始化配置提供。密码不会保存到浏览器存储。</small></form>
  </div>
  <div v-else class="workspace">
    <aside class="sidebar"><div class="brand">F<span>R</span> <strong>FinReceipts</strong></div><p class="tenant">{{quota?.name||me.tenant_id}} <span>工作空间</span></p><nav><button :class="{active:view==='batches'}" @click="view='batches'">▦ <span>批次与任务</span></button><button :class="{active:view==='review'}" @click="view='review';selectedBatch='';load()">✓ <span>待复核任务</span></button><button :class="{active:view==='quota'}" @click="view='quota'">◷ <span>用量与配额</span></button><button :class="{active:view==='settings'}" @click="view='settings'">⚙ <span>团队与接入</span></button></nav><div class="sidebar-note">先证据，后结论。<small>机器核验与人工复核独立记录。</small></div><div class="identity"><b>{{me.username}}</b><small>{{me.role}}</small><button @click="leave">退出登录</button></div></aside>
    <main><header><div><p class="eyebrow">RESEARCH OPERATIONS / 工作台</p><h1>{{({batches:'批次与任务',review:'待复核任务',quota:'用量与配额',settings:'团队与接入'} as Record<string,string>)[view]}}</h1></div><button v-if="canSubmit" class="primary" @click="showCreate=true">＋ 新建核验批次</button></header>
      <p v-if="error" class="error" role="alert">{{error}} <button @click="error=''">关闭</button></p>
      <div v-if="pending" class="notice">有一笔提交尚未确认。恢复时会沿用原始任务内容和幂等键。<button @click="create" :disabled="busy">确认原提交</button></div>
      <template v-if="view==='batches'">
        <div class="stats"><div><span>当前批次页</span><b>{{batches.length}}</b><small>可使用分页查看历史</small></div><div><span>待处理任务</span><b>{{quota?.states?.filter((s:Row)=>['QUEUED','READY','RUNNING'].includes(s.status)).reduce((n:number,s:Row)=>n+s.count,0)||0}}</b><small>组织范围内的执行队列</small></div><div><span>组织已计用量</span><b>¥ {{((quota?.spent_micros||0)/1e6).toFixed(3)}}</b><small>按记录的 token 保守估算</small></div></div>
        <section class="panel"><div class="panel-head"><h2>研究批次</h2><button @click="selectBatch('')">查看所有任务</button></div><div class="batch-grid"><button v-for="b in batches" :key="b.id" :class="['batch-card',{chosen:selectedBatch===b.id}]" @click="selectBatch(b.id)"><span class="eyebrow">BATCH · {{b.id.slice(0,8)}}</span><h3>{{b.title}}</h3><span>{{b.item_count}} 个任务 <span class="muted">· {{new Date(b.created_at).toLocaleDateString()}}</span></span></button><p v-if="!batches.length" class="empty">还没有研究批次。新建批次后，可在这里跟踪每项任务的证据和结果。</p></div><button v-if="nextBatch" @click="guarded(()=>load('batches'))">加载更多</button></section>
      </template>
      <section v-if="view==='batches'||view==='review'" class="panel"><div class="panel-head"><div><h2>{{view==='review'?'等待人工审核':'任务进度'}}</h2><small>执行完成仅表示流程结束，核验结论请单独查看。</small></div><button v-if="selectedBatch" @click="download">导出已审核结果 ↓</button></div><div class="table-scroll"><table><thead><tr><th>任务</th><th>执行状态</th><th>机器结论</th><th>人工审核</th><th>模型尝试</th><th></th></tr></thead><tbody><tr v-for="t in view==='review'?reviewTasks:tasks" :key="t.id"><td><b>{{t.ticker}} · FY{{t.fiscal_year}}</b><small class="task-label">{{t.metric}} · {{t.id.slice(0,8)}}</small></td><td><span :class="['pill',t.status]">{{text(t.status)}}</span></td><td>{{text(t.machine_verdict)}}</td><td>{{text(t.review_status)}}</td><td>{{t.dispatches}}</td><td><button @click="inspect(t.id)">查看证据 ↗</button></td></tr></tbody></table></div><p v-if="!(view==='review'?reviewTasks:tasks).length" class="empty">当前没有符合条件的任务。</p><button v-if="nextTask" @click="guarded(()=>load('tasks'))">加载后续任务</button></section>
      <template v-if="view==='quota'&&quota"><div class="stats"><div><span>已计用量</span><b>¥ {{(quota.spent_micros/1e6).toFixed(3)}}</b></div><div><span>预留与未知占用</span><b>¥ {{(quota.reserved_micros/1e6).toFixed(3)}}</b></div><div><span>组织预算上限</span><b>¥ {{(quota.budget_micros/1e6).toFixed(2)}}</b></div></div><section class="panel"><h2>资源边界</h2><dl><dt>待处理任务上限</dt><dd>{{quota.max_pending}}</dd><dt>组织同时执行上限</dt><dd>{{quota.max_running}}</dd><dt>记账口径</dt><dd>调用前保守预留；结果不明保留占用。该数值不等同于供应商对账单。</dd></dl><div v-for="s in quota.states" :key="s.status" class="state-row"><span>{{text(s.status)}}</span><b>{{s.count}}</b></div></section></template>
      <section v-if="view==='settings'" class="panel settings"><h2>批处理 API 接入</h2><p>令牌只显示一次，可提交任务及读取本账号授权的数据。</p><button @click="guarded(async()=>{token=(await api('/tokens',{scope:'submit',days:7})).token})">生成 7 天令牌</button><code v-if="token" class="token">{{token}}</code><form v-if="me.role==='ADMIN'" @submit.prevent="guarded(async()=>{await api('/members',{username:memberName,password:memberPassword,role:memberRole});memberPassword='';memberName=''})"><h2>新增组织成员</h2><label>用户名<input v-model="memberName" required></label><label>初始密码<input type="password" v-model="memberPassword" minlength="12" required></label><label>角色<select v-model="memberRole"><option>ANALYST</option><option>REVIEWER</option><option>ADMIN</option></select></label><button class="primary">创建成员</button></form></section>
    </main>
    <div v-if="showCreate" class="overlay" @click.self="showCreate=false"><section class="dialog"><div class="panel-head"><div><p class="eyebrow">NEW BATCH</p><h2>新建核验批次</h2></div><button @click="showCreate=false" aria-label="关闭">✕</button></div><form @submit.prevent="create"><label>批次名称<input v-model="title" required maxlength="120"></label><div class="tabs"><button type="button" :class="{active:!jsonMode}" @click="jsonMode=false">单项表单</button><button type="button" :class="{active:jsonMode}" @click="jsonMode=true">批量 JSON 清单</button></div><template v-if="!jsonMode"><div class="form-grid"><label>股票代码<input v-model="ticker" required maxlength="16"></label><label>财年<input v-model.number="year" type="number" min="1990" max="2100"></label><label>执行方式<select v-model="kind"><option value="metric">确定性取数／计算</option><option value="research">研究表述与核验</option></select></label><label>计算类型<select v-model="metricKind"><option value="direct">直接指标</option><option value="ratio">比率</option><option value="yoy_growth">同比增长</option></select></label></div><label>指标<select v-model="metric"><option v-for="m in metrics">{{m}}</option></select></label><label v-if="metricKind==='ratio'">分母指标<select v-model="denominator"><option v-for="m in metrics">{{m}}</option></select></label><label v-if="kind==='research'">研究问题<textarea v-model="question" rows="3" maxlength="12000" placeholder="例如：解释该年度营收变化，并列出证据。"></textarea></label></template><label v-else>任务数组（最多 100 项）<textarea class="mono" v-model="jsonInput" rows="12" placeholder='[{"kind":"metric","ticker":"AAPL","target":{"kind":"direct","metric":"revenue","fiscal_year":2023}}]' required></textarea></label><div class="notice">任务使用冻结的年度证据。研究表述调用模型；确定性计算不消耗模型费用。</div><button class="primary" :disabled="busy||!title">{{busy?'正在确认提交…':'提交批次 →'}}</button></form></section></div>
    <div v-if="selected" class="overlay" @click.self="selected=null;events?.close()"><section class="dialog evidence"><div class="panel-head"><div><p class="eyebrow">EVIDENCE RECEIPT / {{selected.id.slice(0,8)}}</p><h2>{{selected.request.ticker}} · FY{{selected.request.target.fiscal_year}}</h2></div><button @click="selected=null;events?.close()" aria-label="关闭">✕</button></div><div class="verdicts"><span :class="['pill',selected.status]">{{text(selected.status)}}</span><span>机器：{{text(selected.machine_verdict)}}</span><span>审核：{{text(selected.review_status)}}</span></div><h3>{{selected.request.target.metric}}</h3><p v-if="selected.error_code" class="error">{{selected.error_code}}</p><blockquote v-if="selected.result">{{selected.result.answer}}</blockquote><p class="muted">{{selected.result?'当前核验覆盖指定数字、单位和期间；研究文字仍需人工判断。':'任务尚未产生最终结果。'}}</p><h3>年度财报证据</h3><p v-if="evidence?.formula" class="notice">计算方式：{{evidence.formula}}</p><div v-if="facts.length" class="table-scroll"><table class="facts"><thead><tr><th>指标 / 报告期间</th><th>披露数值</th><th>来源</th></tr></thead><tbody><tr v-for="(f,index) in facts" :key="index"><td><b>{{f.label||f.concept}}</b><small class="task-label">{{f.start?f.start+' → ':''}}{{f.end}}</small></td><td :title="String(f.value)">{{number(f.value)}} {{f.unit}}</td><td><a v-if="sourceLink(f.source_url)" :href="sourceLink(f.source_url)" target="_blank" rel="noopener noreferrer">{{f.form}} 原始报告 ↗</a><small class="task-label">披露：{{f.filed}}</small></td></tr></tbody></table></div><p v-else class="notice">当前没有足够的目标证据，不能据此认定数字通过核验。</p><details><summary>查看完整核验记录与证据摘要</summary><p class="mono">{{selected.evidence_digest}}</p><pre>{{JSON.stringify(selected.result?.target_audit||selected.evidence,null,2)}}</pre></details><h3>模型调用台账</h3><div v-for="a in selected.attempts" :key="a.id" class="state-row"><span>{{a.status}}</span><span>预留 ¥{{(a.reserved_micros/1e6).toFixed(4)}} · 计费用量 {{a.actual_micros===null?'未知':'¥'+(a.actual_micros/1e6).toFixed(4)}}</span></div><p v-if="!selected.attempts.length" class="muted">没有模型调用。</p><div class="actions" v-if="canSubmit"><button v-if="['QUEUED','READY','RUNNING','FAILED','UNKNOWN'].includes(selected.status)" @click="action('cancel')" :disabled="busy">取消任务</button><button v-if="['FAILED','UNKNOWN'].includes(selected.status)" @click="retry" :disabled="busy">确认后重试</button></div><form v-if="canReview&&selected.status==='COMPLETED'&&selected.review_status==='PENDING'" @submit.prevent="action('review',{decision:'ACCEPTED',reason})"><h3>人工复核</h3><label>复核理由<textarea v-model="reason" required maxlength="2000" rows="3"></textarea></label><div class="actions"><button class="primary" :disabled="busy||!reason">接受并纳入交付</button><button type="button" :disabled="busy||!reason" @click="action('review',{decision:'REJECTED',reason})">驳回</button></div><small>接受只记录人工决定，原机器核验结论保持不变。</small></form><div v-for="r in selected.reviews" :key="r.created_at" class="review-record"><b>{{text(r.decision)}}</b><p>{{r.reason}}</p></div></section></div>
  </div>
</template>
