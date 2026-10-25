// Offline UI acceptance. Serves local assets; every API response is an in-memory fixture.
// Never reads .env, connects to Compose, or calls a model/provider.
const { chromium } = require('playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');

const root = path.resolve(__dirname, '..');
const out = path.resolve(process.argv[2] || '');
const baseline = process.argv.includes('--baseline');
const distArg = process.argv.find(a => a.startsWith('--dist='));
const dist = distArg ? path.resolve(distArg.slice(7)) : path.join(root, 'console', 'dist');
if (!process.argv[2] || fs.existsSync(out)) throw new Error('Pass a NEW output directory');
if (!fs.existsSync(path.join(dist, 'index.html'))) throw new Error('Build console first, or pass --dist=PATH');
fs.mkdirSync(out, { recursive: true });

const report = { status: 'FAILED', mode: baseline ? 'baseline' : 'offline-fixture', live_compose: 'UNRUN', real_model: 'UNRUN', checks: [], screenshots: [], layouts: [], contrast: [], errors: [], requests: [], native_zoom: { status: 'UNRUN' } };
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const clone = value => JSON.parse(JSON.stringify(value));
function manifest(base, selected) {
  const files = [];
  function visit(file) { if (fs.statSync(file).isDirectory()) fs.readdirSync(file).sort().forEach(name => visit(path.join(file, name))); else files.push(file); }
  selected.forEach(file => visit(path.join(base, file)));
  const entries = files.sort().map(file => ({ path: path.relative(base, file).replaceAll('\\', '/'), sha256: createHash('sha256').update(fs.readFileSync(file)).digest('hex') }));
  return { sha256: createHash('sha256').update(entries.map(entry => entry.path + ':' + entry.sha256).join('\n')).digest('hex'), files: entries };
}
report.binding = { build: manifest(dist, ['.']), script: manifest(root, ['scripts/v2_ui_acceptance.cjs']) };
if (!baseline) report.binding.source = manifest(path.join(root, 'console'), ['src', 'vite.config.ts', 'index.html', 'package.json', 'package-lock.json']);
function makeFixture() {
  const profiles = {
    'alpha.analyst': { id: 'analyst-id', username: 'alpha.analyst', tenant_id: 'alpha', role: 'ANALYST' },
    'alpha.reviewer': { id: 'reviewer-id', username: 'alpha.reviewer', tenant_id: 'alpha', role: 'REVIEWER' },
    'alpha.admin': { id: 'admin-id', username: 'alpha.admin', tenant_id: 'alpha', role: 'ADMIN' },
  };
  const batches = Array.from({ length: 9 }, (_, i) => ({ id: `batch-${String(i + 1).padStart(4, '0')}`, title: i === 1 ? '跨年度研发费用与经营现金流核验：覆盖长批次名称和不同披露单位的财报研究记录' : ['年度营收核验', '现金流研究', '资产负债核验'][i % 3] + ` ${i + 1}`, item_count: 12, created_at: '2026-10-24T08:30:00Z' }));
  const tickers = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'ORCL', 'ADBE', 'CRM', 'AMD', 'INTC', 'IBM', 'CSCO'];
  const tasks = tickers.map((ticker, i) => {
    const status = ['COMPLETED', 'UNKNOWN', 'FAILED', 'RUNNING', 'COMPLETED', 'COMPLETED', 'QUEUED', 'COMPLETED', 'COMPLETED', 'CANCELLED', 'COMPLETED', 'COMPLETED'][i];
    const machine = i === 4 ? 'UNSUPPORTED' : i === 5 ? 'CONTRADICTED' : status === 'COMPLETED' ? 'SUPPORTED' : 'PENDING';
    const review = i === 4 ? 'ACCEPTED' : i === 5 ? 'REJECTED' : 'PENDING';
    return { id: `task${String(i + 1).padStart(4, '0')}-fixture`, batch_id: i < 7 ? batches[0].id : batches[1].id, ticker, fiscal_year: 2023, metric: 'revenue', status, step: status === 'RUNNING' ? 'MODEL' : 'DONE', revision: 3, machine_verdict: machine, review_status: review, dispatches: status === 'UNKNOWN' ? 1 : 0, creator_id: i === 7 ? 'reviewer-id' : 'analyst-id', created_at: '2026-10-24T08:30:00Z', updated_at: '2026-10-24T08:31:00Z', request: { kind: 'metric', ticker, target: { kind: 'direct', metric: 'revenue', fiscal_year: 2023 } }, evidence_digest: 'e'.repeat(64), error_code: status === 'FAILED' ? 'evidence_unavailable' : null,
      result: status === 'COMPLETED' ? { answer: i === 4 ? '缺少足够证据，不能确认该年度数字。' : 'ANSWER: 383285000000 USD\n原始披露金额与报告期间保持完整。', target_audit: { verdict: machine, evidence: { formula: 'RevenueFromContractWithCustomerExcludingAssessedTax[FY2023]', facts: i === 4 ? [] : [{ label: 'Revenue from Contract with Customer, Excluding Assessed Tax', concept: 'RevenueFromContractWithCustomerExcludingAssessedTax', start: '2022-09-25', end: '2023-09-30', value: 383285000000.12345678, unit: 'USD', form: '10-K', filed: '2023-11-03', source_url: 'https://www.sec.gov/Archives/edgar/data/320193/report.htm' }, { label: '测试不可信来源，不应呈现可点击链接', end: '2023-09-30', value: 123456789012345, unit: 'USD', form: '10-K', filed: '2023-11-03', source_url: 'https://www.sec.gov.evil.example/report' }, { label: '测试非 HTTPS 来源', end: '2023-09-30', value: 1, unit: 'USD', form: '10-K', filed: '2023-11-03', source_url: 'http://www.sec.gov/report' }] } } } : null,
      attempts: status === 'UNKNOWN' ? [{ id: 'attempt-unknown', status: 'UNKNOWN', reserved_micros: 145000, actual_micros: null }] : [], reviews: review !== 'PENDING' ? [{ decision: review, reason: '已独立检查机器结论与证据不足状态；人工决定不改变原机器结论。', created_at: '2026-10-24T09:00:00Z' }] : [] };
  });
  return { user: null, profiles, batches, tasks, posts: [], delayedLists: [], pageSize: 6, failNextCreate: false, rejectNextCreate: false, delayNextTaskList: 0, delayNextTaskDetail: 0, empty: false, created: 0 };
}

