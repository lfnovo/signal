(() => {
  const header = document.querySelector('.topbar');
  if (!header) return;
  const reader = document.querySelector('#triage-reader');
  const mobile = window.matchMedia('(max-width: 760px)');
  let anchor = 0;
  const readerOpen = () => document.body.classList.contains('triage-mobile-open');
  const position = () => Math.max(0, readerOpen() ? reader.scrollTop : window.scrollY);
  function reveal() {
    document.body.classList.remove('mobile-header-hidden');
    anchor = position();
  }
  function measure() {
    document.body.style.setProperty('--mobile-header-height', header.getBoundingClientRect().height + 'px');
  }
  function scroll(event) {
    if (!mobile.matches || (event.currentTarget === reader) !== readerOpen()) return;
    const y = position();
    if (y <= 12 || header.contains(document.activeElement)) { reveal(); return; }
    // Accumulate small movements, but ignore touch jitter and overscroll at the top.
    if (Math.abs(y - anchor) < 12) return;
    document.body.classList.toggle('mobile-header-hidden', y > anchor);
    anchor = y;
  }
  window.addEventListener('scroll', scroll, { passive: true });
  reader?.addEventListener('scroll', scroll, { passive: true });
  header.addEventListener('focusin', reveal);
  mobile.addEventListener('change', () => { measure(); reveal(); });
  let wasOpen = readerOpen();
  new MutationObserver(() => {
    if (readerOpen() === wasOpen) return;
    wasOpen = readerOpen(); reveal();
  }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  new ResizeObserver(measure).observe(header);
  measure();
})();
