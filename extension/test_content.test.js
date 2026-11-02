const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const core = require('./core.js');
test('event identity scopes conversation and preserves observation',()=>{
  const a=core.event('whatsapp','alice','m1','hello');const b=core.event('whatsapp','bob','m1','hello');
  assert.notEqual(a.source_event_id,b.source_event_id);assert.equal(a.action,'observed');assert.equal(a.direction,'unknown');
});
test('streaming late revision remains eligible',()=>{assert.ok(core.changed('partial','partial and final'));assert.ok(!core.changed('same','same'));assert.ok(!core.changed('old',''));});
test('queue bounds and expiry',()=>{const now=Date.now();const items=Array.from({length:600},(_,i)=>({queuedAt:now,event:{content:'x',source_event_id:i}}));assert.equal(core.prune(items,now).length,500);assert.equal(core.prune([{queuedAt:0,event:{}}],now).length,0);});
test('production observer scans all rendered messages and updates streaming text',async()=>{
  const sent=[];const nodes=[{dataset:{messageAuthorRole:'user',messageId:'1'},innerText:'question'},{dataset:{messageAuthorRole:'assistant',messageId:'2'},innerText:'partial'}];let callback;
  const context={QareenCore:core,location:{hostname:'chatgpt.com',pathname:'/c/1'},window:{},document:{hidden:false,body:{},querySelectorAll:()=>nodes,addEventListener:()=>{}},MutationObserver:class{constructor(fn){callback=fn;}observe(){}disconnect(){}},setTimeout:fn=>{fn();return 1;},clearTimeout:()=>{},browser:{runtime:{sendMessage:event=>{sent.push(event.event);return Promise.resolve();}},storage:{local:{get:()=>Promise.resolve({enabled:['chatgpt']})},onChanged:{addListener:()=>{}}}}};context.window.top=context.window;context.window.addEventListener=()=>{};
  vm.runInNewContext(fs.readFileSync(require.resolve('./content.js'),'utf8'),context);await new Promise(setImmediate);
  assert.equal(sent.length,2);assert.equal(sent[0].action,'sent');assert.equal(sent[1].action,'received');
  nodes[1].innerText='partial with final text';callback();assert.equal(sent.length,3);assert.equal(sent[1].source_event_id,sent[2].source_event_id);
});
test('manifest excludes all-URL access and private windows',()=>{const m=require('./manifest.json');assert.equal(m.incognito,'not_allowed');assert.ok(!JSON.stringify(m).includes('<all_urls>'));assert.ok(m.background.scripts.includes('background.js'));});
test('dashboard has no dynamic HTML sinks',()=>{const code=fs.readFileSync(require.resolve('../dashboard/app.js'),'utf8');assert.ok(!/innerHTML|insertAdjacentHTML|document\.write/.test(code));});
