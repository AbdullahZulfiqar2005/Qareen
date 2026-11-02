const {chromium}=require('playwright');
const {spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
 const root=path.resolve(__dirname,'..');const python=process.env.QAREEN_TEST_PYTHON||path.join(root,'.venv/bin/python');const temp=fs.mkdtempSync('/tmp/qareen-ui-data-');
 const env={...process.env,QAREEN_DATA_DIR:path.join(temp,'private'),QAREEN_DB_PATH:path.join(temp,'events.db')};
 execFileSync(python,['scripts/demo.py','--db',env.QAREEN_DB_PATH],{cwd:root,env});
 const service=spawn(python,['embedding_server.py','--demo'],{cwd:root,env,stdio:'pipe'});
 let browser;
 try {
  let ready=false;for(let i=0;i<50;i++){try{const r=await fetch('http://127.0.0.1:2846/health');if(r.ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}if(!ready)throw Error('Service not ready');
  const token=fs.readFileSync(path.join(env.QAREEN_DATA_DIR,'admin.token'),'utf8').trim();
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1280,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:2846/');await page.getByLabel('Admin token').fill(token);await page.getByRole('button',{name:'Unlock',exact:true}).click();await page.getByText('Connected to your local service.',{exact:true}).waitFor();
  await page.getByLabel('Question').fill('SQLite');await page.getByRole('button',{name:'Search locally',exact:true}).click();await page.waitForFunction(()=>document.getElementById('events').textContent.includes('SQLite'));
  await page.getByRole('button',{name:'Proposals',exact:true}).click();await page.getByLabel('Proposal content').fill('Demo edited reply, ready for review.');await page.getByRole('button',{name:'Save edits',exact:true}).click();await page.getByText(/revision 2/).waitFor();await page.getByRole('button',{name:'Approve this revision',exact:true}).click();await page.getByRole('button',{name:'Copy approved text',exact:true}).waitFor();
  if(await page.getByRole('button',{name:'Export Gmail draft',exact:true}).count())throw Error('WhatsApp proposal exposed Gmail export');
  await page.getByRole('button',{name:'Privacy & health',exact:true}).click();await page.getByLabel('Timezone',{exact:true}).fill('Asia/Karachi');await page.getByRole('button',{name:'Save settings',exact:true}).click();await page.getByText('Settings saved.',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow');
  if(errors.length)throw Error(errors.join('\n'));console.log('Dashboard smoke passed: login, retrieval, edit revision, approval, privacy settings, mobile layout; zero page errors.');
 } finally {if(browser)await browser.close();service.kill('SIGTERM');await new Promise(resolve=>{service.once('exit',resolve);setTimeout(resolve,3000)});fs.rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e.message);process.exit(1)});