async function installFixture(context, fixture) {
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.hostname.match(/^(127\.0\.0\.1|localhost)$/)) return route.abort();
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const endpoint = url.pathname.slice('/api/v2'.length);
    const method = request.method();
    const userAtRequest = fixture.user && clone(fixture.user);
    report.requests.push({ path: endpoint, method, user: userAtRequest?.username || null, query: url.search });
    const respond = (status, data) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) }).catch(() => {});
    const body = () => request.postDataJSON();
    if (endpoint === '/csrf') return respond(200, { token: 'offline-csrf', header: 'X-XSRF-TOKEN' });
    if (endpoint === '/login') { const form = new URLSearchParams(request.postData()); fixture.user = fixture.profiles[form.get('username')] || null; return respond(fixture.user ? 200 : 401, {}); }
    if (endpoint === '/logout') { fixture.user = null; return respond(200, {}); }
    if (!userAtRequest) return respond(401, { code: 'unauthenticated' });
    if (endpoint === '/me') return respond(200, userAtRequest);
    if (endpoint === '/quota') return respond(200, { name: '清和财报研究组', spent_micros: 2345000, reserved_micros: 145000, budget_micros: 10000000, max_pending: 100, max_running: 4, states: ['QUEUED', 'RUNNING', 'UNKNOWN', 'COMPLETED', 'FAILED'].map(status => ({ status, count: fixture.tasks.filter(t => t.status === status).length })) });
    if (endpoint === '/tokens') { fixture.posts.push({ endpoint, body: body() }); return respond(200, { token: 'offline_fixture_token_only' }); }
    if (endpoint === '/members') { fixture.posts.push({ endpoint, body: body() }); return respond(201, { id: 'fixture-member' }); }
    if (endpoint === '/batches' && method === 'POST') {
      const data = body(), key = request.headers()['idempotency-key']; fixture.posts.push({ endpoint, body: data, key });
      if (fixture.rejectNextCreate) { fixture.rejectNextCreate = false; return respond(422, { code: 'fixture_validation_rejection' }); }
      if (fixture.failNextCreate) { fixture.failNextCreate = false; return respond(503, { code: 'fixture_temporarily_unavailable' }); }
      const existing = fixture.posts.slice(0, -1).find(p => p.endpoint === endpoint && p.key === key && p.batch_id);
      if (existing) return respond(202, { batch_id: existing.batch_id });
      const id = `created-${++fixture.created}`; fixture.posts.at(-1).batch_id = id;
      fixture.batches.unshift({ id, title: data.title, item_count: data.items.length, created_at: '2026-10-24T10:00:00Z' });
      data.items.forEach((item, i) => fixture.tasks.unshift({ ...clone(fixture.tasks.find(t => t.status === 'COMPLETED')), id: `created${fixture.created}-${String(i).padStart(4, '0')}`, batch_id: id, ticker: item.ticker, fiscal_year: item.target.fiscal_year, metric: item.target.metric, request: item, creator_id: userAtRequest.id, review_status: 'PENDING', reviews: [] }));
      return respond(202, { batch_id: id });
    }
    if ((endpoint === '/batches' || endpoint === '/tasks') && method === 'GET') {
      let items = endpoint === '/batches' ? fixture.batches : fixture.tasks;
      if (fixture.empty) items = [];
      if (endpoint === '/tasks') {
        const batch = url.searchParams.get('batch_id'); if (batch) items = items.filter(t => t.batch_id === batch);
        if (url.searchParams.get('review') === 'true') items = items.filter(t => t.status === 'COMPLETED' && t.review_status === 'PENDING');
      }
      const cursor = url.searchParams.get('before'); const start = cursor ? items.findIndex(t => t.id === cursor) + 1 : 0;
      const limit = Number(url.searchParams.get('limit') || fixture.pageSize); const chunk = clone(items.slice(start, start + limit));
      const result = { items: chunk, next_cursor: start + limit < items.length ? chunk.at(-1)?.id || '' : '' };
      if (endpoint === '/tasks' && fixture.delayNextTaskList) { const ms = fixture.delayNextTaskList; fixture.delayNextTaskList = 0; if (result.items[0]) result.items[0].ticker = 'STALE_OLD_USER'; const delayed = { user: userAtRequest.username, started: Date.now(), finished: null }; fixture.delayedLists.push(delayed); await pause(ms); delayed.finished = Date.now(); }
      return respond(200, result);
    }
    const exportMatch = endpoint.match(/^\/batches\/([^/]+)\/export$/);
    if (exportMatch) return respond(200, { items: fixture.tasks.filter(t => t.batch_id === exportMatch[1] && t.review_status === 'ACCEPTED') });
    const match = endpoint.match(/^\/tasks\/([^/]+)(?:\/(.*))?$/);
    if (match) {
      const task = fixture.tasks.find(t => t.id === match[1]); if (!task) return respond(404, { code: 'not_found' });
      if (match[2] === 'events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': offline fixture\n\n' });
      if (!match[2]) { const snapshot = clone(task); if (fixture.delayNextTaskDetail) { const ms = fixture.delayNextTaskDetail; fixture.delayNextTaskDetail = 0; await pause(ms); } return respond(200, snapshot); }
      const data = body(); fixture.posts.push({ endpoint, body: data });
      if (data.expected_revision !== task.revision) return respond(409, { code: 'revision_conflict' });
      if (match[2] === 'review') { if (task.creator_id === userAtRequest.id || !['ADMIN', 'REVIEWER'].includes(userAtRequest.role)) return respond(403, { code: 'self_review_forbidden' }); task.review_status = data.decision; task.reviews.push({ decision: data.decision, reason: data.reason, created_at: '2026-10-24T10:00:00Z' }); }
      if (match[2] === 'cancel') task.status = 'CANCELLED';
      if (match[2] === 'retry') { if (task.status === 'UNKNOWN' && !data.acknowledge_possible_duplicate) return respond(409, { code: 'acknowledgement_required' }); task.status = 'QUEUED'; }
      task.revision++; return respond(200, { id: task.id });
    }
    return respond(404, { code: 'unhandled_fixture_path', endpoint });
  });
}

