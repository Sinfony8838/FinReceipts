// Isolated real HTTP + Edge experiment. No paid provider or existing runtime DB.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function listen(server) {
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return server.address().port;
}
async function close(server) {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
function rawGet(url, headers) {
  return new Promise((resolve,reject)=> {
    const request=http.get(url,{headers},response=> {
      response.resume();
      response.on('end',()=>resolve({status:response.statusCode,sentHost:request.getHeader('host')}));
    });
    request.on('error',reject);
  });
}

(async () => {
  if (!process.argv[2]) throw new Error('Usage: node scripts/browser_boundary_smoke.cjs NEW_OUTPUT_DIRECTORY');
  const out = path.resolve(process.argv[2]);
  fs.mkdirSync(out);
  const reserve = http.createServer();
  const port = await listen(reserve);
  await close(reserve);
  const base = `http://127.0.0.1:${port}`;
  const python = process.env.FINRECEIPTS_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const env = {...process.env, FINRECEIPTS_HOST:'127.0.0.1', FINRECEIPTS_PORT:String(port),
    FINRECEIPTS_RUN_DB:path.join(out, 'runs.sqlite3'), FINRECEIPTS_LLM_PROVIDER:'fake',
    FINRECEIPTS_OFFLINE:'1', FINRECEIPTS_CACHE_DIR:path.join(root,'tests/fixtures/sec'),
    FINRECEIPTS_SMALL_MODEL:'', FINRECEIPTS_LARGE_MODEL:'', FINRECEIPTS_RELOAD:'0',
    FINRECEIPTS_ALLOWED_HOSTS:'localhost,127.0.0.1,[::1]', PYTHONIOENCODING:'utf-8'};
  const log = fs.openSync(path.join(out, 'server.log'), 'wx');
  const server = spawn(python, ['-m', 'finreceipts.app'], {cwd:root, env, windowsHide:true, stdio:['ignore',log,log]});
  const attack = http.createServer((_req, res) => {res.setHeader('Content-Type','text/html'); res.end('<!doctype html><title>Local origin test</title>Controlled test page');});
  const checks=[];
  let current=null;
  function rejectedLogs() {
    return fs.readFileSync(path.join(out,'server.log'),'utf8').split(/\r?\n/).flatMap(line=>{
      try {const row=JSON.parse(line); return row.error_code==='cross_origin_denied'?[row]:[];} catch(_){return [];}
    });
  }
  let browser;
  try {
    let ready = false;
    for (let i=0; i<100; i++) {
      if (server.exitCode !== null) throw new Error(`API exited before ready: ${server.exitCode}`);
      try { const response = await fetch(base+'/health', {signal:AbortSignal.timeout(1000)}); if(response.ok){ready=true; break;} } catch (_) {}
      await pause(100);
    }
    assert.equal(ready,true,'API did not start');
    const attackPort = await listen(attack);
    browser = await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL || 'msedge'});
    const page = await browser.newPage({viewport:{width:1200,height:900}});
    const cdp=await page.context().newCDPSession(page);
    await cdp.send('Network.enable');
    const requestURLs=new Map(), wireHeaders=new Map();
    cdp.on('Network.requestWillBeSent',event=>requestURLs.set(event.requestId,event.request.url));
    cdp.on('Network.requestWillBeSentExtraInfo',event=>wireHeaders.set(event.requestId,
      Object.fromEntries(Object.entries(event.headers).map(([key,value])=>[key.toLowerCase(),value]))));
    const errors=[];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base+'/');
    const body = {question:'Apple revenue in 2023?',ticker:'AAPL',fiscal_years:[2023],mode:'baseline'};
    const legitimate = await page.evaluate(async body => {
      const response = await fetch('/api/ask/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const text=await response.text();
      return {status:response.status, done:text.includes('event: done'), requestId:response.headers.get('x-request-id')};
    },body);
    assert.equal(legitimate.status,200);
    assert.equal(legitimate.done,true);
    const created = await fetch(base+'/api/runs',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':'browser-boundary-fixture'},body:JSON.stringify(body)});
    assert.equal(created.status,201);
    const doc=await created.json();
    const runPath='/api/runs/'+doc.run_id;
    const original=await (await fetch(base+runPath+'/export')).json();
    const before=await (await fetch(base+'/health')).json();
    for (const [host,expectedSite] of [['127.0.0.1','same-site'],['localhost','cross-site']]) {
      await page.goto(`http://${host}:${attackPort}/`);
      for (const [target,method] of [
        ['/api/ask/stream?question=Apple%20revenue%20in%202023%3F&ticker=AAPL&fiscal_years=2023','GET'],
        [runPath+'/export','GET'],[runPath+'/cancel','POST']]) {
        const url=base+target;
        const prior=rejectedLogs().length;
        let observed;
        const capture=request=>{if(request.url()===url) observed=request;};
        page.on('request',capture);
        const opaque=await page.evaluate(async ({url,method,revision})=> {
          try {
            const response=await fetch(url,{method,mode:'no-cors',cache:'no-store',
              ...(method==='POST'?{headers:{'Content-Type':'text/plain'},body:JSON.stringify({expected_revision:revision})}:{})});
            return {type:response.type,status:response.status};
          } catch(error) {return {error:error.name};}
        },{url,method,revision:doc.revision});
        page.off('request',capture);
        assert.ok(observed,'browser did not issue the target request');
        let headers;
        for(let i=0;i<50;i++) {
          const id=[...requestURLs].find(([id,address])=>address===url && wireHeaders.has(id))?.[0];
          if(id){ headers=wireHeaders.get(id); requestURLs.delete(id); wireHeaders.delete(id); break; }
          await pause(40);
        }
        current={url,method,headers:headers ? {fetchSite:headers['sec-fetch-site'],origin:headers.origin}:null,
          browserResult:opaque,networkFailure:observed.failure()?.errorText||null};
        assert.ok(headers,'missing browser network extra-info headers');
        assert.equal(headers['sec-fetch-site'],expectedSite);
        // CORP deliberately blocks reading a no-cors response. Prove that the
        // server actually rejected it using its request-ID-bearing diagnostic.
        for(let i=0;i<50 && rejectedLogs().length===prior;i++) await pause(40);
        const records=rejectedLogs();
        assert.equal(records.length,prior+1,'expected exactly one server-side rejection');
        const record=records.at(-1);
        assert.equal(record.status,403);
        checks.push({method,target,fetchSite:headers['sec-fetch-site'],origin:headers.origin||null,
          status:record.status,requestId:record.request_id,browserResult:opaque,
          networkFailure:observed.failure()?.errorText||null});
      }
    }
    const after=await (await fetch(base+'/health')).json();
    assert.deepEqual(after.execution,before.execution);
    assert.deepEqual(await (await fetch(base+runPath+'/export')).json(),original);
    const untrusted=await rawGet(base+'/health',{Host:'untrusted.example'});
    assert.equal(untrusted.sentHost,'untrusted.example');
    assert.equal(untrusted.status,400);
    const spoofed=await rawGet(base+'/health',{Origin:'https://127.0.0.1:'+port,'X-Forwarded-Proto':'https'});
    assert.equal(spoofed.status,403);
    assert.deepEqual(errors,[]);
    await browser.close(); browser=null;
    // Existing UI acceptance verifies create/advance/reload/export/cancel on both viewports.
    const uiLog=fs.openSync(path.join(out,'runtime-ui.log'),'wx');
    const ui=spawn(process.execPath,[path.join(root,'scripts/browser_runtime_smoke.cjs'),path.join(out,'runtime-ui')],
      {cwd:root,env:{...env,FINRECEIPTS_TEST_URL:base},windowsHide:true,stdio:['ignore',uiLog,uiLog]});
    const uiExit=await new Promise((resolve,reject)=>{ui.once('error',reject);ui.once('exit',resolve);});
    fs.closeSync(uiLog);
    assert.equal(uiExit,0,'workbench browser regression failed; inspect runtime-ui.log');
    const result={status:'PASS',scope:'real loopback HTTP and Edge; fake model; no tenant authentication claim',
      legitimate,checks,executionBefore:before.execution,executionAfter:after.execution,
      unchangedRun:true,untrustedHostStatus:untrusted.status,spoofedForwardedSchemeStatus:spoofed.status,
      runtimeUI:JSON.parse(fs.readFileSync(path.join(out,'runtime-ui/summary.json'),'utf8')),errors};
    fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(result,null,2),{flag:'wx'});
    console.log(JSON.stringify(result,null,2));
  } catch(error) {
    fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({error:String(error),current,checks},null,2),{flag:'wx'});
    throw error;
  } finally {
    if(browser) await browser.close();
    if(attack.listening) await close(attack);
    if(server.exitCode===null){ const stopped=new Promise(resolve=>server.once('exit',resolve)); server.kill(); await stopped; }
    fs.closeSync(log);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
