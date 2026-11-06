const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const script = fs.readFileSync('src/signal_inbox/static/oauth.js', 'utf8');

test('consent submits once without disabling the approve/deny submitter', () => {
  let submit;
  const form = {
    addEventListener(name, handler) { assert.equal(name, 'submit'); submit = handler; },
    setAttribute(name, value) { assert.equal(name, 'aria-busy'); assert.equal(value, 'true'); },
  };
  vm.runInNewContext(script, {
    document: { querySelector: selector => selector === '#oauth-consent' ? form : null },
  });
  let prevented = 0;
  const event = { preventDefault() { prevented++; } };
  submit(event);
  assert.equal(prevented, 0);
  submit(event);
  assert.equal(prevented, 1);
});

test('handoff navigates to the exact server-generated callback without another approval', () => {
  const href = 'https://www.cursor.com/agents/mcp/oauth/callback?code=test&state=a%26b&iss=https%3A%2F%2Fsignal.example%2F';
  const navigations = [];
  vm.runInNewContext(script, {
    document: { querySelector: selector => selector === '#oauth-callback' ? { href } : null },
    window: { location: { replace: url => navigations.push(url) } },
  });
  assert.deepEqual(navigations, [href]);
});
