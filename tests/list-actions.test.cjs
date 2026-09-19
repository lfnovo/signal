const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function click(action, { confirmDelete = true, pressed = false, fail = false } = {}) {
  const calls = [];
  const list = { dataset: {}, innerHTML: 'before', cloneNode() { return { innerHTML: this.innerHTML, querySelectorAll: () => [] }; }, listeners: {}, setAttribute() {}, addEventListener(type, fn) { this.listeners[type] = fn; } };
  const toast = { classList: { toggle() {} } };
  const button = { disabled: false, dataset: { rowAction: action, value: 'library' }, getAttribute: () => String(pressed), closest: () => row };
  const row = { dataset: { sourceId: 'a'.repeat(64) }, querySelector: () => ({ textContent: 'A test find' }), querySelectorAll: () => [button] };
  const count = {}, pagination = {};
  const document = {
    querySelector: selector => ({ '#source-list': list, '#toast': toast, '.count': count, '.pagination': pagination }[selector] || null),
    querySelectorAll: () => [],
  };
  vm.runInNewContext(fs.readFileSync('src/signal_inbox/static/app.js', 'utf8'), {
    document, location: { pathname: '/', search: '' }, confirm: () => confirmDelete,
    setInterval() {}, setTimeout() {}, clearTimeout() {},
    DOMParser: class { parseFromString() { return { querySelector: () => ({ innerHTML: 'after', textContent: '1' }) }; } },
    fetch: async (path, options) => {
      calls.push({ path, options });
      return { ok: !fail, json: async () => fail ? { detail: 'Not saved' } : {}, text: async () => 'html' };
    },
  });
  await list.listeners.click({ target: { closest: () => button }, preventDefault() {}, stopPropagation() {} });
  return { calls, list, button, toast };
}

test('row collection toggle saves and refreshes without navigating', async () => {
  const result = await click('collection');
  assert.equal(result.calls[0].path, '/api/sources/' + 'a'.repeat(64) + '/collection');
  assert.deepEqual(JSON.parse(result.calls[0].options.body), { collection: 'library' });
  assert.equal(result.list.innerHTML, 'after');
  assert.equal(result.list.dataset.mutating, 'false');
});
test('row Focus toggle uses current state', async () => {
  const result = await click('focus', { pressed: true });
  assert.deepEqual(JSON.parse(result.calls[0].options.body), { focused: false });
});
test('row deletion requires confirmation', async () => {
  assert.equal((await click('delete', { confirmDelete: false })).calls.length, 0);
  const result = await click('delete');
  assert.equal(result.calls[0].options.method, 'DELETE');
  assert.deepEqual(JSON.parse(result.calls[0].options.body), { confirm: true });
});
test('failed row changes retain the list and restore controls', async () => {
  const result = await click('collection', { fail: true });
  assert.equal(result.list.innerHTML, 'before');
  assert.equal(result.button.disabled, false);
  assert.equal(result.toast.textContent, 'Not saved');
});
