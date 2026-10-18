// Actual Edge + fake server with 1 second model latency and a 0.5 second HTTP deadline.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const out = process.argv[2];
  if (!out) throw new Error('Pass a NEW output directory');
  fs.mkdirSync(out, { recursive: false });
  const base = process.env.FINRECEIPTS_TEST_URL;
  if (!base || new URL(base).hostname !== '127.0.0.1') throw new Error('Expected isolated loopback server');
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  const errors = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base);
    await page.locator('#exAapl').click();
    await page.locator('.advanced > summary').click();
    await page.locator('#mode').selectOption('baseline');
    for (const [button, endpoint, status] of [['#btnAsk','/api/ask',504], ['#btnStream','/api/ask/stream',200]]) {
      const until = Date.now() + 10000;
      while ((await (await page.request.get(base + '/health')).json()).execution.active !== 0) {
        assert.ok(Date.now() < until, 'model slot was not released');
        await page.waitForTimeout(100);
      }
      const pending = page.waitForResponse(r => r.url() === base + endpoint);
      await page.locator(button).click();
      const response = await pending;
      assert.equal(response.status(), status);
      const id = response.headers()['x-request-id'];
      await page.waitForFunction(id => document.querySelector('#error').textContent.includes(id), id);
      assert.match(await page.locator('#error').textContent(), /后台工作可能仍在继续/);
      assert.equal(await page.locator('#ask-status').textContent(), '未完成');
      checks.push({ endpoint, status, code: 'response_timeout', request_id: id });
    }
    await page.screenshot({ path: path.join(out, 'timeout-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(out, 'timeout-mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    const summary = { status: 'PASS', scope: 'real Edge with offline fake server', checks, errors };
    fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify(summary, null, 2));
    console.log(JSON.stringify(summary));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
