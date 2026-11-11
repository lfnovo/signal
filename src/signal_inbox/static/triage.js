(() => {
  const list = document.querySelector('#source-list');
  const reader = document.querySelector('#triage-reader');
  if (!list || !reader) return;
  let selectedId = null, selectedIndex = 0, version = 0, request, signature = '';
  const selectionKey = 'signal-inbox-selection:' + location.search;
  const mobile = window.matchMedia('(max-width: 760px)');
  let mobileOpen = false, listScroll = 0, returnId = null;
  const backButton = '<button class="text-button triage-back" type="button" data-triage-back>← Inbox</button>';
  function closeMobile() {
    mobileOpen = false;
    document.body.classList.remove('triage-mobile-open');
    selectedId = null; signature = ''; version++; request?.abort();
    reader.innerHTML = ''; reader.setAttribute('aria-busy', 'false');
    window.scrollTo(0, listScroll);
    rows().find(row => row.dataset.sourceId === returnId)?.focus({ preventScroll: true });
  }
  function backToList() {
    if (!mobileOpen) return;
    closeMobile();
    if (history.state?.signalInboxReader) history.back();
  }
  function openMobile(id, push = true) {
    if (!rows().some(row => row.dataset.sourceId === id)) return;
    if (!mobileOpen) listScroll = window.scrollY;
    returnId = id;
    mobileOpen = true;
    document.body.classList.add('triage-mobile-open');
    if (push) history.pushState({ ...history.state, signalInboxReader: id }, '');
    select(id, true);
  }
  window.addEventListener('popstate', () => {
    if (!mobile.matches) return;
    if (history.state?.signalInboxReader) openMobile(history.state.signalInboxReader, false);
    else if (mobileOpen) closeMobile();
  });
  mobile.addEventListener('change', () => {
    if (mobileOpen) closeMobile();
    if (history.state?.signalInboxReader) {
      const state = { ...history.state }; delete state.signalInboxReader;
      history.replaceState(state, '');
    }
    if (!mobile.matches && rows().length) select(rows()[0].dataset.sourceId, true);
  });
  const rows = () => [...list.querySelectorAll('.source-row')];
  const current = () => rows().find(row => row.dataset.sourceId === selectedId);
  function fingerprint(row) {
    const clone = row.cloneNode(true); clone.classList.remove('keyboard-selected');
    return clone.innerHTML;
  }
  async function select(id, force = false) {
    const row = rows().find(item => item.dataset.sourceId === id);
    if (!row) return;
    selectedIndex = rows().indexOf(row);
    const changed = id !== selectedId;
    if (!changed && !force) return;
    selectedId = id; signature = fingerprint(row);
    try { if (!mobile.matches) sessionStorage.setItem(selectionKey, id); } catch { /* Optional navigation memory. */ }
    const ticket = ++version;
    request?.abort(); request = new AbortController();
    // Remove the previous player immediately when moving to another find.
    if (changed) reader.innerHTML = backButton + '<div class="empty" role="status">Following this thread…</div>';
    reader.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch('/sources/' + id + '/triage', { signal: request.signal });
      if (!response.ok) throw new Error('Could not load this find. Select it again to retry.');
      const html = await response.text();
      if (ticket !== version) return;
      reader.innerHTML = backButton + html;
      if (mobile.matches && changed) reader.querySelector('[data-triage-back]')?.focus({ preventScroll: true });
      initTopicEditor(reader.querySelector('#topic-editor'));
      if (changed) reader.scrollTop = 0;
      reader.querySelectorAll('.triage-full').forEach(details => details.addEventListener('toggle', () => {
        const video = details.querySelector('[data-youtube-id]');
        if (!video) return;
        if (!details.open) { video.querySelector('iframe')?.remove(); return; }
        if (video.querySelector('iframe')) return;
        const frame = document.createElement('iframe');
        frame.src = 'https://www.youtube-nocookie.com/embed/' + video.dataset.youtubeId;
        frame.title = 'YouTube player'; frame.allow = 'encrypted-media; picture-in-picture; fullscreen';
        frame.allowFullscreen = true; frame.referrerPolicy = 'strict-origin-when-cross-origin';
        video.prepend(frame);
      }));
    } catch (error) {
      if (ticket === version && error.name !== 'AbortError') {
        reader.innerHTML = backButton;
        const notice = document.createElement('p'); notice.textContent = error.message; reader.append(notice); signature = '';
      }
    } finally { if (ticket === version) reader.setAttribute('aria-busy', 'false'); }
  }
  document.addEventListener('signal:select', event => {
    if (!mobile.matches) select(event.detail.id);
  });
  list.addEventListener('click', event => {
    const row = event.target.closest('.source-row');
    if (!row || event.target.closest('button') || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (mobile.matches) { openMobile(row.dataset.sourceId); return; }
    row.focus(); select(row.dataset.sourceId, !signature);
  });
  reader.addEventListener('click', async event => {
    if (event.target.closest('[data-triage-back]')) { backToList(); return; }
    const processing = event.target.closest('[data-processing-action]');
    if (processing) {
      if (processing.disabled || list.dataset.mutating === 'true' || !current()) return;
      const id = selectedId;
      const action = processing.dataset.processingAction;
      if (action === 'reprocess' && !confirm('Reprocess this find using your current preferences? This rebuilds extraction, summary and embeddings. Previous chats are kept separately.')) return;
      processing.disabled = true;
      list.dataset.mutating = 'true';
      listRevision++;
      let queued = false;
      try {
        await api('/api/sources/' + id + '/' + action, json('POST', {}));
        queued = true;
        toast('Queued. Processing will continue in the background.');
      } catch (error) { toast(error.message, true); }
      finally { processing.disabled = false; list.dataset.mutating = 'false'; }
      if (queued) {
        try { await refreshInbox(); if (selectedId === id) await select(id, true); }
        catch { toast('Queued. Refresh the list to see the change.'); }
      }
      return;
    }
    const button = event.target.closest('[data-triage-action]');
    if (!button || list.dataset.mutating === 'true' || !current()) return;
    const selector = button.dataset.triageAction === 'collection'
      ? '[data-row-action="collection"][data-value="library"]'
      : '[data-row-action="' + button.dataset.triageAction + '"]';
    current().querySelector(selector)?.click();
  });
  new MutationObserver(() => {
    if (list.dataset.mutating === 'true') return;
    if (mobile.matches && !mobileOpen) return;
    const row = current();
    if (row) {
      const focusButton = reader.querySelector('[data-triage-action="focus"]');
      const focused = row.querySelector('[data-row-action="focus"]')?.getAttribute('aria-pressed') === 'true';
      if (focusButton) {
        focusButton.setAttribute('aria-pressed', String(focused));
        focusButton.innerHTML = (focused ? '★' : '☆') + ' Focus <kbd>F</kbd>';
      }
      // Never reset an editor or a playing video because of background polling.
      if (fingerprint(row) !== signature && !reader.contains(document.activeElement) && !reader.querySelector('iframe') && !reader.querySelector('[data-inline-editor]:not([hidden])')) select(selectedId, true);
      return;
    }
    if (mobile.matches) { backToList(); return; }
    const next = rows()[Math.min(selectedIndex, rows().length - 1)];
    if (next) { next.focus({ preventScroll: true }); select(next.dataset.sourceId); }
    else {
      selectedId = null; version++; request?.abort();
      reader.innerHTML = '<div class="empty"><div class="empty-glyph">✓</div><h3>All caught up.</h3><p>A little more room for your next obsession.</p></div>';
      reader.setAttribute('aria-busy', 'false');
    }
  }).observe(list, { childList: true });
  if (mobile.matches && history.state?.signalInboxReader) {
    const state = { ...history.state }; delete state.signalInboxReader;
    history.replaceState(state, '');
  }
  if (!mobile.matches && rows().length) {
    let remembered;
    try { remembered = sessionStorage.getItem(selectionKey); } catch { /* Optional navigation memory. */ }
    const initial = rows().find(row => row.dataset.sourceId === remembered) || rows()[0];
    initial.focus({ preventScroll: true }); select(initial.dataset.sourceId);
    list.scrollTop = initial.offsetTop - list.offsetTop;
  }
})();
