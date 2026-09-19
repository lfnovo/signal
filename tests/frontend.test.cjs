const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function frontend({ rejectRetry = false, storage = new Map() } = {}) {
  const nodes = new Map();
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      dataset: { sourceId: 'a'.repeat(64), status: 'ready' }, value: 'Português', textContent: '',
      disabled: false, hidden: false, innerHTML: '', scrollHeight: 100, children: [],
      matches(value) { return value === 'input, textarea' && ['#url', '#question'].includes(selector); },
      append(child) { this.children.push(child); }, replaceChildren() { this.children = []; },
      listeners: {}, classList: { toggle() {} },
      addEventListener(name, listener) { this.listeners[name] = listener; },
      querySelectorAll() { return [node('#save-button')]; },
      querySelector() { return node('#save-button'); },
      cloneNode() { return { innerHTML: this.innerHTML, querySelectorAll: () => [] }; },
      setAttribute() {}, getAttribute(name) { return name === 'href' ? '/library' : null; }, focus() {}, reset() {},
      requestSubmit() { this.submitted = (this.submitted || 0) + 1; },
    });
    return nodes.get(selector);
  }
  for (const prefix of ['llm', 'embedding']) {
    node('#' + prefix + '-provider').dataset.modelPrefix = prefix;
    node('#' + prefix + '-find').dataset = { modelPrefix: prefix, modelKind: prefix === 'llm' ? 'language' : 'embedding' };
  }
  node('#llm-provider').value = 'google'; node('#llm-model').value = 'gemini-2.5-flash';
  node('#embedding-provider').value = 'google'; node('#embedding-model').value = 'gemini-embedding-001';
  node('#url-engine').value = 'simple'; node('#document-engine').value = 'docling';
  node('#stt-provider').value = 'google'; node('#stt-model').value = 'gemini-2.5-flash';
  node('#url').dataset.copy = 'url';
  const calls = [];
  const context = {
    document: { querySelector: node, hidden: false, createElement: () => ({ value: '' }),
      querySelectorAll(selector) {
        if (selector === 'select[data-model-prefix]') return [node('#llm-provider'), node('#embedding-provider')];
        if (selector === '.find-models') return [node('#llm-find'), node('#embedding-find')];
        if (selector === '[data-copy]') return [node('#url')];
        if (selector === '#delete-source') return [node('#delete-source')];
        return [];
      },
    },
    sessionStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    location: { pathname: '/sources/' + 'a'.repeat(64), search: '', reload() {}, replace(value) { this.replaced = value; } },
    setInterval() {}, setTimeout() {}, clearTimeout() {}, confirm: () => true,
    DOMParser: class { parseFromString() { return { querySelector: () => ({ innerHTML: 'saved conversation' }) }; } },
    fetch: async (path, options) => {
      calls.push({ path, options });
      const failed = rejectRetry && path.endsWith('/retry');
      return { ok: !failed, json: async () => failed ? { detail: 'Try again' } : path.startsWith('/api/models') ? { models: ['available-model'] } : {}, text: async () => '<html></html>' };
    },
  };
  vm.runInNewContext(fs.readFileSync('src/signal_inbox/static/app.js', 'utf8'), context);
  async function fire(selector, type) {
    const event = { currentTarget: node(selector), target: node(selector), preventDefault() {} };
    const result = node(selector).listeners[type](event);
    // Browsers clear currentTarget immediately after synchronous dispatch.
    event.currentTarget = null;
    await result;
  }
  return { node, fire, calls, context, pickCopy: context.pickCopy, poll: context.poll };
}

