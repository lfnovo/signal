const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function popup({ url = 'https://example.com/', created = true, failure = false, live = false } = {}) {
  const elements = new Map();
  const calls = [];
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, { disabled: selector === '#capture', textContent: '', href: '', listeners: {}, addEventListener(name, fn) { this.listeners[name] = fn; } });
    return elements.get(selector);
  };
  const context = {
    document: { querySelector: element },
    chrome: { storage: { local: { get: async () => ({ apiUrl: 'http://127.0.0.1:8020' }) } }, tabs: { query: async () => [{ url, title: 'Example' }] } },
    AbortSignal, TypeError,
    fetch: async (target, options) => {
      calls.push({ target, options });
      if (failure) throw new TypeError('Failed to fetch');
      if (live) return fetch(target, options);
      return { ok: true, json: async () => ({ created, source: { id: 'abc' } }) };
    },
  };
  vm.runInNewContext(fs.readFileSync('extension/popup.js', 'utf8'), context);
  return { element, calls };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));

test('captures the active tab URL into the shared API', async () => {
  const app = popup(); await settle();
  assert.equal(app.element('#capture').disabled, false);
  await app.element('#capture').listeners.click({ target: app.element('#capture') });
  assert.equal(app.calls[0].target, 'http://127.0.0.1:8020/api/sources');
  assert.deepEqual(JSON.parse(app.calls[0].options.body), { url: 'https://example.com/' });
  assert.match(app.element('#status').textContent, /Saved/);
  assert.equal(app.element('#inbox').href, 'http://127.0.0.1:8020/sources/abc');
});
test('reports a duplicate instead of claiming a new capture', async () => {
  const app = popup({ created: false }); await settle();
  await app.element('#capture').listeners.click({ target: app.element('#capture') });
  assert.match(app.element('#status').textContent, /Already/);
});
test('does not capture chrome internal pages', async () => {
  const app = popup({ url: 'chrome://extensions' }); await settle();
  assert.equal(app.element('#capture').disabled, true);
  assert.equal(app.calls.length, 0);
  assert.match(app.element('#status').textContent, /HTTP or HTTPS/);
});
test('explains an unavailable local server and allows retry', async () => {
  const app = popup({ failure: true }); await settle();
  await app.element('#capture').listeners.click({ target: app.element('#capture') });
  assert.equal(app.element('#capture').disabled, false);
  assert.match(app.element('#status').textContent, /uv run signal serve/);
});
test('manifest permissions are limited to active tab and localhost', () => {
  const manifest = JSON.parse(fs.readFileSync('extension/manifest.json', 'utf8'));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ['activeTab', 'storage']);
  assert.deepEqual(manifest.host_permissions, ['http://127.0.0.1/*', 'http://localhost/*']);
  for (const file of [manifest.action.default_popup, manifest.options_page]) assert.ok(fs.existsSync('extension/' + file));
});
test('popup captures through the running real Signal API', { skip: !process.env.SIGNAL_LIVE_EXTENSION }, async () => {
  const app = popup({ live: true }); await settle();
  await app.element('#capture').listeners.click({ target: app.element('#capture') });
  assert.match(app.element('#status').textContent, /Saved|Already/);
  assert.match(app.element('#inbox').href, /\/sources\/[a-f0-9]{64}$/);
});
