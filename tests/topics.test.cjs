const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function search(catalog, attached, query, options = {}) {
  function element() {
    return {
      children: [], listeners: {}, attributes: {}, value: '', focus() {},
      append(child) { this.children.push(child); },
      replaceChildren() { this.children = []; },
      setAttribute(key, value) { this.attributes[key] = value; },
      removeAttribute(key) { delete this.attributes[key]; },
      addEventListener(key, listener) { this.listeners[key] = listener; },
      querySelectorAll() { return this.children.filter(child => child.attributes.role === 'option'); },
    };
  }
  const nodes = Object.fromEntries(['#topic-query', '#topic-results', '#inline-topic-chips', '#topic-edit-status'].map(id => [id, element()]));
  const root = element();
  root.dataset = { sourceId: 'a'.repeat(64), catalog: JSON.stringify(catalog), attached: JSON.stringify(attached) };
  root.querySelector = id => nodes[id];
  const document = { querySelector: () => root, createElement: element };
  vm.runInNewContext(fs.readFileSync('src/signal_inbox/static/topics.js', 'utf8'), { document, api: options.api, toast: options.toast || (() => {}) });
  nodes['#topic-query'].value = query;
  nodes['#topic-query'].listeners.input();
  if (options.inspect) return nodes;
  return nodes['#topic-results'].children.map(child => child.textContent);
}
const systems = { id: 'systems', name: 'Systems Thinking', official: true, manual: true };

test('can create Thinking when Systems Thinking is already attached', () => {
  assert.deepEqual(search([systems], [systems], 'Thinking'), ['+ Create “Thinking”']);
});
test('partial suggestions and creation are offered together', () => {
  assert.deepEqual(search([systems], [], 'Thinking'), ['✓ Systems Thinking', '+ Create “Thinking”']);
});
test('exact existing topic is offered for attachment, without duplicate creation', () => {
  assert.deepEqual(search([systems], [], ' systems   THINKING '), ['✓ Systems Thinking']);
});
test('exact attached topic cannot be created again', () => {
  assert.deepEqual(search([systems], [systems], 'systems thinking'), ['Matching topics are already added.']);
});

test('suggestion symbol promotes globally without navigating, then renders an official chip', async () => {
  const calls=[];
  const topic={...systems,official:false};
  const nodes=search([topic],[topic],'',{inspect:true,api:async (...args)=>calls.push(args)});
  const chip=nodes['#inline-topic-chips'].children[0];
  const approve=chip.children.find(child=>child.className==='topic-approve');
  assert.equal(approve.attributes['aria-label'],'Make Systems Thinking official everywhere');
  await approve.listeners.click();
  assert.equal(calls[0][0],'/api/topics/systems/approve');
  assert.equal(calls[0][1].method,'POST');
  const updated=nodes['#inline-topic-chips'].children[0];
  assert.match(updated.className,/official/);
  assert.equal(updated.children[0].textContent,'✓ Systems Thinking');
});
test('failed promotion keeps the topic suggested and reports the failure', async () => {
  const errors=[];
  const topic={...systems,official:false};
  const nodes=search([topic],[topic],'',{inspect:true,api:async ()=>{throw new Error('Offline');},toast:message=>errors.push(message)});
  await nodes['#inline-topic-chips'].children[0].children[0].listeners.click();
  assert.match(nodes['#inline-topic-chips'].children[0].className,/suggested/);
  assert.deepEqual(errors,['Offline']);
});
