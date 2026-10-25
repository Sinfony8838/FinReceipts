// Partial failure, cancellation and rejection through the deployed Vue UI.
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const env=Object.fromEntries(fs.readFileSync(path.join(root,'.env.v2'),'utf8').split(/\r?\n/).filter(s=>s.includes('=')&&!s.startsWith('#')).map(s=>{const i=s.indexOf('=');return [s.slice(0,i),s.slice(i+1)]}));
(async()=>{
 const out=path.join(root,'runs','v2-browser-cases-'+Date.now());fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const report={status:'FAILED',errors,browser:browser.version()};
 async function login(user){await page.getByLabel('用户名',{exact:true}).fill(user);await page.getByLabel('密码',{exact:true}).fill(env.BOOTSTRAP_PASSWORD);await page.getByRole('button',{name:'进入工作台',exact:true}).click();await page.locator('.identity b').waitFor()}
 async function inspect(id){await page.locator('tbody tr').filter({hasText:id.slice(0,8)}).getByRole('button',{name:'查看证据',exact:true}).click();await page.locator('.evidence').waitFor()}
 async function close(){await page.locator('.evidence').getByRole('button',{name:'关闭',exact:true}).click()}
 try{
  await page.goto('http://127.0.0.1:8090');await login('alpha.analyst');
  await page.getByRole('button',{name:'新建核验批次',exact:true}).click();
  const title='UI partial failure '+Date.now();await page.getByLabel('批次名称').fill(title);
  await page.getByRole('tab',{name:'批量 JSON 清单'}).click();
  await page.locator('#batch-json').fill(JSON.stringify(['AAPL','NOFIXTURE'].map(ticker=>({kind:'metric',ticker,target:{kind:'direct',metric:'revenue',fiscal_year:2023}}))));
  const submitted=page.waitForResponse(r=>r.url().endsWith('/api/v2/batches')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'提交批次',exact:true}).click();report.batch=(await (await submitted).json()).batch_id;
  let tasks=[];
  for(let i=0;i<90;i++){tasks=(await (await page.request.get('http://127.0.0.1:8090/api/v2/tasks?batch_id='+report.batch)).json()).items;if(tasks?.length===2&&tasks.every(t=>['COMPLETED','FAILED'].includes(t.status)))break;await page.waitForTimeout(1000)}
  const failed=tasks.find(t=>t.status==='FAILED'),completed=tasks.find(t=>t.status==='COMPLETED');assert.ok(failed&&completed);report.partial_failure=true;
  await inspect(failed.id);await page.locator('.evidence .drawer-error').getByText('evidence_unavailable').waitFor();
  await page.getByRole('button',{name:'取消任务',exact:true}).click();await page.locator('.evidence .status-badge').getByText('已取消',{exact:true}).waitFor();report.cancelled=true;
  await page.screenshot({path:path.join(out,'cancelled.png'),fullPage:true});await close();
  await page.getByRole('button',{name:'退出登录'}).click();await login('alpha.reviewer');
  await page.locator('.batch-card').filter({hasText:title}).click();await inspect(completed.id);
  await page.getByLabel('复核理由').fill('UI negative control: reject this synthetic result for delivery.');
  await page.getByRole('button',{name:'驳回当前结果',exact:true}).click();await page.locator('.review-record').getByText('已驳回',{exact:true}).waitFor();report.rejected=true;
  await page.screenshot({path:path.join(out,'rejected.png'),fullPage:true});await close();
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'导出已审核结果',exact:true}).click();const dest=path.join(out,'empty-delivery.json');await (await download).saveAs(dest);
  assert.equal(JSON.parse(fs.readFileSync(dest,'utf8')).approved_items.length,0);report.rejected_excluded=true;
  assert.equal(errors.length,0);report.status='PASS';
 }finally{fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({...report,out}));}
})().catch(e=>{console.error(e.message);process.exitCode=1});
