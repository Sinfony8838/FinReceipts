// Run against the offline fake-model demo. Writes only to a NEW evidence directory.
// npm Playwright is supplied by the caller; no package is added to the application.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');

(async () => {
  const out = process.argv[2];
  if (!out) throw new Error('Usage: node scripts/browser_ui_acceptance.cjs NEW_OUTPUT_DIRECTORY');
  fs.mkdirSync(out, {recursive: false});
  const base = process.env.FINRECEIPTS_TEST_URL || 'http://127.0.0.1:8791';
  const channel = process.env.BROWSER_CHANNEL || 'msedge';
  const browser = await chromium.launch({channel, headless: true});
  const errors = [], checks = [], layouts = [], contrast = [];
  let completed;
  const ctx = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await ctx.newPage();
  page.on('pageerror', error => errors.push(error.message));
  const save = name => page.screenshot({path: path.join(out, name + '.png'), fullPage: true});
  async function fit(p, label) {
    const result = await p.evaluate(() => {
      const clipped = [];
      for (const element of document.querySelectorAll('button, input, textarea, select, nav a')) {
        if (!element.checkVisibility()) continue;
        const r = element.getBoundingClientRect();
        if (r.width && (r.left < -1 || r.right > innerWidth + 1)) clipped.push(element.id || element.textContent);
      }
      return {width: innerWidth, scroll: document.documentElement.scrollWidth, clipped};
    });
    assert.ok(result.scroll <= result.width, label + ' horizontal overflow ' + JSON.stringify(result));
    assert.deepEqual(result.clipped, [], label + ' clipped controls');
    layouts.push({label, ...result});
  }
  async function text(p, selector, expected) {
    await p.waitForFunction(({selector, expected}) => document.querySelector(selector)?.textContent === expected, {selector, expected});
  }
  async function getRun(id) { return (await page.request.get(base + '/api/runs/' + id)).json(); }
  async function create(extra = {}) {
    const r = await page.request.post(base + '/api/runs', {headers: {'Idempotency-Key': randomUUID()},
      data: {question: '苹果（AAPL）2023 财年的营业收入是多少？', ticker: 'AAPL', fiscal_years: [2023], max_dispatches: 6, max_revisions: 2, ...extra}});
    assert.equal(r.status(), 201); return r.json();
  }
  async function openRun(doc) {
    const url = base + '/workbench#' + doc.run_id;
    if (page.url() === url) await page.reload();
    else await page.goto(url);
    await text(page, '#status', {ready:'待继续',completed:'已完成',running:'执行中',interrupted:'已中断',cancelled:'已取消',budget_exhausted:'额度已用尽'}[doc.status]);
  }
  try {
    const health = await (await page.request.get(base + '/health')).json();
    assert.equal(health.provider, 'fake'); assert.equal(health.offline, true);
    await page.goto(base + '/workbench');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
    // Real local API: create, run, counter-audit, budget exhaustion, cancellation.
    await page.locator('#create').click(); await text(page, '#status', '待继续');
    const id = page.url().split('#')[1];
    await page.locator('#run').click(); await text(page, '#status', '已完成');
    await page.locator('#compare').click(); await page.locator('#comparison table').waitFor();
    completed = await getRun(id);
    assert.equal(completed.dispatches, 2);
    await page.locator('.skip').focus(); await page.keyboard.press('Enter');
    assert.equal(page.url().split('#')[1], id, 'skip link must preserve the task fragment');
    assert.equal(await page.locator('#main').evaluate(e => e === document.activeElement), true);
    assert.equal(await page.locator('#recovery').isVisible(), false);
    assert.equal(await page.locator('#cancel').isVisible(), false);
    checks.push('real API completion, revision loop and counter-audit');

    // Both pages and completed results: every requested width in every preference.
    for (const route of ['workbench-empty', 'ask-empty', 'workbench-result']) {
      await page.goto(base + (route === 'ask-empty' ? '/' : '/workbench' + (route === 'workbench-result' ? '#' + id : '')));
      if (route === 'workbench-result') {
        await text(page, '#status', '已完成'); await page.locator('#compare').click(); await page.locator('#comparison table').waitFor();
      }
      for (const preference of ['light', 'dark', 'system']) {
        await page.emulateMedia({colorScheme:'dark'});
        await page.locator('#theme').selectOption(preference);
        assert.equal(await page.locator('html').getAttribute('data-theme'), preference === 'light' ? 'light' : 'dark');
        for (const width of [1440,1280,900,640,390]) {
          await page.setViewportSize({width, height:1000});
          await fit(page, route + '-' + preference + '-' + width);
          if (route === 'workbench-result') {
            await page.waitForFunction(open => document.querySelector('#settings').open === open, width >= 960);
            assert.equal(await page.locator('#settings').getAttribute('open') !== null, width >= 960);
            if (width < 960) {
              const settings = await page.locator('#settings').boundingBox(), answer = await page.locator('#answer').boundingBox();
              assert.ok(settings.y > answer.y, 'mobile results must precede settings');
            }
          }
          if ([1440,390].includes(width) && preference !== 'system') await save(route + '-' + preference + '-' + width);
        }
      }
    }
    checks.push('45 layout cases: 5 widths x 3 preferences x 3 page states');

    // System changes, reload, cross-page preference, and storage denied.
    await page.locator('#theme').selectOption('system');
    await page.emulateMedia({colorScheme:'light'});
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await page.emulateMedia({colorScheme:'dark'});
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await page.reload(); assert.equal(await page.locator('#theme').inputValue(), 'system');
    await page.locator('nav a[href="/"]').click();
    assert.equal(await page.locator('#theme').inputValue(), 'system');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await page.locator('#theme').selectOption('light'); await page.reload();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
    await page.locator('#theme').selectOption('dark');
    await page.locator('nav a[href="/workbench"]').click();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    const noStorage = await browser.newContext();
    await noStorage.addInitScript(() => {
      Storage.prototype.getItem = () => { throw new Error('test: storage denied'); };
      Storage.prototype.setItem = () => { throw new Error('test: storage denied'); };
    });
    const denied = await noStorage.newPage();
    denied.on('pageerror', error => errors.push(error.message));
    await denied.goto(base + '/workbench');
    assert.equal(await denied.locator('html').getAttribute('data-theme'), 'light');
    await denied.locator('#theme').selectOption('dark');
    assert.equal(await denied.locator('html').getAttribute('data-theme'), 'dark');
    await noStorage.close();
    checks.push('theme persistence, navigation, live system changes, denied storage');

    let limited = await create({max_dispatches:1});
    await openRun(limited); await page.locator('#run').click(); await text(page, '#status', '额度已用尽');
    limited = await getRun(limited.run_id); assert.equal(limited.dispatches, 1);
    await save('budget-exhausted-real');
    const cancelled = await create();
    await openRun(cancelled); await page.locator('#cancel').click(); await text(page, '#status', '已取消');
    assert.equal((await getRun(cancelled.run_id)).dispatches, 0); await save('cancelled-real');
    checks.push('real API budget exhaustion and cancellation');

    // Explicit UI-only fixtures. No real run/database is overwritten.
    const fixtureId = 'f'.repeat(32);
    let fixture, resumed = false;
    await page.route(base + '/api/runs/' + fixtureId + '**', async route => {
      const url = route.request().url();
      if (url.endsWith('/events')) return route.fulfill({contentType:'text/event-stream', body:''});
      if (url.endsWith('/resume')) {
        assert.deepEqual(route.request().postDataJSON(), {expected_revision: fixture.revision, acknowledge_possible_duplicate:true});
        resumed = true; fixture.status = 'ready'; fixture.lease = null;
      }
      return route.fulfill({json: fixture});
    });
    for (const state of ['running','interrupted','expired']) {
      fixture = structuredClone(completed);
      Object.assign(fixture, {run_id:fixtureId, status:state === 'expired' ? 'running' : state, next_node:'answer',
        stop_reason:state === 'interrupted' ? 'step_failed' : null,
        lease:state === 'interrupted' ? null : {expires:Date.now()/1000 + (state === 'expired' ? -60 : 600)}});
      fixture.request.question = '测试夹具：' + '很长的财务问题用于检查自动换行。'.repeat(10);
      fixture.state.answer = '测试夹具：' + 'unbroken-long-evidence-token'.repeat(35);
      for (const width of [1280,390]) for (const theme of ['light','dark']) {
        await page.setViewportSize({width,height:1000}); await openRun(fixture);
        await page.locator('#theme').selectOption(theme);
        await fit(page, 'fixture-' + state + '-' + width + '-' + theme);
        assert.equal(await page.locator('#recovery').isVisible(), state !== 'running');
        assert.equal(await page.locator('#cancel').isVisible(), true);
        if (state !== 'running') assert.equal(await page.locator('#resume').isDisabled(), true);
        if (width === 390) await save('fixture-' + state + '-' + theme);
      }
    }
    await page.locator('#ack').check(); await page.locator('#resume').click(); await text(page, '#status', '待继续');
    assert.equal(resumed, true); assert.equal(await page.locator('#recovery').isVisible(), false);
    await page.unroute(base + '/api/runs/' + fixtureId + '**');
    checks.push('UI-only marked fixtures: running, interrupted, expired lease, acknowledged resume payload, long text');

    // Streaming and one-shot ask preserve the white, original report document.
    await page.goto(base + '/'); await page.locator('#exAapl').click(); await page.locator('#btnStream').click();
    await text(page, '#ask-status', '已完成'); await page.locator('#report iframe').waitFor();
    assert.match(await page.frameLocator('#report iframe').locator('body').innerText(), /383.3/);
    await save('ask-stream-result-dark');
    assert.equal(await page.locator('#report iframe').evaluate(e => getComputedStyle(e).colorScheme), 'light');
    await page.locator('#exNvda').click(); await page.locator('#btnAsk').click(); await text(page, '#ask-status', '已完成');
    assert.equal(await page.locator('#error').textContent(), '');
    await fit(page, 'ask-report-mobile');
    await page.setViewportSize({width:1440,height:1000}); await page.locator('#theme').selectOption('light'); await save('ask-result-light');
    checks.push('real API streaming Apple and one-shot NVIDIA, report retained');

    // Keyboard reachability, visible focus, hidden advanced validation.
    await page.goto(base + '/workbench'); await page.keyboard.press('Tab');
    assert.equal(await page.locator('.skip').evaluate(e => e === document.activeElement), true);
    await page.keyboard.press('Tab');
    assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
    await page.locator('.advanced > summary').focus(); await page.keyboard.press('Enter');
    assert.equal(await page.locator('.advanced').getAttribute('open') !== null, true);
    await page.locator('#budget').fill('0'); await page.locator('.advanced > summary').click(); await page.locator('#create').click();
    assert.equal(await page.locator('.advanced').getAttribute('open') !== null, true);
    assert.equal(await page.locator('#budget').evaluate(e => e.validity.rangeUnderflow), true);
    checks.push('keyboard focus, details activation, invalid advanced input is revealed');

    // Contrast of actual computed theme tokens (normal text minimum 4.5:1).
    for (const theme of ['light','dark']) {
      await page.locator('#theme').selectOption(theme);
      const ratios = await page.evaluate(() => {
        const vars = getComputedStyle(document.documentElement);
        function rgb(name) {
          const value = name.startsWith('--') ? vars.getPropertyValue(name).trim() : name;
          const s = document.createElement('span'); s.style.color = value; document.body.append(s);
          const c = getComputedStyle(s).color.match(/\d+/g).slice(0,3).map(Number); s.remove(); return c;
        }
        const lum = c => c.map(v => { v /= 255; return v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4; }).reduce((s,v,i) => s+v*[.2126,.7152,.0722][i],0);
        const pairs = [['--text','--surface'],['--muted','--surface'],['--muted','--bg'],['--accent','--surface'],
          ['--good','--good-bg'],['--warn','--warn-bg'],['--bad','--bad-bg'],
          [document.documentElement.dataset.theme === 'dark' ? '#142039' : '#ffffff','--accent']];
        return pairs.map(([fg,bg]) => { const a=lum(rgb(fg)),b=lum(rgb(bg)); return {fg,bg,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)}; });
      });
      ratios.forEach(r => assert.ok(r.ratio >= 4.5, theme + ' contrast ' + JSON.stringify(r)));
      contrast.push({theme,ratios});
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }

  // Native Chromium zoom in an isolated profile, not CSS zoom or DPR emulation.
  // Partition preference format follows Chromium ChromeZoomLevelPrefs; assert actual metrics.
  const profile = path.resolve(out, 'zoom-profile');
  fs.mkdirSync(path.join(profile,'Default'), {recursive:true});
  fs.writeFileSync(path.join(profile,'Default','Preferences'), JSON.stringify({partition:{default_zoom_level:{x:Math.log(2)/Math.log(1.2)}}}));
  const zoom = await chromium.launchPersistentContext(profile, {channel,headless:true,viewport:null,args:['--window-size=1440,1000']});
  let zoomMetrics;
  try {
    const p = zoom.pages()[0]; p.on('pageerror', e => errors.push(e.message));
    const cdp = await zoom.newCDPSession(p);
    for (const route of ['/workbench', '/workbench#' + completed.run_id, '/']) {
      await p.goto(base + route);
      if (route.includes('#')) await text(p, '#status', '已完成');
      zoomMetrics = await p.evaluate(() => ({innerWidth,outerWidth,devicePixelRatio}));
      assert.equal(zoomMetrics.devicePixelRatio, 2);
      assert.ok(zoomMetrics.innerWidth < zoomMetrics.outerWidth * .55);
      for (const theme of ['light','dark']) {
        await p.locator('#theme').selectOption(theme);
        await fit(p, 'native-200-percent-' + route + '-' + theme);
        // Playwright fullPage clips in CSS pixels at native zoom. CDP's contentSize
        // is in device-independent pixels and captures the whole zoomed document.
        const metrics = await cdp.send('Page.getLayoutMetrics');
        assert.equal(metrics.cssVisualViewport.zoom, 2);
        const {data} = await cdp.send('Page.captureScreenshot', {format:'png',fromSurface:true,
          captureBeyondViewport:true,clip:{...metrics.contentSize,scale:1}});
        fs.writeFileSync(path.join(out, 'zoom200-' + (route.includes('#') ? 'result' : route === '/' ? 'ask' : 'workbench') + '-' + theme + '.png'), Buffer.from(data,'base64'));
      }
    }
  } finally { await zoom.close(); }
  assert.deepEqual(errors, []);
  checks.push('native 200% browser zoom, both pages and restored result, light/dark');
  const result = {status:'PASS',scope:'Edge, offline fake model; interruption states explicitly mocked only in browser',
    checks,layouts,contrast,zoomMetrics,errors};
  fs.writeFileSync(path.join(out,'summary.json'), JSON.stringify(result,null,2));
  console.log(JSON.stringify({status:result.status,checks,layoutCases:layouts.length,zoomMetrics,errors}));
})().catch(error => {console.error(error); process.exitCode = 1;});
