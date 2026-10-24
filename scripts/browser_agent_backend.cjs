// Real Edge + isolated server/database. Tokens are generated in memory, never logged.
const {chromium}=require('playwright');
const fs=require('node:fs'), path=require('node:path'), http=require('node:http');
const crypto=require('node:crypto'), assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'), pause=ms=>new Promise(r=>setTimeout(r,ms));

(async()=>{
  if(!process.argv[2]) throw new Error('Pass a NEW output directory');
  const out=path.resolve(process.argv[2]);
  fs.mkdirSync(path.dirname(out),{recursive:true}); fs.mkdirSync(out);
  const tokens={alice:crypto.randomBytes(32).toString('base64url'),bob:crypto.randomBytes(32).toString('base64url')};
  const digests=Object.fromEntries(Object.entries(tokens).map(([u,t])=>[u,crypto.createHash('sha256').update(t).digest('hex')]));
  const listener=http.createServer(); await new Promise(r=>listener.listen(0,'127.0.0.1',r));
  const port=listener.address().port; await new Promise(r=>listener.close(r));
  const base=`http://127.0.0.1:${port}`;
  const python=process.env.FINRECEIPTS_TEST_PYTHON || path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  const env={...process.env,FINRECEIPTS_HOST:'127.0.0.1',FINRECEIPTS_PORT:String(port),
    FINRECEIPTS_RUN_DB:path.join(out,'runs.sqlite3'),FINRECEIPTS_LLM_PROVIDER:'fake',FINRECEIPTS_OFFLINE:'1',
    FINRECEIPTS_CACHE_DIR:path.join(root,'tests/fixtures/sec'),FINRECEIPTS_RELOAD:'0',
    FINRECEIPTS_SMALL_MODEL:'',FINRECEIPTS_LARGE_MODEL:'',FINRECEIPTS_AUTH_MODE:'token',
    FINRECEIPTS_AUTH_TOKENS_ENV:'FIN_TEST_AUTH_DIGESTS',FIN_TEST_AUTH_DIGESTS:JSON.stringify(digests),
    FINRECEIPTS_ALLOWED_HOSTS:'127.0.0.1,localhost,[::1]',PYTHONIOENCODING:'utf-8'};
  const log=fs.openSync(path.join(out,'server.log'),'wx');
  let server, browser, releaseHistory=()=>{};
  const result={status:'FAILED',evidence_type:'real Edge; FakeLLM; retained SEC; injected response loss',attempts:[],server_processes:[]};
  result.provenance={node:process.version,files:Object.fromEntries([
    'scripts/browser_agent_backend.cjs','src/finreceipts/app/static/runs.js',
    'src/finreceipts/app/static/run-auth.js','src/finreceipts/app/static/create-journal.js'
  ].map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')]))};
  async function start(){
    server=spawn(python,['-m','finreceipts.app'],{cwd:root,env,windowsHide:true,stdio:['ignore',log,log]});
    result.server_processes.push(server.pid);
    for(let i=0;i<100;i++){
      if(server.exitCode!==null)throw new Error('Server exited before startup');
      try{if((await fetch(base+'/health',{signal:AbortSignal.timeout(1000)})).ok)return;}catch(_){}
      await pause(100);
    }
    throw new Error('Server startup timed out');
  }
  async function stop(){
    if(server && server.exitCode===null && server.signalCode===null){
      const exited=new Promise(r=>server.once('exit',r)); server.kill(); await exited;
    }
  }
  const headers=(user,extra={})=>({Authorization:'Bearer '+tokens[user],...extra});
  const api=(user,url,options={})=>fetch(base+url,{...options,headers:headers(user,options.headers),signal:AbortSignal.timeout(5000)});
  try{
    await start();
    assert.equal((await fetch(base+'/api/runs')).status,401);
    browser=await chromium.launch({headless:true,channel:'msedge'});
    result.provenance.browser=browser.version();
    const page=await browser.newPage({viewport:{width:1200,height:900}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    async function login(user){
      await page.locator('#auth-token').fill(tokens[user]);
      await page.locator('#auth-form button').click();
      await page.locator('#auth-user').filter({hasText:'当前用户：'+user}).waitFor();
      await page.waitForFunction(()=>!document.querySelector('#auth-token').value);
    }
    let drop=true;
    await page.route(base+'/api/runs',async route=>{
      if(route.request().method()!=='POST')return route.continue();
      const response=await route.fetch(),doc=await response.json();
      result.attempts.push({key:route.request().headers()['idempotency-key'],body:route.request().postData(),run_id:doc.run_id,status:response.status()});
      assert.equal(response.status(),201);
      if(drop){drop=false;await route.abort('connectionreset');}else await route.fulfill({response});
    });
    await page.goto(base+'/workbench'); await login('alice');
    await page.locator('#target-enabled').check(); await page.locator('#target-as-of').fill('2024-01-01');
    await page.locator('#create').click();
    await page.waitForFunction(()=>document.querySelector('#error').textContent.length>0);
    assert.equal(result.attempts.length,1);
    const originalId=result.attempts[0].run_id;
    const before=Buffer.from(await (await api('alice','/api/runs/'+originalId+'/export')).arrayBuffer());
    assert.equal(JSON.parse(before).run.dispatches,0);
    await stop(); await start(); await page.reload(); await login('alice');
    assert.equal(await page.locator('#retry-create').isEnabled(),true);
    assert.equal(await page.locator('#create').isDisabled(),true);
    await page.screenshot({path:path.join(out,'pending-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:path.join(out,'pending-mobile.png'),fullPage:true});
    await page.locator('#retry-create').click(); await page.locator('#status').filter({hasText:'待继续'}).waitFor();
    assert.equal(result.attempts.length,2);
    assert.deepEqual(result.attempts[0],result.attempts[1]);
    const after=Buffer.from(await (await api('alice','/api/runs/'+originalId+'/export')).arrayBuffer());
    assert.deepEqual(after,before);
    const count=spawnSync(python,['-c','import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute("select count(*) from runs").fetchone()[0])',path.join(out,'runs.sqlite3')],{env,cwd:root,windowsHide:true,encoding:'utf8',timeout:10000});
    assert.equal(count.status,0); assert.equal(Number(count.stdout.trim()),1);
    result.recovery={same_key:true,same_body:true,same_run:true,rows:1,events:1,dispatches:0};

    let markHistory;
    const historyStarted=new Promise(resolve=>{markHistory=resolve;});
    const historyRelease=new Promise(resolve=>{releaseHistory=resolve;});
    await page.route(base+'/api/runs?limit=10',async route=>{
      if(route.request().headers().authorization!==headers('alice').Authorization)return route.continue();
      const response=await route.fetch(); markHistory(); await historyRelease;
      await route.fulfill({response});
    });
    await page.locator('#run-history > summary').click();
    let historyTimer;
    try { await Promise.race([historyStarted,new Promise((_,reject)=>{historyTimer=setTimeout(()=>reject(new Error('history interception timed out')),10000);})]); }
    finally { clearTimeout(historyTimer); }
    await login('bob');
    const lateHistory=page.waitForResponse(response=>response.url()===base+'/api/runs?limit=10' && response.request().headers().authorization===headers('alice').Authorization);
    releaseHistory(); await (await lateHistory).finished();
    await page.unroute(base+'/api/runs?limit=10');
    await page.evaluate(()=>Promise.resolve());
    assert.equal(await page.locator('[data-run-id="'+originalId+'"]').count(),0);
    assert.equal((await api('bob','/api/runs/'+originalId+'/export')).status,404);
    const bob=await (await api('bob','/api/runs',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':result.attempts[0].key},body:result.attempts[0].body})).json();
    assert.notEqual(bob.run_id,originalId);
    const bobList=await (await api('bob','/api/runs')).json();
    assert.deepEqual(bobList.items.map(i=>i.run_id),[bob.run_id]);

    await page.goto(base+'/workbench'); await login('alice');
    await page.locator('#question').fill('Alice pending ownership check');
    drop=true; await page.locator('#create').click();
    await page.waitForFunction(()=>document.querySelector('#error').textContent.length>0);
    const attempts=result.attempts.length;
    await login('bob');
    await page.locator('#pending-note').filter({hasText:'其他用户'}).waitFor();
    assert.equal(await page.locator('#retry-create').isDisabled(),true);
    assert.equal(await page.locator('#question').inputValue(),'');
    assert.equal(result.attempts.length,attempts);
    await login('alice'); await page.locator('#retry-create').click();
    await page.locator('#status').filter({hasText:'待继续'}).waitFor();
    result.ownership={foreign_export:404,same_nonce_different_users:true,pending_identity_mismatch_refused:true,stale_history_discarded:true};
    await page.unroute(base+'/api/runs');

    await page.goto(base+'/workbench#'+originalId); await login('alice');
    await page.locator('#run').click(); await page.locator('#status').filter({hasText:'已完成'}).waitFor();
    await page.locator('#target-result').filter({hasText:'目标数值核验通过'}).waitFor();
    await page.locator('#compare').click();
    await page.locator('#comparison').filter({hasText:'新增模型调用'}).waitFor();
    await page.locator('#evidence-details > summary').click();
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#export').click()]);
    const exportPath=path.join(out,'authenticated-export.json');
    await download.saveAs(exportPath);
    const raw=Buffer.from(await (await api('alice','/api/runs/'+originalId+'/export')).arrayBuffer());
    assert.deepEqual(fs.readFileSync(exportPath),raw);
    const checked=spawnSync(python,['scripts/verify_run_export.py',exportPath],{env,cwd:root,windowsHide:true,encoding:'utf8',timeout:15000});
    assert.equal(checked.status,0,checked.stderr);
    const storage=await page.evaluate(()=>JSON.stringify({session:{...sessionStorage},local:{...localStorage}}));
    for(const token of Object.values(tokens))assert.equal(storage.includes(token),false);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:path.join(out,'completed-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1200,height:900});
    await page.screenshot({path:path.join(out,'completed-desktop.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    result.completed={target_gate_passed:JSON.parse(raw).run.quality.target_gate_passed,raw_export_verified:true,tokens_not_persisted:true,page_errors:errors};
    result.status='PASS';
  }catch(error){result.error=error.stack || String(error);process.exitCode=1;}
  finally{
    releaseHistory();
    if(browser)await browser.close(); await stop(); fs.closeSync(log);
    let alive=false;try{alive=(await fetch(base+'/health',{signal:AbortSignal.timeout(700)})).ok;}catch(_){}
    result.server_stopped=!alive;
    if(alive){result.status='FAILED';process.exitCode=1;}
    const savedLog=fs.readFileSync(path.join(out,'server.log'),'utf8');
    for(const token of Object.values(tokens))if(savedLog.includes(token)){result.status='FAILED';result.secret_scan='FAILED';process.exitCode=1;}
    fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result,null,2));
  }
})();
