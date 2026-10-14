(() => {
  const $ = id => document.getElementById(id), U = window.FinUI;
  let current = null, busy = false, cursor = 0, seen = [], pendingKey = null, pendingBody = null, layoutRun;
  const narrow = matchMedia('(max-width: 959px)');
  const terminal = new Set(['completed', 'cancelled', 'budget_exhausted']);
  function layout() { $('settings').open = !current?.state || !narrow.matches; }
  narrow.addEventListener('change', layout);
  async function api(url, body, headers = {}) {
    const response = await fetch(url, {method: body === undefined ? 'GET' : 'POST',
      headers: {'Content-Type': 'application/json', ...headers}, body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
    return data;
  }
  function controls() {
    $('create').disabled = busy;
    for (const id of ['step', 'run']) { $(id).disabled = busy || current?.status !== 'ready'; $(id).hidden = terminal.has(current?.status); }
    $('refresh').disabled = busy || !current?.state;
    $('cancel').disabled = busy || !current?.state; $('cancel').hidden = terminal.has(current?.status);
    const expired = current?.status === 'running' && current.lease?.expires * 1000 < Date.now();
    const recoverable = current?.status === 'interrupted' || expired;
    $('recovery').hidden = !recoverable;
    $('resume').disabled = busy || !$('ack').checked || !recoverable;
    $('compare').disabled = busy || !current?.state?.answer;
  }
  function render() {
    if (!current?.state) return;
    document.body.classList.add('has-run');
    if (layoutRun !== current.run_id) { layout(); layoutRun = current.run_id; }
    $('status').textContent = U.statuses[current.status] || '未知状态';
    $('status').dataset.tone = current.status === 'running' || current.status === 'ready' ? 'active' :
      ['interrupted', 'budget_exhausted'].includes(current.status) ? 'warn' : '';
    $('run-title').textContent = current.request.question;
    $('run-context').hidden = false;
    $('run-context').textContent = current.request.ticker + ' · ' + current.request.fiscal_years.join('、') + ' 财年';
    $('settings-context').replaceChildren(U.el('strong', $('run-context').textContent),
      U.el('span', current.request.question), U.el('span', '展开任务设置 / 新建任务'));
    $('dispatches').textContent = current.dispatches + ' / ' + current.request.max_dispatches;
    $('attempts').textContent = current.state.attempts.length;
    $('next').textContent = terminal.has(current.status) ? '已停止' : U.node(current.next_node);
    $('reason').textContent = U.reason(current.stop_reason || current.quality?.reason);
    $('identity').textContent = '任务编号：' + current.run_id + '\n记录版本：' + current.revision + '\n证据摘要 SHA-256：' + current.evidence_digest;
    $('answer').textContent = current.state.answer || '任务已创建，执行后将在这里显示回答。';
    for (const id of ['run-info','answer-section','comparison-section','evidence-details']) $(id).hidden = false;
    $('empty').hidden = true; $('export').hidden = false; $('export').href = '/api/runs/' + current.run_id + '/export';
    controls();
  }
  async function events() {
    const response = await fetch('/api/runs/' + current.run_id + '/events', {headers: {'Last-Event-ID': String(cursor)}});
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
  $('create-form').onsubmit = e => { e.preventDefault(); action(async () => {
    const years = U.years($('years').value);
    const body = {question: $('question').value, ticker: $('ticker').value.trim(), fiscal_years: years,
      max_dispatches: Number($('budget').value), max_revisions: Number($('revisions').value)};
    const serialized = JSON.stringify(body);
    if (pendingBody !== serialized) { pendingKey = crypto.randomUUID(); pendingBody = serialized; }
    current = await api('/api/runs', body, {'Idempotency-Key': pendingKey}); pendingKey = null; pendingBody = null;
    location.hash = current.run_id; cursor = 0; seen = []; $('ack').checked = false;
    resetComparison(); render(); await events();
  }); };
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
  U.environment();
  // Browser back/forward or a pasted task fragment must not leave an old run visible.
  window.addEventListener('hashchange', () => {
    if (location.hash.slice(1) !== current?.run_id) location.reload();
  });
  action(async () => { const id = location.hash.slice(1); if (/^[a-f0-9]{32}$/.test(id)) { current = {run_id: id}; await refresh(); } });
})();
