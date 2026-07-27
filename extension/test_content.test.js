const { describe, it, beforeEach } = require('node:test');
const assert = require('node:assert/strict');

// Re-implement the pure functions from content.js for isolated testing.
// These mirror the logic in content.js lines 1-59.

function createDeduplicator() {
  const recentLogs = new Map();

  function isDuplicate(text) {
    if (!text || !text.trim()) return true;
    const cleaned = text.trim().toLowerCase();
    const now = Date.now();
    if (recentLogs.has(cleaned)) {
      const lastTime = recentLogs.get(cleaned);
      if (now - lastTime < 3000) return true;
    }
    recentLogs.set(cleaned, now);
    if (recentLogs.size > 100) {
      const firstKey = recentLogs.keys().next().value;
      recentLogs.delete(firstKey);
    }
    return false;
  }

  return { isDuplicate, _map: recentLogs };
}

function isSensitiveInput(targetEl) {
  if (!targetEl) return false;
  const sensitivePatterns = ['password', 'pass', 'card', 'cvv', 'ssn', 'token', 'key', 'secret'];
  const type = (targetEl.type || '').toLowerCase();
  const id = (targetEl.id || '').toLowerCase();
  const name = (targetEl.name || '').toLowerCase();
  const className = (targetEl.className || '').toLowerCase();

  for (const pattern of sensitivePatterns) {
    if (type.includes(pattern) || id.includes(pattern) ||
        name.includes(pattern) || className.includes(pattern)) {
      return true;
    }
  }
  return false;
}

describe('isDuplicate', () => {
  it('returns true for empty string', () => {
    const { isDuplicate } = createDeduplicator();
    assert.equal(isDuplicate(''), true);
  });

  it('returns true for whitespace-only', () => {
    const { isDuplicate } = createDeduplicator();
    assert.equal(isDuplicate('   '), true);
  });

  it('returns true for null/undefined', () => {
    const { isDuplicate } = createDeduplicator();
    assert.equal(isDuplicate(null), true);
    assert.equal(isDuplicate(undefined), true);
  });

  it('returns false for first occurrence', () => {
    const { isDuplicate } = createDeduplicator();
    assert.equal(isDuplicate('hello world'), false);
  });

  it('returns true for same text within 3 seconds', () => {
    const { isDuplicate } = createDeduplicator();
    isDuplicate('test message');
    assert.equal(isDuplicate('test message'), true);
  });

  it('is case-insensitive', () => {
    const { isDuplicate } = createDeduplicator();
    isDuplicate('Hello World');
    assert.equal(isDuplicate('hello world'), true);
  });

  it('trims whitespace before comparison', () => {
    const { isDuplicate } = createDeduplicator();
    isDuplicate('  hello  ');
    assert.equal(isDuplicate('hello'), true);
  });

  it('allows different messages', () => {
    const { isDuplicate } = createDeduplicator();
    assert.equal(isDuplicate('message one'), false);
    assert.equal(isDuplicate('message two'), false);
  });

  it('evicts oldest entry at 100 items', () => {
    const { isDuplicate, _map } = createDeduplicator();
    for (let i = 0; i < 101; i++) {
      isDuplicate(`msg-${i}`);
    }
    assert.equal(_map.size, 100);
    assert.equal(_map.has('msg-0'), false);
    assert.equal(_map.has('msg-1'), true);
  });
});

describe('isSensitiveInput', () => {
  it('returns false for null element', () => {
    assert.equal(isSensitiveInput(null), false);
  });

  it('returns true for password type', () => {
    assert.equal(isSensitiveInput({ type: 'password', id: '', name: '', className: '' }), true);
  });

  it('returns false for text type', () => {
    assert.equal(isSensitiveInput({ type: 'text', id: 'username', name: 'user', className: 'input' }), false);
  });

  it('returns true when id contains "pass"', () => {
    assert.equal(isSensitiveInput({ type: 'text', id: 'user-pass', name: '', className: '' }), true);
  });

  it('returns true when name contains "card"', () => {
    assert.equal(isSensitiveInput({ type: 'text', id: '', name: 'card_number', className: '' }), true);
  });

  it('returns true when className contains "cvv"', () => {
    assert.equal(isSensitiveInput({ type: 'text', id: '', name: '', className: 'input-cvv' }), true);
  });

  it('returns true for ssn field', () => {
    assert.equal(isSensitiveInput({ type: 'text', id: 'ssn-input', name: '', className: '' }), true);
  });

  it('returns true for token field', () => {
    assert.equal(isSensitiveInput({ type: 'text', id: '', name: 'api_token', className: '' }), true);
  });

  it('returns true for secret field', () => {
    assert.equal(isSensitiveInput({ type: 'text', id: '', name: '', className: 'secret-field' }), true);
  });

  it('returns true for key field', () => {
    assert.equal(isSensitiveInput({ type: 'text', id: 'api-key', name: '', className: '' }), true);
  });

  it('is case-insensitive', () => {
    assert.equal(isSensitiveInput({ type: 'PASSWORD', id: '', name: '', className: '' }), true);
  });
});
