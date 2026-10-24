(() => {
  const $ = id => document.getElementById(id), U = window.FinUI;
  let current = null, busy = false, cursor = 0, seen = [], pending = null, pendingProblem = null, layoutRun;
  const journal = window.FinCreateJournal;
  const auth = window.FinRunAuth;
  let authenticated = false;
  let identityEpoch = 0;
  const narrow = matchMedia('(max-width: 959px)');
  const terminal = new Set(['completed', 'cancelled', 'budget_exhausted', 'failed']);
  function layout() { $('settings').open = !current?.state || !narrow.matches; }
  narrow.addEventListener('change', layout);
  async function api(url, body, headers = {}) {
    const response = await auth.request(url, {method: body === undefined ? 'GET' : 'POST',
      headers: {'Content-Type': 'application/json', ...headers}, body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) { const error = U.apiError(data, response.headers.get('X-Request-ID')); error.status = response.status; throw error; }
    return data;
  }
  function controls() {
    $('create').disabled = !authenticated || busy || !!pending || !!pendingProblem;
    for (const id of ['question','ticker','years','budget','revisions','target-enabled','target-kind','target-metric','target-denominator','target-as-of']) $(id).disabled = !authenticated || busy || !!pending || !!pendingProblem;
    $('pending-create').hidden = !pending && !pendingProblem;
    $('retry-create').hidden = !pending;
    $('retry-create').disabled = !authenticated || busy || !pending || !!pendingProblem;
    $('discard-controls').hidden = pendingProblem === 'creation_storage_unavailable';
    $('discard-create').disabled = busy || !$('discard-ack').checked;
    $('discard-ack').disabled = busy;
    $('pending-note').textContent = pendingProblem === 'creation_storage_unavailable'
      ? '浏览器会话存储不可用，暂时不能安全保存或读取创建记录。请允许会话存储后重新加载页面。'
      : pendingProblem === 'creation_identity_mismatch' ? '待确认请求属于其他用户。请切换回原用户；不会使用当前身份重发。'
      : pendingProblem ? '上次创建的恢复记录无法读取。请保留页面排查，或确认放弃找回后重新填写。'
      : '上次创建结果尚未确认。点击确认会重用原请求，找回已经创建的任务；若尚未创建，则完成原请求。原问题与设置暂时锁定。';
    if (!current?.state) {
      $('status').textContent = pending || pendingProblem ? '创建结果待确认' : '未创建任务';
      $('status').dataset.tone = pending || pendingProblem ? 'warn' : '';
    }
    for (const id of ['step', 'run']) { $(id).disabled = busy || current?.status !== 'ready'; $(id).hidden = terminal.has(current?.status); }
    $('refresh').disabled = busy || !current?.state;
    $('cancel').disabled = busy || !current?.state; $('cancel').hidden = terminal.has(current?.status);
    const expired = current?.status === 'running' && current.lease?.expires * 1000 < Date.now();
    const recoverable = current?.status === 'interrupted' || expired;
    $('recovery').hidden = !recoverable;
    $('resume').disabled = busy || !$('ack').checked || !recoverable;
    $('failure-notice').hidden = current?.status !== 'failed';
    $('compare').disabled = busy || !current?.state?.answer || current.status === 'failed';
  }
  function render() {
    if (!current?.state) return;
    document.body.classList.add('has-run');
    if (layoutRun !== current.run_id) { layout(); layoutRun = current.run_id; }
    $('status').textContent = U.statuses[current.status] || '未知状态';
    $('status').dataset.tone = current.status === 'running' || current.status === 'ready' ? 'active' :
      current.status === 'failed' ? 'bad' : ['interrupted', 'budget_exhausted'].includes(current.status) ? 'warn' : '';
    $('run-title').textContent = current.request.question;
    $('run-context').hidden = false;
    $('run-context').textContent = current.request.ticker + ' · ' + current.request.fiscal_years.join('、') + ' 财年';
    $('settings-context').replaceChildren(U.el('strong', $('run-context').textContent),
      U.el('span', current.request.question), U.el('span', '展开任务设置 / 新建任务'));
    $('dispatches').textContent = current.dispatches + ' / ' + current.request.max_dispatches;
    $('attempts').textContent = current.state.attempts.length;
    $('next').textContent = terminal.has(current.status) ? '已停止' : U.node(current.next_node);
    $('reason').textContent = U.reason(current.stop_reason || current.quality?.reason);
    $('reason').hidden = current.status === 'failed';
    const targetAudit = current.state.target_audit;
    $('target-result').replaceChildren();
    if (current.state.target) {
      const passed = current.quality?.target_gate_passed;
      const label = current.status === 'completed' && !passed ? '执行完成，目标核验未通过' : passed ? '目标数值核验通过（不代表全文正确）' : '目标数值待核验';
      $('target-result').append(U.el('p',label), U.rawDetails({target:current.state.target, audit:targetAudit || null, as_of:current.request.as_of, snapshot_retrieved_at:null},'目标与证据'));
    }
    $('failure-reason').textContent = current.status === 'failed' ? U.reason(current.stop_reason) : '';
    $('identity').textContent = '任务编号：' + current.run_id + '\n记录版本：' + current.revision + '\n证据摘要 SHA-256：' + current.evidence_digest;
    $('answer').textContent = current.state.answer || '任务已创建，执行后将在这里显示回答。';
    for (const id of ['run-info','answer-section','comparison-section','evidence-details']) $(id).hidden = false;
    $('empty').hidden = true; $('export').hidden = false; $('export').href = '/api/runs/' + current.run_id + '/export';
    controls();
  }
  async function events() {
    const response = await auth.request('/api/runs/' + current.run_id + '/events', {headers: {'Last-Event-ID': String(cursor)}});
    if (!response.ok) throw new Error('无法补读事件，请刷新状态。');
    for (const line of (await response.text()).split('\n')) if (line.startsWith('data: ')) {
      const event = JSON.parse(line.slice(6));
      if (event.seq > cursor) { seen.push(event); cursor = event.seq; }
    }
    $('events').replaceChildren(...seen.map(U.renderEvent));
    $('event-count').textContent = seen.length + ' 条记录';
  }
  async function refresh() { current = await api('/api/runs/' + current.run_id); render(); await events(); }
  async function action(fn) {
    if (busy) return;
    busy = true; controls(); $('error').replaceChildren();
    try { await fn(); } catch (e) { U.showError($('error'), e); } finally { busy = false; controls(); }
  }
  function resetComparison() { $('comparison').replaceChildren(U.el('p', '回答更新后，请重新复核当前回答。', 'hint')); }
  async function advance() {
    current = await api('/api/runs/' + current.run_id + '/advance', {expected_revision: current.revision});
    resetComparison(); render(); await events();
  }
  function clearPending() {
    try { journal.clear(); } catch (error) { pendingProblem = error.code; throw error; }
    pending = null; pendingProblem = null; $('discard-ack').checked = false; controls();
  }
  async function sendPending() {
    if (!pending || pendingProblem) return;
    journal.forPrincipal(pending, auth.principal());
    let doc;
    try { doc = await api('/api/runs', pending.body, {'Idempotency-Key': pending.key}); }
    catch (error) {
      // Our API validates before creation. Ambiguous failures keep the recovery record.
      if (error.status === 422 && error.code === 'invalid_request') clearPending();
      throw error;
    }
    if (!/^[a-f0-9]{32}$/.test(doc.run_id || '')) throw new Error('创建结果无法确认，请保留原请求重试。');
    current = doc;
    // Save the recoverable run URL before clearing the creation record or reading events.
    location.hash = current.run_id; cursor = 0; seen = []; $('ack').checked = false;
    resetComparison(); render(); clearPending(); await events();
  }
  $('create-form').onsubmit = e => { e.preventDefault(); action(async () => {
    if (pending || pendingProblem) return;
    const years = U.years($('years').value);
    const body = {question: $('question').value, ticker: $('ticker').value.trim(), fiscal_years: years,
      max_dispatches: Number($('budget').value), max_revisions: Number($('revisions').value)};
    if ($('target-enabled').checked) {
      if (years.length !== 1) throw new Error('目标核验只接受一个财年。');
      body.target = {kind:$('target-kind').value, metric:$('target-metric').value, fiscal_year:years[0]};
      if (body.target.kind === 'ratio') body.target.denominator = $('target-denominator').value;
      body.use_tools = true; body.as_of = $('target-as-of').value || null;
    }
    const record = {version: 2, principal_id:auth.principal(), operation:'create_run', key: crypto.randomUUID(), body};
    try { journal.save(record); } catch (error) {
      if (error.code === 'creation_storage_unavailable') pendingProblem = error.code;
      throw error;
    }
    pending = record; controls(); await sendPending();
  }); };
  $('retry-create').onclick = () => action(sendPending);
  $('discard-ack').onchange = controls;
  $('discard-create').onclick = () => action(async () => { if ($('discard-ack').checked) clearPending(); });
  $('step').onclick = () => action(advance);
  $('run').onclick = () => action(async () => { for (let i = 0; i < 50 && current.status === 'ready'; i++) await advance(); });
  $('refresh').onclick = () => action(refresh);
  $('cancel').onclick = () => action(async () => {
    current = await api('/api/runs/' + current.run_id + '/cancel', {expected_revision: current.revision}); render(); await events();
  });
  $('resume').onclick = () => action(async () => {
    current = await api('/api/runs/' + current.run_id + '/resume', {expected_revision: current.revision, acknowledge_possible_duplicate: true});
    $('ack').checked = false; render(); await events();
  });
  $('ack').onchange = controls;
  $('compare').onclick = () => action(async () => {
    const result = await api('/api/runs/' + current.run_id + '/counter-audit');
    const summary = U.el('div', undefined, 'comparison-summary');
    for (const [label, value] of [['数字匹配通过', result.numeric.supported + ' / ' + result.numeric.numbers],
      ['语句核验支持', result.bounded_annual.supported + ' / ' + result.bounded_annual.numbers], ['新增模型调用', result.model_calls]]) {
      const item = U.el('span', label + ' '); item.append(U.el('strong', value)); summary.append(item);
    }
    $('comparison').replaceChildren(summary);
    if (!result.bounded_audit.length) { $('comparison').append(U.el('p', '没有提取到可核验的数字，不能据此判定通过。', 'hint')); return; }
    const labels = ['数值声明', '数字匹配', '语句核验', '差异与依据'];
    const table = U.el('table'), head = U.el('thead'), hr = U.el('tr'), body = U.el('tbody');
    table.setAttribute('aria-label', '声明对照复核结果');
    labels.forEach((label, i) => { const th = U.el('th', label, i === 0 ? 'table-claim' : i < 3 ? 'table-verdict' : ''); th.scope = 'col'; hr.append(th); });
    head.append(hr); table.append(head, body);
    for (const audit of result.bounded_audit) {
      const diff = result.disagreements.find(d => JSON.stringify(d.span) === JSON.stringify(audit.claim.span));
      const tr = U.el('tr'), cells = labels.map(label => { const td = U.el('td'); td.dataset.label = label; tr.append(td); return td; });
      cells[0].textContent = audit.claim.text;
      cells[1].append(U.badge(diff ? diff.numeric : audit.verdict));
      cells[2].append(U.badge(audit.verdict, true));
      cells[3].append(U.el('div', audit.verdict === 'supported' ? '与所选主体、指标及财年证据一致。' : U.reason(audit.reasons?.[0])),
        U.rawDetails(audit, '查看原始依据'));
      body.append(tr);
    }
    const wrap = U.el('div', undefined, 'table-wrap'); wrap.append(table); $('comparison').append(wrap);
  });
  let historyBusy = false, historyLoaded = false, historyCursors = [null], historyPage = 0, historyNext = null, historyFirst = null;
  function historyControls() {
    $('history-refresh').disabled = historyBusy;
    $('history-previous').disabled = historyBusy || historyPage === 0;
    $('history-next').disabled = historyBusy || !historyNext;
  }
  async function loadHistory(page, reset = false) {
    if (!authenticated || historyBusy) return;
    const epoch = identityEpoch;
    historyBusy = true; historyControls(); $('history-error').replaceChildren();
    try {
      const cursor = reset ? null : historyCursors[page];
      const url = '/api/runs?limit=10' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : '');
      const result = page === 0 && !reset && historyFirst ? historyFirst : await api(url);
      if (epoch !== identityEpoch) return;
      if (reset) { historyCursors = [null]; historyFirst = result; }
      historyPage = page; historyNext = result.next_cursor; historyLoaded = true;
      if (historyNext) historyCursors[page + 1] = historyNext;
      const rows = result.items.map(item => {
        const li = U.el('li'); li.dataset.runId = item.run_id;
        const available = item.availability === 'available';
        const title = available ? item.question_preview + (item.question_truncated ? '…' : '') :
          item.availability === 'workflow_incompatible' ? '任务版本不兼容，请保留原记录' : '任务记录校验失败，请保留原记录';
        const link = U.el('a', title); link.href = '/workbench#' + encodeURIComponent(item.run_id); li.append(link);
        link.onclick = event => {
          if (current?.run_id === item.run_id) { event.preventDefault(); action(refresh); }
        };
        if (available) {
          li.append(U.el('p', item.ticker + ' · ' + item.fiscal_years.join('、') + ' 财年 · ' + (U.statuses[item.status] || '未知状态')),
            U.el('p', '已用调用额度 ' + item.dispatches + ' / ' + item.max_dispatches, 'muted'),
            U.el('p', '创建于 ' + new Date(item.created_at * 1000).toLocaleString('zh-CN', {hour12:false}), 'muted'));
        }
        const id = U.el('p', '任务编号：', 'muted'); id.append(U.el('code', item.run_id)); li.append(id);
        return li;
      });
      $('history-list').replaceChildren(...rows);
      $('history-note').textContent = rows.length ? `第 ${page + 1} 页 · ${rows.length} 个任务。状态以打开后的任务详情为准。` : '尚无已保存任务。';
    } catch (error) { if (epoch === identityEpoch) U.showError($('history-error'), error); }
    finally { if (epoch === identityEpoch) { historyBusy = false; historyControls(); } }
  }
  $('run-history').addEventListener('toggle', () => { if ($('run-history').open && !historyLoaded) loadHistory(0, true); });
  $('history-refresh').onclick = () => loadHistory(0, true);
  $('history-previous').onclick = () => loadHistory(historyPage - 1);
  $('history-next').onclick = () => loadHistory(historyPage + 1);
  async function initializeIdentity(token = '') {
    identityEpoch++; historyBusy = false;
    authenticated = false; controls();
    const identity = await auth.connect(token);
    authenticated = true;
    current = null; cursor = 0; seen = []; historyLoaded = false; historyFirst = null;
    historyCursors = [null]; historyPage = 0; historyNext = null;
    $('history-list').replaceChildren(); $('history-note').textContent = '展开后读取任务。';
    pending = null; pendingProblem = null;
    try {
    pending = journal.load();
    journal.forPrincipal(pending, identity.principal_id);
    if (pending) {
      const b = pending.body;
      $('question').value = b.question; $('ticker').value = b.ticker; $('years').value = b.fiscal_years.join(',');
      $('budget').value = b.max_dispatches; $('revisions').value = b.max_revisions;
      $('target-enabled').checked = !!b.target;
      if (b.target) { $('target-kind').value=b.target.kind; $('target-metric').value=b.target.metric; $('target-as-of').value=b.as_of || ''; if(b.target.denominator) $('target-denominator').value=b.target.denominator; }
    }
    } catch (error) { pendingProblem = error.code; }
    if (!pending && !pendingProblem && !$('question').value) $('question').value='苹果（AAPL）2023 财年的营业收入是多少？';
    controls();
    const id = location.hash.slice(1); if (/^[a-f0-9]{32}$/.test(id)) { current = {run_id:id}; await refresh(); }
  }
  function clearVisibleIdentity() {
    identityEpoch++; historyBusy = false;
    authenticated=false; current=null; seen=[]; cursor=0;
    for (const id of ['run-info','answer-section','comparison-section','evidence-details']) $(id).hidden=true;
    $('history-list').replaceChildren(); $('answer').textContent=''; $('events').replaceChildren();
    $('identity').textContent=''; $('comparison').replaceChildren(); $('target-result').replaceChildren();
    $('settings-context').replaceChildren(U.el('strong','任务设置'),U.el('span','展开查看或创建新任务'));
    $('run-title').textContent='研究结果'; $('run-context').hidden=true; $('empty').hidden=false;
    $('question').value=''; controls();
  }
  window.addEventListener('fin-auth-required', clearVisibleIdentity);
  $('auth-form').onsubmit = event => {event.preventDefault(); if(busy)return; clearVisibleIdentity(); action(async()=>{const token=$('auth-token').value; $('auth-token').value=''; await initializeIdentity(token);});};
  $('export').onclick = event => {event.preventDefault(); action(async()=>{
    const response=await auth.request('/api/runs/'+current.run_id+'/export');
    if(!response.ok) throw U.apiError(await response.json());
    const url=URL.createObjectURL(await response.blob()), link=document.createElement('a');
    link.href=url; link.download=current.run_id+'.json'; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  });};
  U.environment();
  // Browser back/forward or a pasted task fragment must not leave an old run visible.
  window.addEventListener('hashchange', () => {
    if (location.hash.slice(1) !== current?.run_id) location.reload();
  });
  action(() => initializeIdentity());
})();
