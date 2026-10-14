(() => {
  const $ = id => document.getElementById(id), U = window.FinUI;
  function body() {
    return {question: $('question').value, ticker: $('ticker').value.trim(), fiscal_years: U.years($('years').value),
      mode: $('mode').value, feedback: $('feedback').value, max_revisions: 2, as_of: $('as_of').value || null};
  }
  function showHtml(html) {
    const frame = U.el('iframe'); frame.title = '财务证据报告'; frame.srcdoc = html; $('report').replaceChildren(frame);
  }
  function log(title, note, raw) {
    const li = U.el('li'), head = U.el('div', undefined, 'event-head'); head.append(U.el('strong', title)); li.append(head);
    if (note) li.append(U.el('p', note, 'event-note'));
    if (raw) li.append(U.rawDetails(raw));
    $('steps').append(li);
  }
  function setBusy(busy) { for (const id of ['btnStream','btnAsk','exAapl','exNvda']) $(id).disabled = busy; }
  function status(text, tone = '') { $('ask-status').textContent = text; $('ask-status').dataset.tone = tone; }
  $('exNvda').onclick = () => {
    $('question').value = '总结英伟达（NVDA）2025 财年的业绩，并与 2024 财年对比。';
    $('ticker').value = 'NVDA'; $('years').value = '2024,2025'; $('as_of').value = '2025-06-30';
  };
  $('exAapl').onclick = () => {
    $('question').value = '苹果（AAPL）2023 财年的营业收入是多少？';
    $('ticker').value = 'AAPL'; $('years').value = '2023'; $('as_of').value = '';
  };
  function handleEvent(event, data) {
    if (event === 'step') log(U.node(data.node), data.audit_summary ? '数值核验已更新，展开查看完整记录。' : '本步骤已完成。', data);
    else if (event === 'done') { log('问答已完成', '修订次数：' + data.revisions, data.audit_summary); if (data.html) showHtml(data.html); status('已完成'); }
    else if (event === 'error') throw new Error(data.detail || JSON.stringify(data));
    else log(event === 'start' ? '开始问答' : '执行记录', undefined, data);
  }
  async function run(streaming) {
    if (!$('ask-form').reportValidity()) return;
    setBusy(true); $('error').replaceChildren(); $('steps').replaceChildren(); status('执行中', 'active');
    try {
      const response = await fetch(streaming ? '/api/ask/stream' : '/api/ask',
        {method: 'POST', headers: {'Content-Type': 'application/json', ...(streaming ? {'Accept': 'text/event-stream'} : {})}, body: JSON.stringify(body())});
      if (!response.ok) throw new Error(await response.text());
      if (!streaming) { const data = await response.json(); handleEvent('done', data); return; }
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = '', doneEvent = false;
      function consume(chunk) {
        let event = 'message', data = '';
        for (const line of chunk.split(/\r?\n/)) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          if (line.startsWith('data:')) data += line.slice(5).trim();
        }
        if (!data) return;
        handleEvent(event, JSON.parse(data));
        if (event === 'done') doneEvent = true;
      }
      try {
        while (true) {
          const {value, done} = await reader.read();
          buffer += done ? decoder.decode() : decoder.decode(value, {stream: true});
          const chunks = buffer.split(/\r?\n\r?\n/); buffer = chunks.pop(); chunks.forEach(consume);
          if (done) { if (buffer.trim()) consume(buffer); break; }
        }
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
      if (!doneEvent) throw new Error('响应流已结束，但未收到完成记录。');
    } catch (error) { status('未完成', 'warn'); U.showError($('error'), error); log('本次请求未完成', '请查看页面上方的错误详情。'); }
    finally { setBusy(false); }
  }
  $('ask-form').onsubmit = event => { event.preventDefault(); run(true); };
  $('btnAsk').onclick = () => run(false);
  U.environment();
})();
