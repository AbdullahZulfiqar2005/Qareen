/* Capture rendered, opted-in content. Never inspect an input, draft, key, or click-to-send. */
'use strict';
(() => {
  const api = globalThis.browser || globalThis.chrome;
  const source = {'chatgpt.com': 'chatgpt', 'web.whatsapp.com': 'whatsapp', 'meet.google.com': 'google-meet'}[location.hostname];
  if (!source || window.top !== window) return;
  let enabled = false, observer, timer;
  const seen = new Map();
  const captionIds = new WeakMap();
  function emit(event) { api.runtime.sendMessage({type: 'capture', event}).catch(() => {}); }
  function scan() {
    if (!enabled || document.hidden) return;
    // ChatGPT message IDs remain stable while streaming; later revisions update the same event.
    if (source === 'chatgpt') {
      document.querySelectorAll('[data-message-id][data-message-author-role]').forEach(node => {
        const role = node.dataset.messageAuthorRole;
        if (!['user', 'assistant'].includes(role)) return;
        const conversation = location.pathname;
        const id = `${conversation}:${node.dataset.messageId}`;
        const content = node.innerText;
        if (!QareenCore.changed(seen.get(id), content)) return;
        seen.set(id, content);
        emit(QareenCore.event(source, conversation, node.dataset.messageId, content, {
          action: role === 'user' ? 'sent' : 'received', direction: role === 'user' ? 'outgoing' : 'incoming', actor_id: role, metadata: {source_time_unknown: true, rendered_only: true}
        }));
      });
    }
    // Captions are observations, not a complete transcript. DOM may recycle nodes.
    if (source === 'google-meet') {
      document.querySelectorAll('[jsname="YSxPC"]').forEach(node => {
        const content = QareenCore.clean(node.innerText);
        if (!content || seen.get(node) === content) return;
        if (!captionIds.has(node) || (seen.get(node) && !content.startsWith(seen.get(node)))) captionIds.set(node, crypto.randomUUID());
        seen.set(node, content);
        emit(QareenCore.event(source, location.pathname, captionIds.get(node), content, {action: 'caption', metadata: {partial: true}}));
      });
    }
    if (seen.size > 2000) seen.clear();
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(scan, 700); }
  function configure(state) {
    enabled = !state.paused && (state.enabled || []).includes(source);
    if (observer) observer.disconnect();
    clearTimeout(timer);
    if (enabled) { observer = new MutationObserver(schedule); observer.observe(document.body, {subtree: true, childList: true, characterData: true}); schedule(); }
  }
  api.storage.local.get(['enabled', 'paused']).then(configure);
  api.storage.onChanged.addListener(() => api.storage.local.get(['enabled', 'paused']).then(configure));
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('pagehide', () => { observer?.disconnect(); clearTimeout(timer); });
  // WhatsApp Web has no supported personal-account ingestion API. Explicit selection avoids guessing contact, date, or direction.
  if (source === 'whatsapp') {
    const button = document.createElement('button');
    button.textContent = 'Save selection to Qareen';
    button.style.cssText = 'position:fixed;right:20px;bottom:80px;z-index:2147483647;padding:10px;border-radius:8px;background:#183c35;color:white;';
    button.addEventListener('click', () => {
      if (!enabled) { alert('Enable WhatsApp in Qareen extension settings first.'); return; }
      const text = QareenCore.clean(window.getSelection()?.toString());
      if (!text) { alert('Select one received message first.'); return; }
      const contact = prompt('Exact contact identifier for this one-to-one conversation (do not use group messages):');
      if (!contact?.trim()) return;
      const id = crypto.randomUUID();
      emit(QareenCore.event(source, contact.trim(), id, text, {actor_id: contact.trim(), action: 'selected', direction: 'incoming', metadata: {user_selected: true, source_time_unknown: true}}));
      button.textContent = 'Selection queued'; setTimeout(() => { button.textContent = 'Save selection to Qareen'; }, 2000);
    });
    document.body.append(button);
  }
})();
