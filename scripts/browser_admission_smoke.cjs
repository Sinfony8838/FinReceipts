// Requires the local controlled server from admission_load.serve with model hold armed.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const out = process.argv[2];
  if (!out) throw new Error('Usage: node scripts/browser_admission_smoke.cjs NEW_OUTPUT_DIRECTORY');
  fs.mkdirSync(out, { recursive: false });
  const base = process.env.FINRECEIPTS_TEST_URL;
  if (!base) throw new Error('FINRECEIPTS_TEST_URL must point to the controlled local server');
  const browser = await chromium.launch({ headless: true, channel: process.env.BROWSER_CHANNEL || 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/workbench');
    await page.locator('#create').click();
    await page.locator('#status').filter({ hasText: '待继续' }).waitFor();
    const runId = new URL(page.url()).hash.slice(1);
    await page.locator('#step').click();
    await page.waitForFunction(async id => {
      const run = await (await fetch('/api/runs/' + id)).json();
      return run.status === 'running' && run.dispatches === 1;
    }, runId);
    // Reload disconnects the pending UI request; the worker is still blocked.
    await page.reload();
    await page.locator('#status').filter({ hasText: '执行中' }).waitFor();
    await page.locator('#cancel').click();
    await page.locator('#status').filter({ hasText: '已取消' }).waitFor();
    assert.equal(await page.locator('#dispatches').textContent(), '1 / 6');
    const health = await (await page.request.get(base + '/health')).json();
    assert.equal(health.execution.active, 1);
    await page.goto(base + '/');
    await page.locator('#exAapl').click();
    const waitResponse = page.waitForResponse(r => r.url() === base + '/api/ask');
    await page.locator('#btnAsk').click();
    const response = await waitResponse;
    assert.equal(response.status(), 503);
    const data = await response.json();
    assert.equal(data.code, 'capacity_exhausted');
    await page.waitForFunction(id => document.querySelector('#error').textContent.includes(id), data.request_id);
    assert.match(await page.locator('#error').textContent(), /当前执行名额已满/);
    assert.equal(await page.locator('#ask-status').textContent(), '未完成');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(out, 'overload-mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    const summary = { status: 'PASS', scope: 'real Edge; controlled fake model held in flight', runId,
      checks: ['advance', 'reload while model blocked', 'cancel remains available', 'reservation retained',
        'capacity held after cancellation', 'overload notice with request ID', 'mobile no horizontal overflow'],
      rejectedRequestId: data.request_id, errors };
    fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify(summary, null, 2));
    console.log(JSON.stringify(summary));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
