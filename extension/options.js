'use strict';
const api = globalThis.browser || globalThis.chrome;
const sources = ['chatgpt', 'whatsapp', 'google-meet'];
async function load() {
  const s = await api.storage.local.get(null);
  document.getElementById('token').value = s.token || '';
  sources.forEach(x => { document.getElementById(x).checked = (s.enabled || []).includes(x); });
  document.getElementById('paused').checked = !!s.paused;
  document.getElementById('status').textContent = JSON.stringify({pending: (s.outbox || []).length, lastSuccess: s.lastSuccess, lastError: s.lastError, lastDropped: s.dropped}, null, 2);
}
document.getElementById('settings').addEventListener('submit', async e => {
  e.preventDefault();
  await api.storage.local.set({token: document.getElementById('token').value.trim(), enabled: sources.filter(x => document.getElementById(x).checked), paused: document.getElementById('paused').checked});
  await load();
});
document.getElementById('clear').addEventListener('click', async () => { await api.storage.local.set({outbox: []}); await load(); });
load();
