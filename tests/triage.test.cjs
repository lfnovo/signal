const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(mobile = true) {
  const handlers = {}, windowHandlers = {}, calls = [], classes = new Set();
  let observer;
  const node = () => ({ dataset: {}, innerHTML: '', listeners: {}, scrollTop: 0,
    classList: { remove() {} }, setAttribute() {},
    addEventListener(k, fn) { this.listeners[k] = fn; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    focus() {}, contains() { return false; }, append() {},
  });
  const list = node(), reader = node();
  let rows = ['a', 'b'].map(id => Object.assign(node(), {
    dataset: { sourceId: id }, offsetTop: 0,
    cloneNode() { return { innerHTML: id, classList: { remove() {} } }; },
  }));
  list.querySelectorAll = () => rows;
  const media = { matches: mobile, addEventListener(k, fn) { this.change = fn; } };
  const history = { state: null,
    pushState(state) { this.state = state; calls.push('push'); },
    replaceState(state) { this.state = state; },
    back() { this.state = null; calls.push('back'); windowHandlers.popstate(); },
  };
  const document = { body: { classList: { add(c) { classes.add(c); }, remove(c) { classes.delete(c); } } },
    querySelector: s => s === '#source-list' ? list : reader,
    addEventListener(k, fn) { handlers[k] = fn; }, createElement: node,
  };
  vm.runInNewContext(fs.readFileSync('src/signal_inbox/static/triage.js', 'utf8'), {
    document, history, location: { search: '?page=2' },
    window: { matchMedia: () => media, scrollY: 240,
      scrollTo(x, y) { calls.push(['scroll', y]); }, addEventListener(k, fn) { windowHandlers[k] = fn; } },
    sessionStorage: { getItem: () => 'b', setItem(k, v) { calls.push(['remember', v]); } },
    AbortController, MutationObserver: class { constructor(fn) { observer = fn; } observe() {} },
    fetch: async url => { calls.push(url); return { ok: true, text: async () => '<p>Summary</p>' }; },
    initTopicEditor() {},
  });
  return { calls, classes, reader, history, media,
    select: id => handlers['signal:select']({ detail: { id } }),
    click: id => list.listeners.click({ target: { closest: s => s === '.source-row' ? rows.find(r => r.dataset.sourceId === id) : null }, preventDefault() {} }),
    back: () => reader.listeners.click({ target: { closest: s => s === '[data-triage-back]' ? {} : null } }),
    mutate: () => observer(), remove: id => { rows = rows.filter(r => r.dataset.sourceId !== id); observer(); },
  };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
test('mobile starts list-only despite remembered desktop selection and background selection events', () => {
  const s = setup(); s.select('b'); s.mutate();
  assert.deepEqual(s.calls, []);
});
test('tap opens reader; back preserves list position and cancels pending response', async () => {
  const s = setup(); s.click('a'); s.back(); await settle();
  assert.equal(s.classes.has('triage-mobile-open'), false);
  assert.equal(s.reader.innerHTML, '');
  assert.ok(s.calls.some(c => Array.isArray(c) && c[0] === 'scroll' && c[1] === 240));
  assert.equal(s.history.state, null);
});
test('removing the open item returns to list without loading the next item', async () => {
  const s = setup(); s.click('a'); await settle(); s.remove('a'); s.select('b'); s.mutate();
  assert.equal(s.classes.has('triage-mobile-open'), false);
  assert.deepEqual(s.calls.filter(c => typeof c === 'string' && c.startsWith('/sources')), ['/sources/a/triage']);
});
test('desktop restores selection and advances after removal', async () => {
  const s = setup(false); await settle(); s.remove('b'); await settle();
  assert.deepEqual(s.calls.filter(c => typeof c === 'string' && c.startsWith('/sources')), ['/sources/b/triage', '/sources/a/triage']);
});
test('resizing to mobile does not open the desktop selection', async () => {
  const s = setup(false); await settle(); s.media.matches = true; s.media.change();
  assert.equal(s.classes.has('triage-mobile-open'), false);
});
