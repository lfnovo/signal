(() => {
  const input = document.querySelector('#global-search-query');
  const box = document.querySelector('#search-suggestions');
  if (!input || !box) return;
  let timer, controller, version = 0;
  const close = () => { box.hidden = true; input.setAttribute('aria-expanded', 'false'); };
  input.addEventListener('input', () => {
    clearTimeout(timer); controller?.abort(); const current = ++version;
    const query = input.value.trim(); close();
    if (query.length < 2) return;
    timer = setTimeout(async () => {
      controller = new AbortController();
      try {
        const response = await fetch('/api/search/suggest?q=' + encodeURIComponent(query), { signal: controller.signal });
        if (!response.ok) return;
        const data = await response.json();
        if (current !== version || document.activeElement !== input) return;
        box.replaceChildren();
        for (const [label, values, prefix] of [['Topics', data.topics, '/topics/'], ['Finds', data.sources, '/sources/']]) {
          if (!values.length) continue;
          const heading = document.createElement('div'); heading.className = 'tiny-label'; heading.textContent = label; box.append(heading);
          for (const value of values) { const link = document.createElement('a'); link.href = prefix + value.id; link.textContent = value.name || value.title; box.append(link); }
        }
        const all = document.createElement('a'); all.className = 'search-all'; all.href = '/search?q=' + encodeURIComponent(query); all.textContent = 'Search words + meaning ↗'; box.append(all);
        box.hidden = false; input.setAttribute('aria-expanded', 'true');
      } catch { /* Typing suggestions are optional; submitted search remains available. */ }
    }, 250);
  });
  document.querySelector('.global-search').addEventListener('focusout', event => { if (!event.currentTarget.contains(event.relatedTarget)) close(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { version++; controller?.abort(); close(); }
    if (event.key === 'ArrowDown' && !box.hidden) { event.preventDefault(); box.querySelector('a')?.focus(); }
  });
  box.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); input.focus(); }
    if (['ArrowDown','ArrowUp'].includes(event.key)) {
      event.preventDefault(); event.stopPropagation(); const links = [...box.querySelectorAll('a')]; const index = links.indexOf(document.activeElement) + (event.key === 'ArrowDown' ? 1 : -1);
      if (index < 0) input.focus(); else links[Math.min(index, links.length-1)]?.focus();
    }
  });
  document.querySelector('#search-page-form')?.addEventListener('submit', () => { document.querySelector('#search-loading').hidden = false; });
})();
