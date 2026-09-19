const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
  const handlers = {}, calls = [];
  let observer;
  const node = () => ({ dataset: {}, listeners: {}, classList: { toggle() {}, remove() {} }, closest: () => null,
    addEventListener(k, fn) { this.listeners[k] = fn; }, focus() { document.activeElement = this; }, scrollIntoView() {},
    showModal() { this.open = true; }, close() { this.open = false; this.listeners.close?.(); },
    querySelectorAll: () => [], append() {} });
  const body = node(), help = node(), preview = node(), toggle = node(), list = node();
  const nodes = { '#keyboard-help':help, '#quick-preview':preview, '#keyboard-enabled':toggle, '#source-list':list,
    '#preview-title':node(), '#preview-kind':node(), '#preview-body':node(), '#preview-open':node() };
  const rows = ['a', 'b'].map(id => {
    const row = node(); row.dataset.sourceId = id.repeat(64); row.matches = () => false;
    row.querySelector = selector => selector === '.source-title-link' ? { textContent:id, getAttribute: () => '/sources/' + id.repeat(64) } : { click() { calls.push(selector); }, disabled:false };
    return row;
  });
  const document = { activeElement:body, querySelector: s => nodes[s] || null,
    querySelectorAll: s => s === '.source-row, .topic-card' ? rows : s === 'dialog[open]' ? [help,preview].filter(n=>n.open) : [],
    addEventListener(k, fn) { handlers[k] = fn; }, createElement:node };
  const context = { document, location:{}, localStorage:{ getItem:()=>null, setItem(){} }, AbortController,
    api:async path => { calls.push(path); return { title:'Preview', kind:'summary', html:'<p>Summary</p>' }; },
    MutationObserver:class { constructor(fn) { observer=fn; } observe() {} } };
  vm.runInNewContext(fs.readFileSync('src/signal_inbox/static/keyboard.js','utf8'),context);
  const key = (key, extra={}) => { const event={key,target:document.activeElement,preventDefault(){this.prevented=true;},...extra}; handlers.keydown(event); return event; };
  return { key, calls, rows, document, nodes, context, observer:()=>observer() };
}

test('arrows select sources, Enter opens and digit keys navigate menus', () => {
  const app=setup(); app.key('ArrowDown'); assert.equal(app.document.activeElement,app.rows[0]);
  app.key('ArrowDown'); assert.equal(app.document.activeElement,app.rows[1]);
  app.key('Enter'); assert.equal(app.context.location.href,'/sources/'+'b'.repeat(64));
  app.key('3'); assert.equal(app.context.location.href,'/focus');
});
test('actions reuse collection and Focus controls, including confirmed delete control', () => {
  const app=setup(); app.key('ArrowDown'); app.key('l'); app.key('f'); app.key('Backspace',{metaKey:true});
  assert.deepEqual(app.calls,['[data-row-action="collection"][data-value="library"]','[data-row-action="focus"]','[data-row-action="delete"]']);
});
test('typing, composition, other dialogs and browser shortcuts are untouched', () => {
  const app=setup(); app.key('ArrowDown');
  const input={closest:()=>({})};
  assert.ok(!app.key('Backspace',{metaKey:true,target:input}).prevented);
  assert.ok(!app.key('l',{target:input}).prevented);
  assert.ok(!app.key('f',{ctrlKey:true}).prevented);
  assert.ok(!app.key('l',{isComposing:true}).prevented);
  app.nodes['#keyboard-help'].open=true; app.key('l');
  assert.equal(app.calls.length,0);
});
test('Space previews and closes, and navigation refreshes the preview', async () => {
  const app=setup(); app.key('ArrowDown'); app.key(' ');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(app.nodes['#quick-preview'].open,true);
  assert.equal(app.nodes['#preview-body'].innerHTML,'<p>Summary</p>');
  app.key('ArrowDown'); await new Promise(resolve=>setImmediate(resolve));
  assert.ok(app.calls.includes('/api/sources/'+'b'.repeat(64)+'/preview'));
  app.key(' '); assert.equal(app.nodes['#quick-preview'].open,false);
});
test('selection advances when a row leaves the list', () => {
  const app=setup();app.key('ArrowDown'); app.rows.shift();app.observer();app.key('i');
  assert.equal(app.calls[0],'[data-row-action="collection"][data-value="inbox"]');
});
test('single key shortcuts can be disabled while help stays accessible', () => {
  const app=setup();app.nodes['#keyboard-enabled'].checked=false;app.nodes['#keyboard-enabled'].listeners.change();
  app.key('1');assert.equal(app.context.location.href,undefined);
  app.key('?');assert.equal(app.nodes['#keyboard-help'].open,true);
});

test('Escape returns from source, with direct-link fallback and dialog priority', () => {
  const app=setup(); let backs=0;
  app.nodes['#source-detail']={dataset:{sourceId:'a'.repeat(64)}};
  app.nodes['.back-link']={getAttribute:()=>'/library'};
  app.context.history={length:2,back(){backs++;}};
  app.key('Escape'); assert.equal(backs,1);
  app.nodes['#quick-preview'].open=true;
  app.key('Escape'); assert.equal(backs,1); assert.equal(app.nodes['#quick-preview'].open,false);
  app.nodes['#keyboard-help'].open=true;
  app.key('Escape'); assert.equal(backs,1);
  app.nodes['#keyboard-help'].open=false;
  app.key('Escape',{target:{closest:()=>({})}}); assert.equal(backs,1);
  app.context.history.length=1;
  app.key('Escape'); assert.equal(app.context.location.href,'/library');
});

test('plain Backspace uses confirmed deletion and respects editing and disabled shortcuts', () => {
  const app=setup();app.key('ArrowDown');
  assert.equal(app.key('Backspace').prevented,true);
  assert.deepEqual(app.calls,['[data-row-action="delete"]']);
  app.key('Backspace',{repeat:true});
  app.key('Backspace',{target:{closest:()=>({})}});
  app.key('Backspace',{altKey:true});
  app.nodes['#keyboard-enabled'].checked=false;app.nodes['#keyboard-enabled'].listeners.change();
  app.key('Backspace');assert.equal(app.calls.length,1);
});
