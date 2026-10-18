// Real browser/API errors. Run against an offline fake-model server with no large model.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const out = process.argv[2];
  if (!out) throw new Error('Usage: node scripts/browser_diagnostics_smoke.cjs NEW_OUTPUT_DIRECTORY');
  fs.mkdirSync(out, { recursive: false });
  const base = process.env.FINRECEIPTS_TEST_URL || 'http://127.0.0.1:8793';
  const browser = await chromium.launch({ headless: true, channel: process.env.BROWSER_CHANNEL || 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  const errors = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/');
    await page.locator('#exAapl').click();
    await page.locator('.advanced > summary').click();
    await page.locator('#mode').selectOption('cascade');
    const oneResponse = page.waitForResponse(r => r.url() === base + '/api/ask');
    await page.locator('#btnAsk').click();
    const once = await oneResponse;
    assert.equal(once.status(), 400);
    const oneId = once.headers()['x-request-id'];
    await page.waitForFunction(id => document.querySelector('#error').textContent.includes(id), oneId);
    assert.match(oneId, /^[a-f0-9]{32}$/);
    checks.push({ path: '/api/ask', requestId: oneId, status: once.status(), code: (await once.json()).code });
    const streamResponse = page.waitForResponse(r => r.url() === base + '/api/ask/stream');
    await page.locator('#btnStream').click();
    const stream = await streamResponse;
    assert.equal(stream.status(), 200);
    const streamId = stream.headers()['x-request-id'];
    await page.waitForFunction(id => document.querySelector('#error').textContent.includes(id), streamId);
    assert.equal(await page.locator('#ask-status').textContent(), '未完成');
    assert.match(await page.locator('#error').textContent(), /尚未配置升级模型/);
    assert.notEqual(streamId, oneId);
    checks.push({ path: '/api/ask/stream', requestId: streamId, status: stream.status(), code: 'cascade_unconfigured' });
    await page.screenshot({ path: path.join(out, 'ask-error.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(out, 'ask-error-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1200, height: 900 });

    await page.goto(base + '/workbench');
    await page.locator('#create').click();
    await page.locator('#status').filter({ hasText: '待继续' }).waitFor();
    const id = new URL(page.url()).hash.slice(1);
    const doc = await (await page.request.get(base + '/api/runs/' + id)).json();
    const cancelled = await page.request.post(base + '/api/runs/' + id + '/cancel', { data: { expected_revision: doc.revision } });
    assert.equal(cancelled.status(), 200);
    const staleResponse = page.waitForResponse(r => r.url().endsWith('/advance'));
    await page.locator('#step').click();
    const stale = await staleResponse;
    assert.equal(stale.status(), 409);
    const staleId = stale.headers()['x-request-id'];
    await page.waitForFunction(id => document.querySelector('#error').textContent.includes(id), staleId);
    checks.push({ path: '/api/runs/{run_id}/advance', requestId: staleId, status: stale.status(), code: (await stale.json()).code });
    await page.screenshot({ path: path.join(out, 'workbench-error.png'), fullPage: true });
    await page.locator('#refresh').click();
    await page.locator('#status').filter({ hasText: '已取消' }).waitFor();
    assert.equal(await page.locator('#error').textContent(), '');
    assert.equal(await page.locator('#dispatches').textContent(), '0 / 6');
    assert.deepEqual(errors, []);
    const summary = { status: 'PASS', scope: 'real Edge + offline fake API; HTTP error, SSE error, stale revision and refresh', checks, errors };
    fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify(summary, null, 2));
    console.log(JSON.stringify(summary));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
