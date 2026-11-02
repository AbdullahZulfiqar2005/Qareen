'use strict';
if (typeof importScripts === 'function') importScripts('core.js');
const api = globalThis.browser || globalThis.chrome;
let chain = Promise.resolve();
function serial(fn) { const next = chain.then(fn); chain = next.catch(() => {}); return next; }
async function receive(message, sender) {
  if (message.type !== 'capture' || !sender.tab || sender.tab.incognito) return {ignored: true};
  const config = await api.storage.local.get(['enabled', 'paused']);
  const hosts = {'chatgpt.com': 'chatgpt', 'web.whatsapp.com': 'whatsapp', 'meet.google.com': 'google-meet'};
  const source = hosts[new URL(sender.url || sender.tab.url).hostname];
  if (!source || source !== message.event.event_type || config.paused || !(config.enabled || []).includes(source)) return {ignored: true};
  return serial(async () => {
    const saved = await api.storage.local.get('outbox');
    const queue = saved.outbox || [];
    const item = {event: message.event, queuedAt: Date.now()};
    const index = queue.findIndex(x => x.event.source_event_id === item.event.source_event_id && x.event.event_type === source);
    if (index >= 0) queue[index] = item; else queue.push(item);
    const kept = QareenCore.prune(queue);
    await api.storage.local.set({outbox: kept, dropped: queue.length - kept.length});
    return {queued: true};
  });
}
api.runtime.onMessage.addListener((message, sender, reply) => {
  receive(message, sender).then(reply, () => reply({error: 'Capture failed'}));
  return true;
});
async function flush() {
  await serial(async () => {
    const state = await api.storage.local.get(['token', 'outbox', 'paused']);
    if (!state.token || state.paused) return;
    const queue = QareenCore.prune(state.outbox || []);
    for (let count = 0; queue.length && count < 20; count++) {
      try {
        const response = await fetch('http://127.0.0.1:2846/ingest', {
          method: 'POST', headers: {'Content-Type': 'application/json', Authorization: `Bearer ${state.token}`},
          body: JSON.stringify(queue[0].event), signal: AbortSignal.timeout(10000)
        });
        if (!response.ok) throw new Error(`Service returned ${response.status}`);
        queue.shift();
        await api.storage.local.set({outbox: queue, lastSuccess: new Date().toISOString(), lastError: ''});
      } catch (error) {
        await api.storage.local.set({lastError: error.message}); break;
      }
    }
    await api.storage.local.set({outbox: queue});
  });
}
api.alarms.create('flush', {periodInMinutes: 1});
api.alarms.onAlarm.addListener(() => { flush().catch(() => {}); });
api.runtime.onStartup.addListener(() => { flush().catch(() => {}); });
