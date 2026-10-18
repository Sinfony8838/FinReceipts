// Optional browser acceptance: install Playwright separately or set NODE_PATH.
// Start the API with offline fixtures + fake LLM first. No provider credentials.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');

(async () => {
  const out = process.argv[2];
  if (!out) throw new Error('Usage: node scripts/browser_runtime_smoke.cjs OUTPUT_DIRECTORY');
  fs.mkdirSync(out, { recursive: false });
  const browser = await chromium.launch({ headless: true, channel: process.env.BROWSER_CHANNEL || 'msedge' });
  const base = process.env.FINRECEIPTS_TEST_URL || 'http://127.0.0.1:8791';
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto(base + '/workbench');
    await page.getByText('本地演示', { exact: true }).waitFor();
    await page.getByRole('button', { name: '创建任务', exact: true }).click();
    await page.locator('#status').filter({ hasText: '待继续' }).waitFor();
    const runUrl = page.url();
    await page.getByRole('button', { name: '推进一步', exact: true }).click();
    await page.locator('#next').filter({ hasText: '核验数字' }).waitFor();
    assert.equal(await page.locator('#dispatches').textContent(), '1 / 6');
    await page.reload();
    await page.locator('#next').filter({ hasText: '核验数字' }).waitFor();
    assert.equal(await page.locator('#dispatches').textContent(), '1 / 6');
    await page.getByRole('button', { name: '继续执行', exact: true }).click();
    await page.locator('#status').filter({ hasText: '已完成' }).waitFor();
    assert.equal(await page.locator('#dispatches').textContent(), '2 / 6');
    await page.getByRole('button', { name: '复核当前回答', exact: true }).click();
    await page.locator('#comparison table').waitFor();
    assert.match(await page.locator('#comparison').innerText(), /未支持/);
    assert.equal(await page.locator('#comparison tbody tr').count(), 2);
    assert.equal(await page.locator('#error').textContent(), '');
    await page.screenshot({ path: path.join(out, 'desktop.png'), fullPage: true });
    const eventCount = await page.locator('#events li').count();
    const href = await page.locator('#export').getAttribute('href');
    const exported = await page.request.get(new URL(href, page.url()).href);
    assert.equal(exported.status(), 200);
    const exportPath = path.resolve(out, 'export.json');
    // Preserve HTTP bytes: JSON.stringify turns Python 0.0 into 0 and breaks the hash codec.
    fs.writeFileSync(exportPath, await exported.body());
    const root = path.resolve(__dirname, '..');
    const python = process.env.FINRECEIPTS_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    const verification = spawnSync(python, ['-c',
      'import json,sys; from finreceipts.runtime.store import verify_export; verify_export(json.load(open(sys.argv[1],encoding="utf-8")))',
      exportPath], {cwd:root,windowsHide:true,encoding:'utf8',timeout:10000});
    assert.equal(verification.status, 0, verification.stderr || 'export verification failed');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await page.locator('#status').filter({ hasText: '已完成' }).waitFor();
    await page.waitForFunction(n => document.querySelectorAll('#events li').length === n, eventCount);
    assert.equal(await page.locator('#dispatches').textContent(), '2 / 6');
    await page.screenshot({ path: path.join(out, 'mobile.png'), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.locator('#settings > summary').click();
    await page.getByRole('button', { name: '创建任务', exact: true }).click();
    await page.locator('#status').filter({ hasText: '待继续' }).waitFor();
    await page.getByRole('button', { name: '取消任务', exact: true }).click();
    await page.locator('#status').filter({ hasText: '已取消' }).waitFor();
    assert.equal(await page.locator('#dispatches').textContent(), '0 / 6');
    assert.deepEqual(errors, []);
    const summary = { status: 'PASS', runUrl, eventCount, exportIntegrity: 'PASS', desktop: '1440x1050', mobile: '390x844',
      checks: ['create', 'advance', 'reload without recall', 'revision loop', 'counter-audit',
        'event catch-up', 'JSON export', 'mobile no horizontal overflow', 'cancel before dispatch'],
      scope: 'real Chromium UI with fake model and offline SEC fixtures', errors };
    fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify(summary, null, 2));
    console.log(JSON.stringify(summary));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
