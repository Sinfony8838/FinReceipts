// Real Edge against the actual Compose application; secrets never enter reports.
const {chromium}=require('playwright');
const fs=require('node:fs'), path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const env=Object.fromEntries(fs.readFileSync(path.join(root,'.env.v2'),'utf8').split(/\r?\n/).filter(s=>s.includes('=')&&!s.startsWith('#')).map(s=>{const n=s.indexOf('=');return [s.slice(0,n),s.slice(n+1)]}));
(async()=>{
 const out=path.join(root,'runs','v2-browser-'+Date.now());fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const report={status:'FAILED',browser:browser.version(),screens:[],errors};
 async function screen(name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});report.screens.push(name+'.png')}
 async function login(username){await page.getByLabel('用户名',{exact:true}).fill(username);await page.getByLabel('密码',{exact:true}).fill(env.BOOTSTRAP_PASSWORD);await page.getByRole('button',{name:'进入工作台',exact:true}).click();await page.locator('.identity b').waitFor();assert.equal(await page.locator('.identity b').textContent(),username)}
 try {
  await page.goto('http://127.0.0.1:8090');await page.getByRole('heading',{name:'登录 FinReceipts'}).waitFor();await screen('login');
  await login('alpha.analyst');await page.getByRole('button',{name:'新建核验批次',exact:true}).click();
  await page.getByLabel('批次名称').fill('Browser acceptance '+Date.now());
  const response=page.waitForResponse(r=>r.url().endsWith('/api/v2/batches')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'提交批次',exact:true}).click();report.batch=(await (await response).json()).batch_id;
  await page.locator('.dialog').waitFor({state:'hidden'});
  for(let i=0;i<90;i++){const data=await (await page.request.get('http://127.0.0.1:8090/api/v2/tasks?batch_id='+report.batch)).json();if(data.items?.[0]?.status==='COMPLETED'){report.task_id=data.items[0].id;break}await page.waitForTimeout(1000)}
  assert.ok(report.task_id);await page.locator('tbody tr').filter({hasText:report.task_id.slice(0,8)}).getByText('执行完成',{exact:true}).waitFor({timeout:10000});await screen('batches');
  await page.getByRole('button',{name:'查看证据',exact:true}).first().click();await page.locator('.evidence blockquote').waitFor();
  assert.match(await page.locator('.evidence blockquote').textContent(),/ANSWER:/);await screen('evidence');
  await page.locator('.evidence').getByRole('button',{name:'关闭',exact:true}).click();
  await page.getByRole('button',{name:'用量与配额'}).click();await screen('quota');
  await page.getByRole('button',{name:'退出登录'}).click();await login('alpha.reviewer');
  await page.getByRole('button',{name:'待复核任务'}).click();await page.locator('tbody tr').filter({hasText:report.task_id.slice(0,8)}).getByRole('button',{name:'查看证据',exact:true}).click();
  await page.getByLabel('复核理由').fill('Browser acceptance: reviewed the displayed annual evidence.');
  await page.getByRole('button',{name:'接受并纳入交付'}).click();await page.locator('.review-record').waitFor();await screen('reviewed');
  await page.locator('.evidence').getByRole('button',{name:'关闭',exact:true}).click();
  await page.getByRole('button',{name:'批次与任务'}).click();
  await page.locator('.batch-card').filter({hasText:report.batch.slice(0,8)}).click();
  const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'导出已审核结果',exact:true}).click();await (await downloaded).saveAs(path.join(out,'approved.json'));
  assert.ok(fs.readFileSync(path.join(out,'approved.json'),'utf8').includes(report.task_id));report.exported=true;
  assert.equal(errors.length,0);report.status='PASS';
 } finally {fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({status:report.status,out,errors}));}
})().catch(e=>{console.error(e.message);process.exitCode=1});