async function run() {
  const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/api/')) { res.writeHead(503); return res.end('API requests must be intercepted by offline fixture'); }
    const file = path.resolve(dist, '.' + decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname));
    if (!file.startsWith(dist + path.sep) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream'); res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  let browser, activePage;
  try {
    browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'msedge', headless: true });
    report.browser = browser.version();
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
    const fixture = makeFixture(); await installFixture(context, fixture);
    const page = await context.newPage(); activePage = page; page.on('pageerror', e => report.errors.push(e.message));
    page.setDefaultTimeout(10000);
    async function screenshot(name) { const overlayOpen = await page.locator('dialog[open],.overlay').count(); await page.screenshot({ path: path.join(out, name + '.png'), fullPage: !overlayOpen }); report.screenshots.push(name + '.png'); }
    async function login(username) { await page.getByLabel('用户名', { exact: true }).fill(username); await page.getByLabel('密码', { exact: true }).fill('offline-fixture-password'); await page.getByRole('button', { name: /进入工作台/ }).click(); await page.getByRole('heading', { name: '批次与任务', exact: true }).waitFor(); }
    async function nav(name) { await page.locator('nav').getByRole('button', { name: baseline ? new RegExp(name + '$') : name, exact: !baseline }).click(); await page.getByRole('heading', { name, exact: true }).waitFor(); }
    const modal = () => page.locator('dialog[open],.overlay .dialog').last();
    async function closeModal() { await modal().getByRole('button', { name: '关闭', exact: true }).click(); await modal().waitFor({ state: 'hidden' }); }
    async function fit(label, target = page) {
      const layout = await target.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth, clipped: [...document.querySelectorAll('button,input,select,textarea,a[href],summary')].filter(e => e.checkVisibility() && (!e.matches('.skip-link') || e === document.activeElement)).map(e => ({ e, r: e.getBoundingClientRect() })).filter(({ r }) => r.width && (r.left < -1 || r.right > innerWidth + 1)).map(({ e }) => e.getAttribute('aria-label') || e.textContent.trim().slice(0, 40)) }));
      report.layouts.push({ label, ...layout }); assert.ok(layout.scroll <= layout.width + 1, `${label}: horizontal overflow`); assert.deepEqual(layout.clipped, [], `${label}: clipped controls`);
    }
    async function inspectTicker(ticker) { await page.getByRole('table', { name: '核验任务' }).getByRole('row').filter({ hasText: ticker }).getByRole('button', { name: '查看证据' }).click(); await modal().waitFor(); }
    await page.goto(base); await page.getByRole('heading', { name: '登录 FinReceipts' }).waitFor(); await screenshot('login-1440');
    if (!baseline) {
      assert.equal(await page.getByRole('combobox', { name: '页面字号' }).inputValue(), 'standard');
      await page.getByRole('combobox', { name: '页面字号' }).selectOption('large');
      await page.reload(); await page.getByRole('heading', { name: '登录 FinReceipts' }).waitFor();
      assert.equal(await page.getByRole('combobox', { name: '页面字号' }).inputValue(), 'large');
      assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).fontSize), '18px');
      await page.getByRole('combobox', { name: '页面字号' }).selectOption('standard');
      report.checks.push('font size defaults and selection persist across reload before login');
      for (const width of [1280, 768, 390, 375]) { await page.setViewportSize({ width, height: width < 768 ? 844 : 900 }); await fit('login-' + width); await screenshot('login-' + width); }
      await page.setViewportSize({ width: 1440, height: 900 });
    }
    await login('alpha.analyst'); await screenshot('tasks-1440');
    if (baseline) {
      await page.getByRole('button', { name: /查看证据/ }).first().click(); await screenshot('evidence-1440'); await closeModal();
      await nav('用量与配额'); await screenshot('quota-1440'); await nav('团队与接入'); await screenshot('settings-1440');
      await nav('批次与任务');
      await page.getByRole('button', { name: /新建核验批次/ }).click(); await screenshot('create-1440'); await closeModal();
      await page.setViewportSize({ width: 390, height: 844 }); await nav('批次与任务'); await screenshot('tasks-390'); report.status = 'BASELINE_CAPTURED'; return;
    }
    await fit('tasks-1440');
    const firstRow = await page.getByRole('table', { name: '核验任务' }).locator('tbody tr').first().boundingBox(); assert.ok(firstRow && firstRow.y + firstRow.height <= 900, 'first task row is visible in initial desktop viewport'); report.checks.push('first task row stays above fold with paginated batches');
    report.density = { viewport: { width: 1440, height: 900 }, firstRowTop: firstRow.y, taskRowHeight: firstRow.height, tableWidth: firstRow.width };
    // Paging survives the five-second background refresh, including batches.
    await page.getByRole('button', { name: /加载.*批次|更多批次/ }).filter({ visible: true }).first().click();
    await page.locator('.batch-card').nth(8).waitFor();
    const nineBatchesRow = await page.getByRole('table', { name: '核验任务' }).locator('tbody tr').first().boundingBox(); assert.ok(nineBatchesRow && nineBatchesRow.y + nineBatchesRow.height <= 900); await screenshot('tasks-1440-all-batches'); report.checks.push('all nine batches loaded without pushing tasks below fold');
    await page.getByRole('button', { name: /加载.*任务|更多任务/ }).filter({ visible: true }).first().click();
    await page.getByRole('table', { name: '核验任务' }).locator('tbody tr').nth(6).waitFor();
    const rowsBefore = await page.getByRole('table', { name: '核验任务' }).locator('tbody tr').count(); assert.ok(rowsBefore > 6);
    await pause(5500); assert.equal(await page.getByRole('table', { name: '核验任务' }).locator('tbody tr').count(), rowsBefore); report.checks.push('loaded task and batch pages survive polling');
    const taskTable = page.getByRole('table', { name: '核验任务' });
    const search = page.getByRole('searchbox', { name: '搜索已加载任务' });
    const postsBeforeFilter = fixture.posts.length;
    await search.fill('aapl'); assert.equal(await taskTable.locator('tbody tr').count(), 1); assert.match(await taskTable.locator('tbody').innerText(), /AAPL/);
    await search.fill('营业收入'); assert.equal(await taskTable.locator('tbody tr').count(), rowsBefore);
    await search.fill('task0002'); assert.equal(await taskTable.locator('tbody tr').count(), 1); assert.match(await taskTable.locator('tbody').innerText(), /MSFT/);
    await search.fill('fixture-no-match'); await page.getByRole('heading', { name: '没有匹配的任务' }).waitFor(); assert.equal(await taskTable.locator('tbody tr').count(), 0);
    await page.getByRole('button', { name: '清除筛选', exact: true }).click(); assert.equal(await taskTable.locator('tbody tr').count(), rowsBefore);
    const filterTabs = page.getByRole('group', { name: '筛选已加载任务' });
    for (const [label, states] of [['需关注', ['FAILED', 'UNKNOWN']], ['进行中', ['QUEUED', 'READY', 'RUNNING']], ['已完成', ['COMPLETED']]]) {
      await filterTabs.getByRole('button', { name: new RegExp('^' + label) }).click();
      assert.equal(await taskTable.locator('tbody tr').count(), fixture.tasks.filter(t => states.includes(t.status)).length);
    }
    await filterTabs.getByRole('button', { name: /^全部任务/ }).click(); assert.equal(await taskTable.locator('tbody tr').count(), rowsBefore);
    assert.equal(fixture.posts.length, postsBeforeFilter); report.checks.push('loaded-task search, translated metrics, IDs, empty/reset states and status filters; no mutations');
    await nav('待复核任务'); await screenshot('review-1440'); await nav('批次与任务');
    assert.equal(new URL('http://fixture' + report.requests.filter(r => r.path === '/tasks').at(-1).query).searchParams.get('review'), 'false'); report.checks.push('review navigation resets query context');
    await inspectTicker('AAPL'); await screenshot('evidence-1440');
    const sources = await modal().locator('a[href]').evaluateAll(links => links.map(a => a.href)); assert.ok(sources.includes('https://www.sec.gov/Archives/edgar/data/320193/report.htm')); assert.ok(sources.every(url => new URL(url).protocol === 'https:' && new URL(url).hostname === 'www.sec.gov')); assert.equal(await modal().getByRole('button', { name: '接受并纳入交付' }).count(), 0); report.checks.push('SEC source whitelist and analyst review permissions');
    await page.keyboard.press('Tab'); for (let i = 0; i < 15; i++) { await page.keyboard.press('Tab'); assert.ok(await page.evaluate(() => !!document.querySelector('dialog[open]')?.contains(document.activeElement)), 'focus remains inside modal'); }
    await page.keyboard.press('Escape'); await modal().waitFor({ state: 'hidden' }); assert.match(await page.evaluate(() => document.activeElement?.textContent || ''), /查看证据/); report.checks.push('dialog focus trap, Escape, and trigger restoration');
    await inspectTicker('META'); assert.match(await modal().innerText(), /缺少支持/); assert.match(await modal().innerText(), /已接受/); await screenshot('unsupported-accepted'); await closeModal(); report.checks.push('machine unsupported remains visible after human acceptance');
    await inspectTicker('MSFT'); let confirmSeen = false; page.once('dialog', async d => { confirmSeen = true; await d.dismiss(); }); await modal().getByRole('button', { name: /重试/ }).click(); await pause(100); assert.ok(confirmSeen); assert.equal(fixture.posts.filter(p => p.endpoint.endsWith('/retry')).length, 0);
    page.once('dialog', d => d.accept()); await modal().getByRole('button', { name: /重试/ }).click(); await pause(200); assert.equal(fixture.posts.find(p => p.endpoint.endsWith('/retry')).body.acknowledge_possible_duplicate, true); await closeModal(); report.checks.push('UNKNOWN retry requires explicit duplicate acknowledgement');
    await inspectTicker('NVDA'); await modal().getByRole('button', { name: '取消任务', exact: true }).click(); await pause(150); assert.equal(fixture.tasks.find(t => t.ticker === 'NVDA').status, 'CANCELLED'); await closeModal();
    await page.getByRole('button', { name: /新建核验批次/ }).click(); fixture.rejectNextCreate = true; await modal().getByRole('button', { name: '提交批次', exact: true }).click(); await modal().getByRole('alert').waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('fr:v2:pending:alpha:analyst-id')), null); assert.ok(await modal().getByLabel('批次名称', { exact: true }).isEnabled()); await modal().getByLabel('批次名称', { exact: true }).fill('已修正拒绝提交'); await modal().getByRole('button', { name: '提交批次', exact: true }).click(); await modal().waitFor({ state: 'hidden' }); report.checks.push('confirmed 422 rejection releases pending record for editing');
    // Creation failure retains the exact body and idempotency key across a reload.
    await page.getByRole('button', { name: /新建核验批次/ }).click(); await screenshot('create-1440'); await modal().getByLabel('批次名称', { exact: true }).fill('离线恢复核验批次'); fixture.failNextCreate = true; await modal().getByRole('button', { name: '提交批次', exact: true }).click(); await modal().getByRole('alert').waitFor(); const firstPost = clone(fixture.posts.filter(p => p.endpoint === '/batches').at(-1)); await page.reload(); await page.getByRole('button', { name: /确认原提交|恢复原提交/ }).click(); await pause(300); const recovered = fixture.posts.filter(p => p.endpoint === '/batches').at(-1); assert.deepEqual(recovered.body, firstPost.body); assert.equal(recovered.key, firstPost.key); report.checks.push('pending submission recovers unchanged body and idempotency key');
    // New research and 100-item batch forms are exercised without a real provider.
    await page.getByRole('button', { name: /新建核验批次/ }).click(); await modal().locator('#batch-kind').selectOption('research'); await modal().getByLabel(/研究问题/).fill('解释该年度营收变化并列出证据。'); await modal().getByRole('button', { name: '提交批次', exact: true }).click(); await modal().waitFor({ state: 'hidden' }); assert.equal(fixture.posts.filter(p => p.endpoint === '/batches').at(-1).body.items[0].kind, 'research');
    await page.getByRole('button', { name: /新建核验批次/ }).click(); await modal().getByRole('tab', { name: /批量 JSON/ }).click(); const jsonInput = modal().locator('textarea').first(); await jsonInput.fill('{broken'); const postsBeforeInvalid = fixture.posts.length; await modal().getByRole('button', { name: '提交批次', exact: true }).click(); await modal().locator('#batch-json-error').waitFor(); assert.equal(fixture.posts.length, postsBeforeInvalid); await jsonInput.fill('[{}]'); await modal().getByRole('button', { name: '提交批次', exact: true }).click(); await modal().locator('#batch-json-error').waitFor(); assert.equal(fixture.posts.length, postsBeforeInvalid); const hundred = Array.from({ length: 100 }, () => ({ kind: 'metric', ticker: 'AAPL', target: { kind: 'direct', metric: 'revenue', fiscal_year: 2023 } })); await jsonInput.fill(JSON.stringify(hundred)); await modal().getByRole('button', { name: '提交批次', exact: true }).click(); await modal().waitFor({ state: 'hidden' }); assert.equal(fixture.posts.filter(p => p.endpoint === '/batches').at(-1).body.items.length, 100); report.checks.push('research and 100-item JSON forms, malformed JSON and [{}] send no request');
    await screenshot('batch-100-items');
    // Independent fixture scenario: remove generated fixture rows before checking historic role boundaries.
    fixture.tasks = fixture.tasks.filter(t => !t.id.startsWith('created')); fixture.batches = fixture.batches.filter(b => !b.id.startsWith('created'));
    await nav('用量与配额'); await screenshot('quota-1440'); await fit('quota-1440');
    await nav('团队与接入'); await page.getByRole('button', { name: /生成.*7.*天令牌/ }).click(); await page.getByText('offline_fixture_token_only').waitFor(); await screenshot('settings-1440'); assert.equal(await page.getByRole('heading', { name: '新增组织成员' }).count(), 0); report.checks.push('token generation and analyst member-management boundary');
    await page.getByRole('button', { name: '退出登录', exact: true }).click(); await login('alpha.reviewer'); await nav('待复核任务'); await inspectTicker('CRM'); assert.equal(await modal().getByRole('button', { name: '接受并纳入交付' }).count(), 0); await closeModal();
    await inspectTicker('AAPL'); const beforeEmptyReason = fixture.posts.length; await modal().getByRole('button', { name: '接受并纳入交付', exact: true }).click(); await modal().locator('#review-reason-error').waitFor(); assert.equal(fixture.posts.length, beforeEmptyReason); await modal().getByLabel('复核理由', { exact: true }).fill('已逐项核对年度、单位与 SEC 原始披露。'); await modal().getByRole('button', { name: '接受并纳入交付', exact: true }).click(); await pause(250); assert.equal(fixture.tasks.find(t => t.ticker === 'AAPL').review_status, 'ACCEPTED'); await screenshot('reviewed-1440'); await closeModal(); report.checks.push('reviewer cannot self-review; empty reason sends no request; accepts another creator task');
    await inspectTicker('AMD'); await modal().getByLabel('复核理由', { exact: true }).fill('本次结果需要进一步研究，暂不纳入交付。'); await modal().getByRole('button', { name: '驳回当前结果', exact: true }).click(); await pause(250); assert.equal(fixture.tasks.find(t => t.ticker === 'AMD').review_status, 'REJECTED'); assert.equal(fixture.tasks.find(t => t.ticker === 'AMD').machine_verdict, 'SUPPORTED'); await screenshot('rejected-1440'); await closeModal(); report.checks.push('human rejection leaves machine verdict unchanged');
    await nav('批次与任务'); await page.locator('.batch-card').filter({ hasText: '年度营收核验 1' }).click(); const downloadEvent = page.waitForEvent('download'); await page.getByRole('button', { name: '导出已审核结果', exact: true }).click(); const downloaded = await downloadEvent; await downloaded.saveAs(path.join(out, 'approved-fixture.json')); const exported = JSON.parse(fs.readFileSync(path.join(out, 'approved-fixture.json'))); assert.ok(exported.items.length); assert.ok(exported.items.every(t => t.review_status === 'ACCEPTED')); report.checks.push('export includes accepted result versions only');
    await page.getByRole('button', { name: '退出登录', exact: true }).click(); await login('alpha.admin'); await nav('团队与接入'); await page.getByLabel('用户名', { exact: true }).fill('new.analyst'); await page.getByLabel('初始密码', { exact: true }).fill('fixture-password-123'); await page.getByLabel('角色', { exact: true }).selectOption('ANALYST'); await page.getByRole('button', { name: '创建成员', exact: true }).click(); await pause(200); assert.equal(fixture.posts.find(p => p.endpoint === '/members').body.username, 'new.analyst'); assert.equal(await page.getByLabel('初始密码', { exact: true }).inputValue(), ''); await screenshot('settings-admin-1440'); report.checks.push('admin member creation clears password');
    await nav('批次与任务');
    await page.getByRole('button', { name: '退出登录', exact: true }).click({ trial: true });
    fixture.delayNextTaskList = 2200;
    const oldRefresh = page.waitForRequest(r => new URL(r.url()).pathname === '/api/v2/tasks' && r.method() === 'GET');
    await oldRefresh;
    await page.getByRole('button', { name: '退出登录', exact: true }).click();
    await login('alpha.analyst'); const newIdentityReadyAt = Date.now(); await pause(2500);
    assert.equal(fixture.delayedLists.at(-1).user, 'alpha.admin'); assert.ok(fixture.delayedLists.at(-1).finished > newIdentityReadyAt, 'old response arrives after new identity is active');
    assert.equal(await page.getByText('STALE_OLD_USER', { exact: false }).count(), 0);
    report.checks.push('late old-identity polling response cannot replace new-session data');
    for (const width of [1440, 1280, 768, 390, 375]) {
      await page.setViewportSize({ width, height: width < 768 ? 844 : 900 }); await fit('tasks-' + width); await screenshot('tasks-' + width); assert.ok(await page.getByRole('button', { name: '退出登录', exact: true }).isVisible());
      await page.getByRole('button', { name: '查看证据', exact: true }).first().click(); await modal().waitFor(); await fit('evidence-' + width); await screenshot('evidence-' + width); await closeModal();
      await page.getByRole('button', { name: /新建核验批次/ }).click(); await fit('create-' + width); await screenshot('create-' + width); await closeModal();
      await nav('用量与配额'); await fit('quota-' + width); await screenshot('quota-' + width); await nav('团队与接入'); await fit('settings-' + width); await screenshot('settings-' + width); await nav('批次与任务');
    }
    report.checks.push('all target widths preserve controls, account access, and page layout');
    for (const [size, rootSize, widths] of [['large', 18, [1440, 1024, 375]], ['xlarge', 20, [1440, 1280, 1024, 768, 390, 375]]]) {
      await page.getByRole('combobox', { name: '页面字号' }).selectOption(size);
      assert.equal(await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize)), rootSize);
      assert.equal(await page.evaluate(() => localStorage.getItem('fr:ui:text-size')), size);
      for (const width of widths) {
        await page.setViewportSize({ width, height: width < 768 ? 844 : 900 });
        await fit('font-' + size + '-tasks-' + width); await screenshot('font-' + size + '-tasks-' + width);
        const taskTextSize = await page.locator('.task-identity strong').filter({ visible: true }).first().evaluate(e => parseFloat(getComputedStyle(e).fontSize));
        assert.ok(taskTextSize >= rootSize * .9375, 'task labels grow with selected font size');
        await page.getByRole('button', { name: '查看证据', exact: true }).filter({ visible: true }).first().click(); await modal().waitFor(); await fit('font-' + size + '-evidence-' + width); await screenshot('font-' + size + '-evidence-' + width); await closeModal();
        await page.getByRole('button', { name: /新建核验批次/ }).click(); await fit('font-' + size + '-create-' + width); await screenshot('font-' + size + '-create-' + width); await closeModal();
        for (const [label, name] of [['用量与配额', 'quota'], ['团队与接入', 'settings']]) { await nav(label); await fit('font-' + size + '-' + name + '-' + width); await screenshot('font-' + size + '-' + name + '-' + width); }
        await nav('批次与任务');
      }
    }
    await page.reload(); await page.getByRole('heading', { name: '批次与任务', exact: true }).waitFor(); assert.equal(await page.getByRole('combobox', { name: '页面字号' }).inputValue(), 'xlarge');
    await page.getByRole('combobox', { name: '页面字号' }).selectOption('standard');
    report.checks.push('larger and largest text sizes across pages, dialogs and 375–1440px; persistence after login');
    await page.setViewportSize({ width: 1440, height: 900 }); await page.emulateMedia({ reducedMotion: 'reduce' }); await screenshot('reduced-motion'); report.checks.push('reduced-motion layout');
    fixture.empty = true; await nav('待复核任务'); await screenshot('empty-review'); await fit('empty-review'); fixture.empty = false;
    // Real Chromium browser zoom in a NEW isolated profile, not CSS zoom or DPR emulation.
    // Reuses the partition preference format already verified by browser_ui_acceptance.cjs.
    const zoomProfile = path.join(out, 'zoom-profile'); fs.mkdirSync(path.join(zoomProfile, 'Default'), { recursive: true });
    fs.writeFileSync(path.join(zoomProfile, 'Default', 'Preferences'), JSON.stringify({ partition: { default_zoom_level: { x: Math.log(2) / Math.log(1.2) } } }));
    const zoomContext = await chromium.launchPersistentContext(zoomProfile, { channel: process.env.BROWSER_CHANNEL || 'msedge', headless: true, viewport: null, args: ['--window-size=1440,1000'] });
    try {
      const zoomFixture = makeFixture(); await installFixture(zoomContext, zoomFixture);
      const zoomPage = zoomContext.pages()[0]; zoomPage.on('pageerror', e => report.errors.push(e.message));
      const cdp = await zoomContext.newCDPSession(zoomPage);
      async function nativeScreen(name) { await fit('native200-' + name, zoomPage); const metrics = await cdp.send('Page.getLayoutMetrics'); assert.equal(metrics.cssVisualViewport.zoom, 2); const inModal = await zoomPage.locator('dialog[open]').count(); const captured = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true, ...(inModal ? {} : { captureBeyondViewport: true, clip: { ...metrics.contentSize, scale: 1 } }) }); fs.writeFileSync(path.join(out, 'native200-' + name + '.png'), Buffer.from(captured.data, 'base64')); report.screenshots.push('native200-' + name + '.png'); }
      await zoomPage.goto(base); await zoomPage.getByRole('heading', { name: '登录 FinReceipts' }).waitFor();
      const measured = await zoomPage.evaluate(() => ({ innerWidth, outerWidth, devicePixelRatio })); assert.equal(measured.devicePixelRatio, 2); assert.ok(measured.innerWidth < measured.outerWidth * .55);
      await nativeScreen('login'); await zoomPage.getByLabel('用户名', { exact: true }).fill('alpha.analyst'); await zoomPage.getByLabel('密码', { exact: true }).fill('offline-fixture-password'); await zoomPage.getByRole('button', { name: /进入工作台/ }).click(); await zoomPage.getByRole('heading', { name: '批次与任务', exact: true }).waitFor(); await nativeScreen('tasks');
      await zoomPage.getByRole('button', { name: '查看证据', exact: true }).first().click(); await zoomPage.locator('dialog[open]').waitFor(); await nativeScreen('evidence'); await zoomPage.locator('dialog[open]').getByRole('button', { name: '关闭', exact: true }).click();
      await zoomPage.getByRole('button', { name: /新建核验批次/ }).click(); await zoomPage.locator('dialog[open]').waitFor(); await nativeScreen('create'); await zoomPage.locator('dialog[open]').getByRole('button', { name: '关闭', exact: true }).click();
      for (const [label, name] of [['用量与配额', 'quota'], ['团队与接入', 'settings']]) { await zoomPage.locator('nav').getByRole('button', { name: label, exact: true }).click(); await zoomPage.getByRole('heading', { name: label, exact: true }).waitFor(); await nativeScreen(name); }
      report.native_zoom = { status: 'PASS', ...measured, cssVisualViewportZoom: 2 }; report.checks.push('native 200% browser zoom, six page/modal layouts, DPR and CDP zoom verified');
    } finally { await zoomContext.close(); }
    // Computed text/background pairs, including the muted helper copy.
    report.contrast = await page.evaluate(() => {
      function rgb(value) { const m = value.match(/[\d.]+/g); return m ? m.slice(0, 3).map(Number) : [255, 255, 255]; }
      function lum(color) { return color.map(x => { x /= 255; return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }).reduce((n, x, i) => n + x * [0.2126, 0.7152, 0.0722][i], 0); }
      return [...document.querySelectorAll('h1,h2,p,label,small,nav button')].filter(e => e.checkVisibility() && e.textContent.trim() && !e.disabled).slice(0, 40).map(e => { const c = getComputedStyle(e); let ancestor = e; let bg; do { bg = getComputedStyle(ancestor).backgroundColor; ancestor = ancestor.parentElement; } while (ancestor && (bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent')); const a = lum(rgb(c.color)), b = lum(rgb(bg)); return { text: e.textContent.trim().slice(0, 50), foreground: c.color, background: bg, ratio: +((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)).toFixed(2) }; });
    });
    assert.ok(report.contrast.every(c => c.ratio >= 4.5), 'sampled normal text meets 4.5:1 contrast'); report.checks.push('computed normal-text contrast samples >= 4.5:1');
    assert.deepEqual(report.errors, []);
    assert.equal(manifest(dist, ['.']).sha256, report.binding.build.sha256, 'served build must not change during acceptance');
    assert.equal(manifest(path.join(root, 'console'), ['src', 'vite.config.ts', 'index.html', 'package.json', 'package-lock.json']).sha256, report.binding.source.sha256, 'source must not change during acceptance');
    report.status = 'PASS';
  } catch (error) { report.failure = error.stack; if (activePage) { report.failure_ui = await activePage.locator('body').innerText().catch(() => 'page unavailable'); await activePage.screenshot({ path: path.join(out, 'failure.png'), fullPage: true }).catch(() => {}); } throw error; }
  finally { fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2)); await browser?.close(); await new Promise(resolve => server.close(resolve)); console.log(JSON.stringify({ status: report.status, output: out, checks: report.checks.length, native_zoom: report.native_zoom.status })); }
}
run().catch(error => { console.error(error.stack); process.exitCode = 1; });
