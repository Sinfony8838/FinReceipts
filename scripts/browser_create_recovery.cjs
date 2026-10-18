// Real server commits a creation, then Playwright drops only the response to the page.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawn, spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const root = path.resolve(__dirname, '..');

(async () => {
  const out = path.resolve(process.argv[2] || '');
  if (!process.argv[2]) throw new Error('Pass a NEW output directory');
  fs.mkdirSync(out);
  const listener = http.createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const python = process.env.FINRECEIPTS_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const env = {...process.env, FINRECEIPTS_HOST:'127.0.0.1', FINRECEIPTS_PORT:String(port),
    FINRECEIPTS_RUN_DB:path.join(out,'runs.sqlite3'), FINRECEIPTS_LLM_PROVIDER:'fake',
    FINRECEIPTS_OFFLINE:'1', FINRECEIPTS_CACHE_DIR:path.join(root,'tests/fixtures/sec'),
    FINRECEIPTS_SMALL_MODEL:'', FINRECEIPTS_LARGE_MODEL:'', FINRECEIPTS_RELOAD:'0',
    FINRECEIPTS_ALLOWED_HOSTS:'localhost,127.0.0.1,[::1]', FINRECEIPTS_MAX_INFLIGHT:'4',
    FINRECEIPTS_MAX_REQUEST_BYTES:'262144', FINRECEIPTS_REQUEST_READ_TIMEOUT_S:'10',
    FINRECEIPTS_RESPONSE_TIMEOUT_S:'120', PYTHONIOENCODING:'utf-8'};
  const log = fs.openSync(path.join(out,'server.log'),'wx');
  const startServer = () => spawn(python, ['-m','finreceipts.app'], {cwd:root,env,windowsHide:true,stdio:['ignore',log,log]});
  let server = startServer();
  async function stopServer() {
    if(server.exitCode!==null || server.signalCode!==null) return;
    const exited=new Promise(resolve=>server.once('exit',resolve));
    server.kill(); await exited;
  }
  async function readyServer() {
    for(let i=0;i<100;i++) {
      if(server.exitCode!==null) throw new Error('Server exited before startup');
      try { if((await fetch(base+'/health',{signal:AbortSignal.timeout(1000)})).ok) return; } catch (_) {}
      await pause(100);
    }
    throw new Error('Server startup timed out');
  }
  const result = {status:'FAILED', scope:'real Edge, offline fake server, lost committed response', attempts:[]};
  let browser;
  try {
    await readyServer();
    browser=await chromium.launch({headless:true,channel:'msedge'});
    const page=await browser.newPage({viewport:{width:1200,height:900}});
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    let drop=true;
    await page.route(base+'/api/runs',async route=>{
      const response=await route.fetch();
      const doc=await response.json();
      result.attempts.push({key:route.request().headers()['idempotency-key'],body:route.request().postData(),status:response.status(),run_id:doc.run_id});
      assert.equal(response.status(),201);
      if(drop){drop=false;await route.abort('connectionreset');} else await route.fulfill({response});
    });
    await page.goto(base+'/workbench');
    await page.locator('#create').click();
    await page.waitForFunction(()=>document.querySelector('#error').textContent.length>0);
    assert.equal(result.attempts.length,1);
    const before=await (await page.request.get(base+'/api/runs/'+result.attempts[0].run_id+'/export')).json();
    assert.equal(before.run.dispatches,0);
    result.server_processes=[server.pid];
    await stopServer();
    server=startServer();
    result.server_processes.push(server.pid);
    await readyServer();
    await page.reload();
    const retry=page.locator('#retry-create');
    if(await retry.count() && await retry.isVisible()) {
      assert.equal(await page.locator('#question').isDisabled(),true);
      assert.equal(await page.locator('#create').isDisabled(),true);
      assert.equal(await page.locator('#discard-create').isDisabled(),true);
      await page.screenshot({path:path.join(out,'pending-desktop.png'),fullPage:true});
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.screenshot({path:path.join(out,'pending-mobile.png'),fullPage:true});
      await retry.click();
    }
    else await page.locator('#create').click();
    await page.locator('#status').filter({hasText:'待继续'}).waitFor();
    assert.equal(result.attempts.length,2);
    assert.equal(result.attempts[0].key,result.attempts[1].key,'reload must reuse the original idempotency key');
    assert.equal(result.attempts[0].body,result.attempts[1].body);
    assert.equal(result.attempts[0].run_id,result.attempts[1].run_id);
    const after=await (await page.request.get(base+'/api/runs/'+result.attempts[0].run_id+'/export')).json();
    assert.deepEqual(after,before);
    assert.equal(new URL(page.url()).hash,'#'+before.run.run_id);
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('finreceipts.pending-create.v1')),null);
    await page.unroute(base+'/api/runs');
    const countAfterRecovery = spawnSync(python,['-c','import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute("SELECT COUNT(*) FROM runs").fetchone()[0])',path.join(out,'runs.sqlite3')],{cwd:root,env,windowsHide:true,encoding:'utf8'});
    assert.equal(countAfterRecovery.status,0);
    assert.equal(Number(countAfterRecovery.stdout.trim()),1);
    result.run_count_after_recovery=1;

    // Successful creation, failed event read: the run URL and cleared journal survive reload.
    await page.goto(base+'/workbench');
    await page.route('**/api/runs/*/events',route=>route.abort('connectionreset'));
    await page.locator('#create').click();
    await page.waitForFunction(()=>document.querySelector('#error').textContent.length>0);
    const eventFailureId=new URL(page.url()).hash.slice(1);
    assert.match(eventFailureId,/^[a-f0-9]{32}$/);
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('finreceipts.pending-create.v1')),null);
    await page.unroute('**/api/runs/*/events');
    await page.reload();
    await page.locator('#status').filter({hasText:'待继续'}).waitFor();
    assert.equal(new URL(page.url()).hash.slice(1),eventFailureId);
    await page.locator('#event-count').filter({hasText:'1 条记录'}).waitFor();
    assert.equal(await page.locator('#event-count').textContent(),'1 条记录');
    result.event_read_failure_recovered=true;

    // Known validation rejection is editable; ambiguous server failures must retain the key.
    await page.goto(base+'/workbench');
    await page.locator('#ticker').fill('*');
    await page.locator('#create').click();
    await page.waitForFunction(()=>document.querySelector('#error').textContent.length>0);
    assert.equal(await page.locator('#ticker').isDisabled(),false);
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('finreceipts.pending-create.v1')),null);
    result.validation_rejection_unlocks=true;

    let ambiguousId;
    await page.locator('#ticker').fill('AAPL');
    await page.route(base+'/api/runs',async route=>{
      const upstream=await route.fetch();
      assert.equal(upstream.status(),201);
      ambiguousId=(await upstream.json()).run_id;
      await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({code:'run_failed',detail:'controlled ambiguous failure'})});
    });
    await page.locator('#create').click();
    await page.waitForFunction(()=>document.querySelector('#error').textContent.length>0);
    assert.ok(await page.evaluate(()=>sessionStorage.getItem('finreceipts.pending-create.v1')));
    await page.unroute(base+'/api/runs');
    await page.reload();
    await page.locator('#retry-create').click();
    await page.locator('#status').filter({hasText:'待继续'}).waitFor();
    assert.equal(new URL(page.url()).hash.slice(1),ambiguousId);
    result.ambiguous_503_reuses_request=true;

    await page.goto(base+'/workbench');
    await page.evaluate(()=>{Storage.prototype.removeItem=function(){throw new DOMException('disabled','SecurityError');};});
    await page.locator('#create').click();
    await page.waitForFunction(()=>document.querySelector('#error').textContent.length>0);
    const clearFailureId=new URL(page.url()).hash.slice(1);
    assert.match(clearFailureId,/^[a-f0-9]{32}$/);
    assert.ok(await page.evaluate(()=>sessionStorage.getItem('finreceipts.pending-create.v1')));
    assert.equal(await page.locator('#create').isDisabled(),true);
    assert.equal(await page.locator('#retry-create').isDisabled(),true);
    await page.reload();
    await page.locator('#retry-create').click();
    await page.waitForFunction(()=>sessionStorage.getItem('finreceipts.pending-create.v1')===null);
    assert.equal(new URL(page.url()).hash.slice(1),clearFailureId);
    result.failed_cleanup_keeps_run_url=true;

    // Corrupt saved input is never submitted; discard requires an explicit acknowledgement.
    await page.evaluate(()=>sessionStorage.setItem('finreceipts.pending-create.v1','{broken'));
    await page.reload();
    assert.equal(await page.locator('#create').isDisabled(),true);
    assert.equal(await page.locator('#retry-create').isVisible(),false);
    assert.equal(await page.locator('#discard-create').isDisabled(),true);
    await page.locator('#discard-ack').check();
    await page.locator('#discard-create').click();
    assert.equal(await page.locator('#create').isDisabled(),false);
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('finreceipts.pending-create.v1')),null);
    result.corrupt_record_requires_discard=true;

    // A browser that cannot persist the key must not send a creation request.
    const unavailable=await browser.newPage();
    let unsafePosts=0;
    unavailable.on('request',request=>{if(request.method()==='POST'&&request.url()===base+'/api/runs')unsafePosts++;});
    await unavailable.addInitScript(()=>{Storage.prototype.setItem=function(){throw new DOMException('disabled','QuotaExceededError');};});
    await unavailable.goto(base+'/workbench');
    await unavailable.locator('#create').click();
    await unavailable.waitForFunction(()=>document.querySelector('#error').textContent.length>0);
    assert.equal(unsafePosts,0);
    assert.equal(await unavailable.locator('#create').isDisabled(),true);
    assert.match(await unavailable.locator('#pending-note').textContent(),/会话存储不可用/);
    await unavailable.close();
    result.unavailable_storage_posts=unsafePosts;

    assert.deepEqual(errors,[]);
    await page.setViewportSize({width:1200,height:900});
    await page.goto(base+'/workbench#'+before.run.run_id);
    await page.locator('#status').filter({hasText:'待继续'}).waitFor();
    await page.screenshot({path:path.join(out,'recovered-desktop.png'),fullPage:true});
    const regression=spawnSync(process.execPath,[path.join(root,'scripts/browser_runtime_smoke.cjs'),path.join(out,'runtime-regression')],
      {cwd:root,env:{...env,FINRECEIPTS_TEST_URL:base},windowsHide:true,encoding:'utf8',timeout:90000});
    fs.writeFileSync(path.join(out,'runtime-regression.log'),(regression.stdout||'')+(regression.stderr||''));
    assert.equal(regression.status,0,regression.stderr);
    result.runtime_regression='PASS';
    result.status='PASS';
  } finally {
    if(browser) await browser.close();
    await stopServer();
    fs.closeSync(log);
    result.server_stopped=server.exitCode!==null || server.signalCode!==null;
    fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(result,null,2));
  }
  console.log(JSON.stringify(result));
})().catch(error=>{console.error(error);process.exitCode=1;});
