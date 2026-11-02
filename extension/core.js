/* Shared production helpers; also exercised by node:test. */
(function (root) {
  'use strict';
  function clean(value) { return String(value || '').replace(/\s+/g, ' ').trim().slice(0, 50000); }
  function event(source, conversation, id, content, extra = {}) {
    return {event_type: source, app: source, conversation_id: conversation,
      source_event_id: `${conversation}:${id}`, content: clean(content),
      occurred_at: new Date().toISOString(), action: 'observed', direction: 'unknown',
      revision_update: true, ...extra};
  }
  function changed(previous, current) { return !!clean(current) && clean(previous) !== clean(current); }
  function prune(queue, now = Date.now()) {
    let size = 0;
    return queue.filter(x => now - x.queuedAt < 7 * 86400000).reverse().filter(x => {
      size += JSON.stringify(x).length * 2;
      return size <= 4000000;
    }).slice(0, 500).reverse();
  }
  const api = {clean, event, changed, prune};
  if (typeof module !== 'undefined') module.exports = api;
  else root.QareenCore = api;
})(globalThis);
