/* Shared display helpers; no requests or model/data transformations. */
(() => {
  const key = 'finreceipts.theme';
  const media = matchMedia('(prefers-color-scheme: dark)');
  let preference = 'light';
  try { const saved = localStorage.getItem(key); if (['light','dark','system'].includes(saved)) preference = saved; } catch (_) { /* Storage is optional. */ }
  function applyTheme() {
    document.documentElement.dataset.theme = preference === 'system' ? (media.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.themePreference = preference;
  }
  applyTheme();
  media.addEventListener('change', () => { if (preference === 'system') applyTheme(); });
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelector('.skip')?.addEventListener('click', event => {
      event.preventDefault();
      const main = document.getElementById('main');
      main.tabIndex = -1; main.focus(); main.scrollIntoView();
    });
    const select = document.getElementById('theme');
    if (!select) return;
    select.value = preference;
    select.addEventListener('change', () => {
      preference = select.value; applyTheme();
      try { localStorage.setItem(key, preference); } catch (_) { /* Continue without persistence. */ }
    });
  });
  const nodes = {answer:'生成回答', verify:'核验数字', revise:'修订回答', escalate:'升级模型', __end__:'已结束'};
  const statuses = {ready:'待继续', running:'执行中', completed:'已完成', interrupted:'已中断', cancelled:'已取消', budget_exhausted:'额度已用尽'};
  const reasons = {numeric_match_only:'数字匹配通过，仍需复查语义归属。', unresolved_claims:'仍有未获支持的声明，请查看核验结果。', awaiting_verification:'回答已生成，等待核验。', dispatch_or_tool_budget:'调用额度已用尽，本次任务已停止。', step_failed:'本步骤未完成，可查看详情后恢复。', user_cancelled:'任务已取消，已发送的请求无法撤回。'};
  const verdicts = {supported:'匹配', unsupported:'未支持', contradicted:'矛盾'};
  const eventNames = {created:'任务已创建',step_started:'开始步骤',budget_reserved:'预留调用额度',step_completed:'步骤已完成',step_stopped:'执行已停止',cancelled:'任务已取消',resumed:'任务已恢复'};
  const node = value => nodes[value] || '未知步骤';
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  function badge(value, bounded=false) {
    const e = el('span', value === 'supported' && bounded ? '支持' : (verdicts[value] || '待核验'), 'badge');
    e.dataset.tone = value === 'supported' ? (bounded ? 'good' : 'warn') : value === 'contradicted' ? 'bad' : 'warn';
    return e;
  }
  function rawDetails(value, label='原始记录') {
    const d = el('details'); d.append(el('summary',label),el('pre',typeof value === 'string' ? value : JSON.stringify(value,null,2),'raw')); return d;
  }
  function reason(value) {
    if (reasons[value]) return reasons[value];
    if (!value) return '尚未核验。';
    if (value.includes('no recognized metric')) return '未识别到指标，无法确认数字归属。';
    if (value.includes('no explicit fiscal year')) return '缺少明确财年。';
    if (value.includes('conflicts with requested scope')) return '语句财年与任务范围不一致。';
    if (value.includes('value differs')) return '数值与所选财务事实不一致。';
    if (value.includes('entity') || value.includes('issuer')) return '主体或语句表达超出支持的核验范围。';
    if (value.startsWith('text scope')) return '语句表达超出支持的核验范围。';
    return '未满足本项核验条件，请展开查看依据。';
  }
  function eventNote(event) {
    const p=event.payload || {};
    if(event.kind==='created')return '已固定本次任务使用的财务事实。';
    if(event.kind==='budget_reserved')return `${p.counter==='dispatches'?'模型调度':'工具调用'}：已占用 ${p.reserved} / ${p.limit} 次。`;
    if(event.kind==='step_completed')return `${node(p.node)}完成${p.next_node==='__end__'?'，流程已结束。':`，下一步：${node(p.next_node)}。`}`;
    if(event.kind==='step_started')return `正在${node(p.node)}。`;
    if(event.kind==='step_stopped')return reason(p.reason);
    if(event.kind==='resumed')return `将从${node(p.node)}继续，已占用额度保留。`;
    if(event.kind==='cancelled')return '后续步骤已停止。';
    return '展开查看原始事件。';
  }
  function renderEvent(event) {
    const li=el('li'), head=el('div',undefined,'event-head');
    head.append(el('strong',eventNames[event.kind]||'执行记录'));
    const t=el('time',`#${event.seq} · ${new Date(event.time*1000).toLocaleTimeString('zh-CN',{hour12:false})}`);
    t.dateTime=new Date(event.time*1000).toISOString();head.append(t);
    li.append(head,el('p',eventNote(event),'event-note'),rawDetails(event)); return li;
  }
  function apiError(data, requestId) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : '请求未完成，请检查输入或稍后重试。');
    const id = data.request_id || requestId;
    if (/^[a-f0-9]{32}$/.test(id || '')) error.requestId = id;
    error.code = data.code;
    return error;
  }
  function showError(container, error) {
    const original=error.message || String(error);
    const guidance = {
      cascade_unconfigured:'尚未配置升级模型，请在核验设置中选择其他执行方式，或配置升级模型后重试。',
      invalid_request:'请求参数不符合要求，请检查输入内容与核验设置。',
      creation_storage_unavailable:'浏览器无法保存或读取创建恢复记录。请允许会话存储后重新加载，确认原请求结果。',
      creation_record_invalid:'创建恢复记录无法读取。请先处理上方的恢复提示，避免另建重复任务。',
      history_cursor_expired:'任务列表位置已变化，请点击“刷新列表”重新读取。',
      evidence_unavailable:'当前公司和财年没有可用证据，请调整范围或检查已录制的数据。',
      storage_busy:'任务存储暂时繁忙，请稍候刷新状态后继续。',
      capacity_exhausted:'当前执行名额已满，请稍候刷新状态后重试。已有任务仍可查看或取消。',
      request_too_large:'输入内容超过服务接收上限，请缩短问题或待核验文本。',
      query_too_large:'查询参数过长，请缩短问题后重试。',
      request_body_timeout:'请求内容未能及时上传完成，请检查连接后重试。',
      response_timeout:'等待已超时，后台工作可能仍在继续。已保存任务请先刷新状态；快速问答请等待执行名额释放后重试。',
      ask_failed:'本次回答生成失败，请保留请求编号并检查模型服务后重试。',
      audit_failed:'财务证据核验未完成，请保留请求编号并检查数据服务。'
    };
    let message='操作未完成，请刷新任务状态后重试。';
    if (Object.hasOwn(guidance, error.code)) message=guidance[error.code];
    else if (original.startsWith('财年')) message=original;
    else if (/fetch|network/i.test(original)) message='暂时无法连接服务，请检查本地服务是否正在运行。';
    else if (/revision|lease|not ready/i.test(original)) message='任务状态已变化，请刷新后继续。';
    else if (/implementation|provider configuration/i.test(original)) message='运行配置已更新，请保留原任务记录并创建新任务。';
    else if (/integrity/i.test(original)) message='任务记录完整性检查未通过，无法继续执行。';
    else if (/not found/i.test(original)) message='未找到该任务，请检查任务地址。';
    container.replaceChildren(el('div',message),rawDetails(original,'错误详情'));
    if (error.requestId) container.append(el('p', '排障请求编号：' + error.requestId, 'hint'));
  }
  async function environment() {
    const e=document.getElementById('environment'); if(!e)return;
    try { const r=await fetch('/health'); if(!r.ok)throw new Error(); const h=await r.json(); e.textContent=h.provider==='fake'?'本地演示':'已连接模型服务'; e.title=h.provider==='fake'?'使用固定回答，不代表真实模型效果。':`服务：${h.provider}`; }
    catch (_) {e.textContent='服务状态未知';}
  }
  function years(value) {
    const values = value.split(/[,，]/).map(v => v.trim());
    if (!values.length || values.length > 10 || values.some(v => !/^\d{4}$/.test(v) || Number(v) < 1990 || Number(v) > 2100))
      throw new Error('财年请填写 1990 至 2100 之间的四位年份，多个财年用逗号分隔，最多 10 个。');
    return values.map(Number);
  }
  document.addEventListener('invalid', event => {
    const details = event.target.closest('details');
    if (details) details.open = true;
  }, true);
  window.FinUI={node, statuses, reason, badge, el, rawDetails, renderEvent, apiError, showError, environment, years};
})();
