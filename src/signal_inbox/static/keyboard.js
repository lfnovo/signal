(() => {
  const help = document.querySelector('#keyboard-help');
  const preview = document.querySelector('#quick-preview');
  if (!help || !preview) return;
  const toggle = document.querySelector('#keyboard-enabled');
  let enabled = true;
  try { enabled = localStorage.getItem('signal-keyboard-enabled') !== 'false'; } catch { /* Optional storage. */ }
  toggle.checked = enabled;
  toggle.addEventListener('change', () => {
    enabled = toggle.checked;
    try { localStorage.setItem('signal-keyboard-enabled', String(enabled)); } catch { /* Optional storage. */ }
  });
  let selectedId = null, selectedIndex = -1, request = null, requestNumber = 0;
  const items = () => [...document.querySelectorAll('.source-row, .topic-card')].filter(item => !preview.open || item.dataset.sourceId);
  const identity = item => item.dataset.sourceId || item.getAttribute('href');
  const selected = () => items().find(item => identity(item) === selectedId);
  const editing = element => element?.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"]');
  const interactive = element => element?.closest('button, a, summary, input, textarea, select');
  const busyList = () => document.querySelector('#source-list')?.dataset.mutating === 'true';

  function select(item, focus = true) {
    if (!item) return;
    const all = items();
    selectedId = identity(item); selectedIndex = all.indexOf(item);
    all.forEach(row => row.classList.toggle('keyboard-selected', row === item));
    if (document.querySelector('#triage-workspace')) document.dispatchEvent(new CustomEvent('signal:select', { detail: { id: selectedId } }));
    if (focus && !preview.open) item.focus({ preventScroll: true });
    if (focus && !preview.open) item.scrollIntoView({ block: 'nearest', behavior: 'instant' });
  }
  function move(direction) {
    if (busyList()) return;
    const all = items();
    if (!all.length) return;
    const index = all.findIndex(item => identity(item) === selectedId);
    const next = index < 0 ? (direction > 0 ? 0 : all.length - 1) : Math.max(0, Math.min(all.length - 1, index + direction));
    select(all[next]);
    if (preview.open) loadPreview(all[next]);
  }
  function openSelected() {
    const item = selected();
    const link = item?.matches('.topic-card') ? item : item?.querySelector('.source-title-link');
    if (link) {
      if (item?.dataset.sourceId && typeof rememberSourceReturn === 'function') rememberSourceReturn(item.dataset.sourceId);
      location.href = link.getAttribute('href');
    }
    else if (preview.open) location.href = document.querySelector('#preview-open').getAttribute('href');
  }
  async function loadPreview(item) {
    const id = item?.dataset.sourceId || document.querySelector('#source-detail')?.dataset.sourceId;
    if (!id) return;
    request?.abort(); request = new AbortController();
    const number = ++requestNumber;
    document.querySelector('#preview-title').textContent = item?.querySelector('.source-title-link')?.textContent || document.querySelector('.detail-heading h1')?.textContent || 'Quick preview';
    document.querySelector('#preview-body').textContent = 'Taking a quick look…';
    document.querySelector('#preview-kind').textContent = 'QUICK PREVIEW';
    document.querySelector('#preview-open').href = '/sources/' + id;
    if (!preview.open) { preview.showModal(); document.querySelector('#preview-body').focus(); }
    try {
      const data = await api('/api/sources/' + id + '/preview', { signal: request.signal });
      if (number !== requestNumber || !preview.open) return;
      document.querySelector('#preview-title').textContent = data.title;
      document.querySelector('#preview-kind').textContent = data.kind === 'summary' ? 'THE SHORT VERSION' : 'FROM THE FULL STORY';
      // Markdown is rendered by the server with raw HTML disabled, just like source pages.
      document.querySelector('#preview-body').innerHTML = data.html || '<p>No extracted text yet. Check back once processing has started.</p>';
      if (/^[A-Za-z0-9_-]{11}$/.test(data.youtube_video_id || '')) {
        const video = document.createElement('div'); video.className = 'preview-video';
        const player = document.createElement('iframe');
        player.src = 'https://www.youtube-nocookie.com/embed/' + data.youtube_video_id;
        player.title = 'YouTube player: ' + data.title;
        player.allow = 'encrypted-media; picture-in-picture; fullscreen';
        player.allowFullscreen = true;
        player.referrerPolicy = 'strict-origin-when-cross-origin';
        const link = document.createElement('a');
        link.href = 'https://www.youtube.com/watch?v=' + data.youtube_video_id;
        link.textContent = 'Watch on YouTube ↗'; link.target = '_blank'; link.rel = 'noopener noreferrer';
        video.append(player, link);
        document.querySelector('#preview-body').prepend(video);
      }
      if (data.truncated) {
        const note = document.createElement('p'); note.className = 'muted'; note.textContent = 'Preview shortened. Open the source for the full story.';
        document.querySelector('#preview-body').append(note);
      }
    } catch (error) {
      if (number === requestNumber && preview.open && error.name !== 'AbortError') document.querySelector('#preview-body').textContent = error.message;
    }
  }
  preview.addEventListener('close', () => {
    requestNumber++; request?.abort();
    document.querySelector('#preview-body').textContent = '';
    selected()?.focus({ preventScroll: true });
  });
  function action(key) {
    if (busyList()) return;
    const row = selected();
    let button;
    if (row?.dataset.sourceId) {
      button = key === 'f' ? row.querySelector('[data-row-action="focus"]') : row.querySelector(`[data-row-action="collection"][data-value="${key === 'i' ? 'inbox' : 'library'}"]`);
    } else if (document.querySelector('#source-detail')) {
      button = key === 'f' ? document.querySelector('#focus-source') : document.querySelector(`[data-source-collection="${key === 'i' ? 'inbox' : 'library'}"]`);
    }
    if (button && !button.disabled) button.click();
  }
  document.addEventListener('focusin', event => {
    const item = event.target.closest('.source-row, .topic-card');
    if (item) select(item, false);
  });
  document.addEventListener('pointerdown', event => {
    const item = event.target.closest('.source-row, .topic-card');
    if (item) select(item, false);
  });
  const list = document.querySelector('#source-list');
  if (list) new MutationObserver(() => {
    if (selectedId === null) return;
    const all = items();
    const item = all.find(row => identity(row) === selectedId) || all[Math.min(selectedIndex, all.length - 1)];
    if (!item) {
      selectedId = null; selectedIndex = -1;
      if (preview.open) preview.close();
      return;
    }
    const hadKeyboardFocus = document.activeElement === document.body;
    select(item, hadKeyboardFocus);
    if (preview.open) loadPreview(item);
  }).observe(list, { childList: true });

  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.isComposing) return;
    if (editing(event.target)) return;
    const otherDialog = [...document.querySelectorAll('dialog[open]')].some(dialog => dialog !== preview);
    if (otherDialog) return;
    const deleteShortcut = ((event.metaKey || event.ctrlKey) && ['Backspace', 'Delete'].includes(event.key))
      || (enabled && !event.metaKey && !event.ctrlKey && event.key === 'Backspace');
    if (deleteShortcut && !event.altKey && !event.shiftKey) {
      if (event.repeat || busyList()) return;
      const row = selected();
      const button = row?.dataset.sourceId ? row.querySelector('[data-row-action="delete"]') : document.querySelector('[data-open-dialog="delete-source-dialog"]');
      if (button && !button.disabled) {
        event.preventDefault();
        if (!row && preview.open) preview.close();
        button.click();
      }
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === 'Escape' && !event.repeat) {
      if (preview.open) { event.preventDefault(); preview.close(); }
      else if (document.querySelector('#source-detail')) {
        event.preventDefault();
        if (history.length > 1) history.back();
        else location.href = document.querySelector('.back-link')?.getAttribute('href') || '/';
      } else { selected()?.classList.remove('keyboard-selected'); selectedId = null; selectedIndex = -1; }
      return;
    }
    if (event.key === '?' && !event.repeat) { event.preventDefault(); if (preview.open) preview.close(); help.showModal(); return; }
    if (event.key === '/' && !preview.open && document.querySelector('#global-search-query')) { event.preventDefault(); document.querySelector('#global-search-query').focus(); return; }
    if (!enabled) return;
    const key = event.key.toLowerCase();
    if (event.repeat && !event.key.startsWith('Arrow')) return;
    if ('1234'.includes(key) && key.length === 1) {
      event.preventDefault(); location.href = ['/', '/library', '/focus', '/topics'][Number(key) - 1]; return;
    }
    const nav = event.target.closest('.nav-item');
    if (nav && ['ArrowUp', 'ArrowDown'].includes(event.key)) {
      const links = [...document.querySelectorAll('.nav-item')];
      const index = Math.max(0, Math.min(links.length - 1, links.indexOf(nav) + (event.key === 'ArrowDown' ? 1 : -1)));
      event.preventDefault(); links[index].focus(); return;
    }
    if (['ArrowUp', 'ArrowDown'].includes(event.key) && items().length) {
      event.preventDefault(); move(event.key === 'ArrowDown' ? 1 : -1); return;
    }
    if (['ArrowLeft', 'ArrowRight'].includes(event.key) && !preview.open) {
      if (document.querySelector('#triage-workspace')) {
        event.preventDefault();
        if (event.key === 'ArrowRight') document.querySelector('#triage-reader [data-triage-action]')?.focus();
        else selected()?.focus({ preventScroll: true });
        return;
      }
      if (selected()?.matches('.topic-card')) { event.preventDefault(); move(event.key === 'ArrowRight' ? 1 : -1); return; }
      const controls = [...(selected()?.querySelectorAll('a, button:not(:disabled)') || [])];
      if (controls.length) {
        const next = (controls.indexOf(document.activeElement) + (event.key === 'ArrowRight' ? 1 : -1) + controls.length) % controls.length;
        event.preventDefault(); controls[next].focus();
      }
      return;
    }
    if (event.key === ' ' && (preview.open || !interactive(event.target))) {
      if (preview.open) { event.preventDefault(); preview.close(); }
      else if (selected()?.dataset.sourceId || document.querySelector('#source-detail')) { event.preventDefault(); loadPreview(selected()); }
      return;
    }
    if (event.key === 'Enter' && (preview.open || !interactive(event.target)) && (selected() || preview.open)) { event.preventDefault(); openSelected(); return; }
    if (['i', 'l', 'f'].includes(key) && (selected()?.dataset.sourceId || document.querySelector('#source-detail'))) { event.preventDefault(); action(key); }
  });
})();
