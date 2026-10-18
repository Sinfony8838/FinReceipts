// Saved-task discovery in a fresh tab, with real HTTP and an isolated SQLite database.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawn, spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
const root = path.resolve(__dirname,'..');

(async()=>{
  if(!process.argv[2]) throw new Error('Pass a NEW output directory');
  const out=path.resolve(process.argv[2]); fs.mkdirSync(out);
  const listener=http.createServer();
  await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
  const port=listener.address().port; await new Promise(resolve=>listener.close(resolve));
  const base=`http://127.0.0.1:${port}`;
  const python=process.env.FINRECEIPTS_TEST_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  const env={...process.env,FINRECEIPTS_HOST:'127.0.0.1',FINRECEIPTS_PORT:String(port),
    FINRECEIPTS_RUN_DB:path.join(out,'runs.sqlite3'),FINRECEIPTS_LLM_PROVIDER:'fake',FINRECEIPTS_OFFLINE:'1',
    FINRECEIPTS_CACHE_DIR:path.join(root,'tests/fixtures/sec'),FINRECEIPTS_SMALL_MODEL:'',FINRECEIPTS_LARGE_MODEL:'',
    FINRECEIPTS_RELOAD:'0',FINRECEIPTS_ALLOWED_HOSTS:'localhost,127.0.0.1,[::1]',FINRECEIPTS_MAX_INFLIGHT:'4',
    FINRECEIPTS_MAX_REQUEST_BYTES:'262144',FINRECEIPTS_REQUEST_READ_TIMEOUT_S:'10',FINRECEIPTS_RESPONSE_TIMEOUT_S:'120',PYTHONIOENCODING:'utf-8'};
  const log=fs.openSync(path.join(out,'server.log'),'wx');
  const server=spawn(python,['-m','finreceipts.app'],{cwd:root,env,windowsHide:true,stdio:['ignore',log,log]});
  const result={status:'FAILED',scope:'real Edge and offline fake API; new isolated database'};
  let browser;
  async function create(number,question=`History ${number}: AAPL revenue in 2023?`) {
    const response=await fetch(base+'/api/runs',{method:'POST',headers:{'content-type':'application/json','Idempotency-Key':`history-browser-${number}`},
      body:JSON.stringify({question,ticker:'AAPL',fiscal_years:[2023]})});
    assert.equal(response.status,201); return response.json();
  }
  async function exported(id){return (await fetch(base+'/api/runs/'+id+'/export')).json();}
  try {
    let ready=false;
    for(let i=0;i<100;i++) {
      assert.equal(server.exitCode,null);
      try{if((await fetch(base+'/health',{signal:AbortSignal.timeout(1000)})).ok){ready=true;break;}}catch(_){}
      await pause(100);
    }
    assert.ok(ready);
    const docs=[]; for(let i=0;i<12;i++) docs.push(await create(i));
    const before=await Promise.all(docs.map(doc=>exported(doc.run_id)));
    browser=await chromium.launch({headless:true,channel:'msedge'});
    let page=await browser.newPage({viewport:{width:1200,height:900}});
    const errors=[]; page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base+'/workbench');
    await page.locator('#run-history > summary').click();
    await page.waitForFunction(()=>document.querySelectorAll('#history-list li').length===10);
    const ids=()=>page.locator('#history-list li').evaluateAll(rows=>rows.map(row=>row.dataset.runId));
    const first=await ids(); assert.deepEqual(first,docs.slice(2).reverse().map(d=>d.run_id));
    const xss='<img src=x onerror="window.__historyXss=true">';
    const newest=await create(12,xss);
    await page.locator('#history-next').click();
    await page.locator('#history-note').filter({hasText:'第 2 页'}).waitFor();
    const second=await ids(); assert.deepEqual(second,[docs[1].run_id,docs[0].run_id]);
    assert.equal(new Set([...first,...second]).size,12);
    assert.equal(await page.locator('#history-next').isDisabled(),true);
    await page.locator('#history-previous').click();
    await page.locator('#history-note').filter({hasText:'第 1 页'}).waitFor();
    assert.deepEqual(await ids(),first);
    await page.locator('#history-refresh').click();
    await page.waitForFunction(id=>document.querySelector('#history-list li')?.dataset.runId===id,newest.run_id);
    assert.equal(await page.locator('#history-list img').count(),0);
    assert.equal(await page.evaluate(()=>window.__historyXss),undefined);
    assert.equal(await page.locator('#history-list li').first().locator('a').textContent(),xss);
    result.pagination={initial:10,next:2,unique:12,new_insert_excluded_until_refresh:true,previous_page_stable:true};
    result.untrusted_question_rendered_as_text=true;

    // All tab-local state is discarded. Find the old task via the saved-task list.
    await page.close();
    page=await browser.newPage({viewport:{width:1200,height:900}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base+'/workbench');
    assert.equal(new URL(page.url()).hash,'');
    await page.locator('#run-history > summary').click();
    await page.waitForFunction(()=>document.querySelectorAll('#history-list li').length===10);
    await page.locator('#history-next').click();
    await page.locator('#history-note').filter({hasText:'第 2 页'}).waitFor();
    await page.locator(`#history-list li[data-run-id="${docs[0].run_id}"] a`).click();
    await page.locator('#status').filter({hasText:'待继续'}).waitFor();
    assert.equal(new URL(page.url()).hash,'#'+docs[0].run_id);
    assert.equal(await page.locator('#dispatches').textContent(),'0 / 6');
    assert.deepEqual(await Promise.all(docs.map(doc=>exported(doc.run_id))),before);
    result.fresh_tab_found_original_run=docs[0].run_id;
    result.original_exports_unchanged=12;

    await page.route('**/api/runs?**',route=>route.abort('connectionreset'));
    await page.locator('#run-history > summary').click();
    await page.waitForFunction(()=>document.querySelector('#history-error').textContent.length>0);
    assert.equal(new URL(page.url()).hash,'#'+docs[0].run_id);
    assert.equal(await page.locator('#status').textContent(),'待继续');
    assert.equal(await page.locator('#step').isDisabled(),false);
    await page.unroute('**/api/runs?**');
    await page.locator('#history-refresh').click();
    await page.waitForFunction(()=>document.querySelectorAll('#history-list li').length===10);
    assert.equal(await page.locator('#history-error').textContent(),'');
    result.list_failure_preserves_open_task=true;

    const changed=await create(14);
    await page.goto(base+'/workbench#'+changed.run_id);
    await page.locator('#status').filter({hasText:'待继续'}).waitFor();
    const cancelled=await page.request.post(base+'/api/runs/'+changed.run_id+'/cancel',{data:{expected_revision:changed.revision}});
    assert.equal(cancelled.status(),200);
    await page.locator('#run-history > summary').click();
    const currentLink=page.locator(`#history-list li[data-run-id="${changed.run_id}"] a`);
    await currentLink.waitFor();
    await currentLink.click();
    await page.locator('#status').filter({hasText:'已取消'}).waitFor();
    result.opening_current_task_refreshes_state=true;

    // Corrupt only this drill's extra record; healthy task discovery must remain usable.
    const corrupt=await create(13,'TAINTED HISTORY QUESTION');
    const mutation=spawnSync(python,['-c',
      'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE runs SET document=? WHERE run_id=?",("{TAINTED",sys.argv[2])); c.commit(); c.close()',
      path.join(out,'runs.sqlite3'),corrupt.run_id],{cwd:root,env,windowsHide:true,encoding:'utf8'});
    assert.equal(mutation.status,0,mutation.stderr);
    await page.goto(base+'/workbench');
    await page.locator('#run-history > summary').click();
    await page.waitForFunction(()=>document.querySelectorAll('#history-list li').length===10);
    assert.match(await page.locator('#history-list li').first().textContent(),/校验失败/);
    assert.ok(!(await page.locator('#history-list').textContent()).includes('TAINTED'));
    assert.equal(await page.locator('#history-list li').count(),10);
    result.damaged_record_isolated=true;
    await page.screenshot({path:path.join(out,'history-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:path.join(out,'history-mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    result.errors=errors;
    const regression=spawnSync(process.execPath,[path.join(root,'scripts/browser_runtime_smoke.cjs'),path.join(out,'runtime-regression')],
      {cwd:root,env:{...env,FINRECEIPTS_TEST_URL:base},windowsHide:true,encoding:'utf8',timeout:90000});
    fs.writeFileSync(path.join(out,'runtime-regression.log'),(regression.stdout||'')+(regression.stderr||''));
    assert.equal(regression.status,0,regression.stderr);
    result.runtime_regression='PASS'; result.status='PASS';
  } finally {
    if(browser) await browser.close();
    if(server.exitCode===null && server.signalCode===null){const stopped=new Promise(resolve=>server.once('exit',resolve));server.kill();await stopped;}
    fs.closeSync(log); result.server_stopped=server.exitCode!==null||server.signalCode!==null;
    fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(result,null,2));
  }
  console.log(JSON.stringify(result));
})().catch(error=>{console.error(error);process.exitCode=1;});
