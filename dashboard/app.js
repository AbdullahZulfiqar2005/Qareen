'use strict';
const $ = id => document.getElementById(id);
let token = sessionStorage.getItem('qareen-token') || '', offset = 0, settings;
const sources = ['window','command','history','tab','error','notification','file','keybind','query','browse','chatgpt','claude','gemini','grok','groq','whatsapp','gmail','google-meet','linkedin','instagram','reddit','github','chess','system'];
function el(tag, text, cls) { const n = document.createElement(tag); if(text !== undefined) n.textContent = text; if(cls) n.className = cls; return n; }
function notice(message) { $('notice').textContent = message; }
async function api(path, body) {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const result = await response.json(); if(!response.ok) throw new Error(result.error || `HTTP ${response.status}`); return result;
}
function action(node, callback) { node.addEventListener('click', async () => { node.disabled = true; try { await callback(); } catch(e) { notice(e.message); } finally { node.disabled = false; } }); }
function button(text, callback) { const b=el('button',text); action(b,callback); return b; }
function showEvent(event) {
  const card=el('article',undefined,'card');
  card.append(el('div',`[event:${event.id}] · ${event.event_type} · ${event.action} · ${event.timestamp} · ${event.app || ''}`,'meta'),el('div',event.content,'content'));
  if (event.event_type==='error' || ['gmail','whatsapp'].includes(event.event_type) && event.direction==='incoming') card.append(button('Draft proposal',async()=>{await api('/api/proposals/create',{event_id:event.id,type:event.event_type==='error'?'incident':'reply'}); notice('Proposal created. Open Proposals to review.'); await loadProposals();}));
  card.append(button('Delete event',async()=>{await api('/api/delete',{ids:[event.id]});card.remove();notice('Event and dependent proposals deleted.');}));
  return card;
}
async function loadEvents(append=false) {
  if(!append) { offset=0; $('events').replaceChildren(); }
  const data=await api('/api/events?'+new URLSearchParams({offset,limit:50,type:$('source').value,app:$('app').value}));
  data.events.forEach(e=>$('events').append(showEvent(e)));offset+=data.events.length;$('more').hidden=offset>=data.total;
}
async function loadProposals() {
  const data=await api('/api/proposals');$('proposals').replaceChildren();
  if(!data.proposals.length) $('proposals').append(el('p','No proposals yet. Select an incoming message or error in Memory to create one.'));
  for(const p of data.proposals) {
    const card=el('article',undefined,'card'); card.append(el('div',`${p.type} · ${p.status} · revision ${p.revision} · ${p.recipient_ids.join(', ') || 'local advice'}`,'meta'));
    const area=el('textarea');area.value=p.content;area.rows=7;area.setAttribute('aria-label','Proposal content');area.readOnly=!['proposed','approved'].includes(p.status);card.append(area);
    card.append(el('p',`Evidence: ${p.source_refs.map(id=>`[event:${id}]`).join(' ')} · Expires: ${p.expires_at} UTC`));
    const evidence=el('details'); evidence.append(el('summary','View source evidence'));
    action(evidence.firstChild,async()=>{if(evidence.children.length>1)return;const all=await api('/api/events?limit=500');for(const id of p.source_refs){let e=all.events.find(x=>x.id===id);if(!e)e=await api('/api/event?id='+id);if(e)evidence.append(showEvent(e));}});card.append(evidence);
    if(['proposed','approved'].includes(p.status)) {
      const decide=async decision=>{if(decision==='approved' && area.value!==p.content)throw new Error('Save your edits, then review the new revision before approving.');await api('/api/proposals/decide',{id:p.id,revision:p.revision,decision,content:area.value});await loadProposals();};
      card.append(button('Save edits',()=>decide('edited')),button('Approve this revision',()=>decide('approved')),button('Reject',()=>decide('rejected')));
      if(p.status==='approved' && p.type==='reply' && p.source==='gmail') card.append(button('Export Gmail draft',async()=>{const r=await api('/api/proposals/export',{id:p.id,revision:p.revision});notice(`Gmail draft created: ${r.draft_id}. It has not been sent.`);await loadProposals();}));
    }
    if(p.status==='approved') card.append(button('Copy approved text',async()=>{await navigator.clipboard.writeText(p.content);notice('Approved text copied.');}));
    if(p.export_state==='unknown') card.append(el('p','Export outcome is uncertain. Retry export to reconcile; inspect Gmail manually if no exact match is found.'));
    $('proposals').append(card);
  }
}
async function loadSettings() {
  settings=await api('/api/settings');for(const key of ['paused','auto_proposals','gmail_enabled'])$(key).checked=settings[key];for(const key of ['retention_days','daily_call_limit','timezone','gmail_label'])$(key).value=settings[key];$('writing').value=JSON.stringify(settings.preferences,null,2);
  $('source-settings').replaceChildren();for(const source of sources){const row=el('div');row.append(el('strong',source+' '));for(const [key,title,checked] of [['capture','capture',!settings.excluded_sources.includes(source)],['cloud','cloud',settings.cloud_sources.includes(source)]]){const label=el('label'),box=el('input');box.type='checkbox';box.id=`${key}-${source}`;box.checked=checked;label.append(box,document.createTextNode(title+' '));row.append(label);}$('source-settings').append(row);}
  const stats=await api('/api/stats');$('health').textContent=JSON.stringify(stats,null,2);
  const previous=$('app').value;$('app').replaceChildren(new Option('All apps',''));stats.top_apps.forEach(a=>$('app').add(new Option(a.label,a.label)));$('app').value=previous;
}
async function refresh(){if(!token)return;$('unlock').hidden=true;$('workspace').hidden=false;await Promise.all([loadEvents(),loadProposals(),loadSettings()]);notice('Connected to your local service.');}
$('login').addEventListener('submit',async e=>{e.preventDefault();token=$('token').value.trim();try{await refresh();sessionStorage.setItem('qareen-token',token);$('token').value='';}catch(err){notice(err.message);$('unlock').hidden=false;$('workspace').hidden=true;}});
action($('lock'),async()=>{sessionStorage.removeItem('qareen-token');location.reload();});action($('refresh'),refresh);
document.querySelectorAll('[data-panel]').forEach(b=>action(b,async()=>{document.querySelectorAll('.panel').forEach(p=>{p.hidden=p.id!==b.dataset.panel;});}));
sources.forEach(s=>$('source').add(new Option(s,s)));action($('timeline-load'),()=>loadEvents());action($('more'),()=>loadEvents(true));
async function ask(local){const q=$('query').value;const r=local?await api('/api/search?'+new URLSearchParams({q,source:$('source').value,app:$('app').value})):await api('/api/ask',{query:q,source:$('source').value,app:$('app').value});$('answer').textContent=r.answer||'';$('coverage').textContent=JSON.stringify(r.coverage);$('events').replaceChildren(...r.results.map(showEvent));$('more').hidden=true;}
$('ask').addEventListener('submit',async e=>{e.preventDefault();try{await ask(false);}catch(err){notice(err.message);}});action($('search'),()=>ask(true));
$('preferences').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/settings',{paused:$('paused').checked,auto_proposals:$('auto_proposals').checked,gmail_enabled:$('gmail_enabled').checked,retention_days:Number($('retention_days').value),daily_call_limit:Number($('daily_call_limit').value),timezone:$('timezone').value,gmail_label:$('gmail_label').value,preferences:JSON.parse($('writing').value),excluded_sources:sources.filter(s=>!$(`capture-${s}`).checked),cloud_sources:sources.filter(s=>$(`cloud-${s}`).checked)});notice('Settings saved.');await loadSettings();}catch(err){notice(err.message);}});
action($('gmail-sync'),async()=>{await api('/api/gmail/sync',{});notice('Gmail sync completed.');await refresh();});
action($('export'),async()=>{const r=await api('/api/export');const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='qareen-events.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
action($('delete-all'),async()=>{if(!confirm('Delete all local events, vectors, feedback and proposals? Gmail drafts already exported remain in Gmail. Pause the extension and clear its pending queue first.'))return;await api('/api/delete',{all:true});await refresh();notice('Local events and dependent data deleted.');});
refresh().catch(e=>{notice(e.message);$('unlock').hidden=false;$('workspace').hidden=true;});
