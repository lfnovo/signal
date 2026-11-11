const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function setup() {
  const classes = new Set(), listeners = {}, reader = { scrollTop: 0, addEventListener(k, fn) { this.scroll = fn; } };
  const header = { getBoundingClientRect: () => ({ height: 164 }), contains: el => el === header,
    addEventListener(k, fn) { this.focus = fn; } };
  const media = { matches: true, addEventListener(k, fn) { this.change = fn; } };
  let mutated, measured;
  const body = { style: { setProperty(k, v) { measured = v; } }, classList: {
    contains: c => classes.has(c), remove: c => classes.delete(c),
    toggle(c, on) { if (on) classes.add(c); else classes.delete(c); },
  } };
  const document = { body, activeElement: null, querySelector: s => s === '.topbar' ? header : reader };
  const window = { scrollY: 0, matchMedia: () => media, addEventListener(k, fn) { listeners[k] = fn; } };
  vm.runInNewContext(fs.readFileSync('src/signal_inbox/static/mobile-header.js', 'utf8'), {
    document, window,
    MutationObserver: class { constructor(fn) { mutated = fn; } observe() {} },
    ResizeObserver: class { observe() {} },
  });
  return { classes, document, header, media, measured,
    scroll(y) { window.scrollY = y; listeners.scroll({ currentTarget: window }); },
    open() { classes.add('triage-mobile-open'); mutated(); },
    read(y) { reader.scrollTop = y; reader.scroll({ currentTarget: reader }); },
  };
}
test('mobile header hides down, reveals up, and ignores small jitter', () => {
  const s = setup(); assert.equal(s.measured, '164px');
  s.scroll(50); assert.ok(s.classes.has('mobile-header-hidden'));
  s.scroll(47); assert.ok(s.classes.has('mobile-header-hidden'));
  s.scroll(30); assert.ok(!s.classes.has('mobile-header-hidden'));
});
test('reader has an independent scroll position and resets the header on opening', () => {
  const s = setup(); s.scroll(400); s.open();
  assert.ok(!s.classes.has('mobile-header-hidden'));
  s.scroll(800); assert.ok(!s.classes.has('mobile-header-hidden'));
  s.read(100); assert.ok(s.classes.has('mobile-header-hidden'));
  s.read(60); assert.ok(!s.classes.has('mobile-header-hidden'));
});
test('search focus and desktop keep the header visible', () => {
  const s = setup(); s.scroll(100); s.document.activeElement = s.header; s.header.focus();
  s.scroll(200); assert.ok(!s.classes.has('mobile-header-hidden'));
  s.document.activeElement = null; s.media.matches = false; s.media.change(); s.scroll(500);
  assert.ok(!s.classes.has('mobile-header-hidden'));
});