test('preference form re-enables controls after asynchronous submit', async () => {
  const app = frontend();
  await app.fire('#preferences-form', 'submit');
  assert.equal(app.node('#save-button').disabled, false);
  assert.equal(app.calls[0].path, '/api/preferences');
});
test('clear chat re-enables its button after asynchronous delete', async () => {
  const app = frontend();
  await app.fire('#clear-chat', 'click');
  assert.equal(app.node('#clear-chat').disabled, false);
  assert.equal(app.calls[0].options.method, 'DELETE');
  assert.equal(app.node('#chat-history').innerHTML, 'saved conversation');
});
test('retry failure re-enables its button and displays the error', async () => {
  const app = frontend({ rejectRetry: true });
  await app.fire('#retry-button', 'click');
  assert.equal(app.node('#retry-button').disabled, false);
  assert.equal(app.node('#toast').textContent, 'Try again');
});

test('Alt Enter submits the source chat while plain Enter keeps editing', async () => {
  const app = frontend();
  const plain = { key: 'Enter', altKey: false, preventDefault() { this.prevented = true; } };
  app.node('#question').listeners.keydown(plain);
  assert.equal(plain.prevented, undefined);
  const shortcut = { key: 'Enter', altKey: true, preventDefault() { this.prevented = true; } };
  app.node('#question').listeners.keydown(shortcut);
  assert.equal(shortcut.prevented, true);
  assert.equal(app.node('#chat-form').submitted, 1);
});

test('source deletion returns to the list that opened it', async () => {
  const storage = new Map();
  const app = frontend({ storage });
  app.context.location.pathname = '/focus';
  app.context.location.search = '?page=2';
  app.context.rememberSourceReturn('a'.repeat(64));
  app.context.location.pathname = '/sources/' + 'a'.repeat(64);
  app.context.location.search = '';
  await app.fire('#delete-source', 'click');
  assert.equal(app.context.location.replaced, '/focus?page=2');
  assert.equal(app.calls[0].options.method, 'DELETE');
});


test('preferences submit all model fields and keep the configured conversation language', async () => {
  const app = frontend();
  app.node('#llm-provider').value = 'openai'; app.node('#llm-model').value = 'custom-model';
  await app.fire('#preferences-form', 'submit');
  assert.deepEqual(JSON.parse(app.calls[0].options.body), {
    language: 'Português', llm_provider: 'openai', llm_model: 'custom-model',
    embedding_provider: 'google', embedding_model: 'gemini-embedding-001',
    url_engine: 'simple', document_engine: 'docling', stt_provider: 'google', stt_model: 'gemini-2.5-flash',
  });
});
test('changing provider clears the incompatible model and loads suggestions on demand', async () => {
  const app = frontend();
  assert.equal(app.calls.length, 0);
  app.node('#llm-provider').value = 'openai';
  await app.fire('#llm-provider', 'change');
  assert.equal(app.node('#llm-model').value, '');
  await app.fire('#llm-find', 'click');
  assert.equal(app.calls[0].path, '/api/models?provider=openai&kind=language');
  assert.equal(app.node('#llm-models').children[0].value, 'available-model');
  assert.equal(app.node('#llm-find').disabled, false);
});
test('copy rotates without immediate repeats, and polling never changes a typing hint', async () => {
  const storage = new Map();
  const first = frontend({ storage });
  const initial = first.node('#url').placeholder;
  first.node('#url').value = 'https://typing.example';
  await first.poll();
  assert.equal(first.node('#url').placeholder, initial);
  assert.equal(first.node('#url').value, 'https://typing.example');
  const next = frontend({ storage });
  assert.notEqual(next.node('#url').placeholder, initial);
  let previous = next.pickCopy('saved');
  for (let i = 0; i < 20; i++) {
    const selected = next.pickCopy('saved');
    assert.notEqual(selected, previous); previous = selected;
  }
});

test('regenerate submits optional steering separately from extraction settings', async () => {
  const app = frontend();
  app.node('#summary-steering').value = 'Focus on practical applications';
  await app.fire('#regenerate-form', 'submit');
  assert.equal(app.calls[0].path, '/api/sources/' + 'a'.repeat(64) + '/regenerate');
  assert.deepEqual(JSON.parse(app.calls[0].options.body), { steering: 'Focus on practical applications' });
  assert.equal(app.node('#save-button').disabled, false);
});
