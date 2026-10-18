// Real Edge acceptance with a fresh offline database; no existing runs are touched.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawn, spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  if (!process.argv[2]) throw new Error('Pass a NEW evidence directory');
  const out = path.resolve(process.argv[2]);
  fs.mkdirSync(out);
  const listener = http.createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const python = process.env.FINRECEIPTS_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('FINRECEIPTS_') && !['OPENAI_API_KEY','ANTHROPIC_API_KEY','MINIMAX_API_KEY'].includes(k)));
  Object.assign(env, {FINRECEIPTS_HOST:'127.0.0.1', FINRECEIPTS_PORT:String(port), FINRECEIPTS_OFFLINE:'1',
    FINRECEIPTS_LLM_PROVIDER:'fake', FINRECEIPTS_CACHE_DIR:path.join(root,'tests/fixtures/sec'),
    FINRECEIPTS_RUN_DB:path.join(out,'runs.sqlite3'), PYTHONIOENCODING:'utf-8'});
  const seed = `
import json
from finreceipts.app.main import AppState
from finreceipts.app.runs import CreateRun
from finreceipts.llm.client import FakeLLM
state=AppState(); state.start()
docs=[]
try:
    request=CreateRun(question='Apple revenue for 2023?',ticker='AAPL',fiscal_years=[2023]).model_dump()
    request.update(model='demo',escalate_model=None)
    def transient(*_): raise OSError('synthetic provider outage')
    for index,responder in enumerate([lambda *_: '$1 '*257,lambda *_: '$'+'9'*400,transient]):
        state.runs.llm=FakeLLM(responder)
        doc=state.runs.create('browser-failure-'+str(index),request)
        for _ in range(4):
            if doc['status']!='ready': break
            doc=state.runs.advance(doc['run_id'],doc['revision'])
        docs.append(doc)
    print(json.dumps(docs))
finally: state.stop()
`;
  const seeded = spawnSync(python, ['-c',seed], {cwd:root,env,windowsHide:true,encoding:'utf8',timeout:30000});
  assert.equal(seeded.status, 0, seeded.stderr);
  const docs = JSON.parse(seeded.stdout);
  assert.deepEqual(docs.map(d=>d.status), ['failed','failed','interrupted']);
  fs.writeFileSync(path.join(out,'seed.json'),seeded.stdout);
  const log = fs.openSync(path.join(out,'server.log'),'wx');
  const server = spawn(python,['-m','finreceipts.app'],{cwd:root,env,windowsHide:true,stdio:['ignore',log,log]});
  let browser;
  const result = {status:'FAILED',scope:'real Edge, offline FakeLLM, fresh SQLite',checks:[]};
  try {
    for(let i=0;i<100;i++) {
      if(server.exitCode!==null) throw new Error('server exited before startup');
      try { if((await fetch(base+'/health',{signal:AbortSignal.timeout(1000)})).ok) break; } catch (_) {}
      if(i===99) throw new Error('server did not become ready');
      await pause(100);
    }
    browser=await chromium.launch({headless:true,channel:'msedge'});
    const page=await browser.newPage({viewport:{width:1200,height:900}});
    const errors=[]; page.on('pageerror',error=>errors.push(error.message));
    for(const [i,doc] of docs.slice(0,2).entries()) {
      const url=base+'/api/runs/'+doc.run_id;
      const before=Buffer.from(await (await fetch(url+'/export')).arrayBuffer());
      await page.goto(base+'/workbench#'+doc.run_id);
      await page.locator('#status').filter({hasText:'已失败'}).waitFor();
      await page.waitForFunction(n=>document.querySelectorAll('#events li').length===n,doc.revision);
      assert.equal(await page.locator('#failure-notice').isVisible(),true);
      assert.match(await page.locator('#failure-reason').textContent(),/当前任务无法恢复/);
      assert.equal(await page.locator('#recovery').isVisible(),false);
      for(const id of ['step','run','cancel']) assert.equal(await page.locator('#'+id).isVisible(),false);
      assert.equal(await page.locator('#compare').isDisabled(),true);
      assert.equal(await page.locator('#dispatches').textContent(),'1 / 6');
      const resume=await fetch(url+'/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:doc.revision,acknowledge_possible_duplicate:true})});
      assert.equal(resume.status,409);
      await page.reload();
      await page.locator('#status').filter({hasText:'已失败'}).waitFor();
      const after=Buffer.from(await (await fetch(url+'/export')).arrayBuffer());
      assert.deepEqual(after,before);
      const exportFile=path.join(out,`export-${i}.json`);fs.writeFileSync(exportFile,after);
      const checked=spawnSync(python,['-c','import json,sys; from finreceipts.runtime.store import verify_export; verify_export(json.load(open(sys.argv[1],encoding="utf-8")))',exportFile],{cwd:root,env,windowsHide:true,encoding:'utf8',timeout:15000});
      assert.equal(checked.status,0,checked.stderr);
      await page.screenshot({path:path.join(out,`failed-${i}-desktop.png`),fullPage:true});
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      assert.ok((await page.locator('#failure-notice').boundingBox()).y < 600);
      await page.screenshot({path:path.join(out,`failed-${i}-mobile.png`),fullPage:true});
      await page.setViewportSize({width:1200,height:900});
      result.checks.push({run_id:doc.run_id,reason:doc.stop_reason,resume:409,export_unchanged:true,export_integrity:'PASS'});
    }
    await page.locator('#question').fill('Apple revenue for fiscal year 2023 only?');
    await page.locator('#ticker').fill('AAPL');await page.locator('#years').fill('2023');
    await page.locator('#create').click();
    await page.locator('#status').filter({hasText:'待继续'}).waitFor();
    const replacementId=new URL(page.url()).hash.slice(1);
    assert.ok(!docs.some(doc=>doc.run_id===replacementId));
    assert.equal(await page.locator('#failure-notice').isVisible(),false);
    assert.equal(await page.locator('#dispatches').textContent(),'0 / 6');
    await page.locator('#cancel').click();
    await page.locator('#status').filter({hasText:'已取消'}).waitFor();
    result.new_task_after_failure={run_id:replacementId,dispatches:0,old_runs_preserved:true};
    for(const doc of docs.slice(0,2)) assert.equal((await (await fetch(base+'/api/runs/'+doc.run_id)).json()).status,'failed');
    await page.goto(base+'/workbench#'+docs[2].run_id);
    await page.locator('#status').filter({hasText:'已中断'}).waitFor();
    assert.equal(await page.locator('#recovery').isVisible(),true);
    assert.equal(await page.locator('#failure-notice').isVisible(),false);
    await page.locator('#ack').check(); await page.locator('#resume').click();
    await page.locator('#status').filter({hasText:'待继续'}).waitFor();
    await page.locator('#run').click();
    await page.locator('#status').filter({hasText:'已完成'}).waitFor({timeout:30000});
    const restored=await (await fetch(base+'/api/runs/'+docs[2].run_id)).json();
    assert.ok(restored.dispatches>1);
    result.transient={status:restored.status,dispatches:restored.dispatches,reserved_before_resume:1};
    assert.deepEqual(errors,[]);
    result.status='PASS';result.errors=errors;
  } catch(error) { result.error=error.stack;throw error; }
  finally {
    if(browser) await browser.close();
    if(server.exitCode===null && server.signalCode===null) {
      const exited=new Promise(resolve=>server.once('exit',resolve));server.kill();await exited;
    }
    fs.closeSync(log);result.server_stopped=true;
    fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(result,null,2));
  }
  console.log(JSON.stringify(result));
})().catch(error=>{console.error(error);process.exitCode=1;});
