function initTopicEditor(root = document.querySelector('#topic-editor')) {
  if (!root) return;
  const input = root.querySelector('#topic-query');
  const results = root.querySelector('#topic-results');
  const chips = root.querySelector('#inline-topic-chips');
  const status = root.querySelector('#topic-edit-status');
  const path = '/api/sources/' + root.dataset.sourceId + '/topics';
  let catalog = JSON.parse(root.dataset.catalog);
  let attached = JSON.parse(root.dataset.attached);
  let choices = [], active = -1, saving = false, refreshVersion = 0;
  const clean = (text) => text.normalize('NFKC').trim().replace(/\s+/gu, ' ');
  const fold = (text) => clean(text).toLocaleLowerCase().normalize('NFD').replace(/\p{M}/gu, '');

  function close() {
    results.hidden = true; active = -1;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }
  function highlight(index) {
    active = index;
    [...results.querySelectorAll('[role="option"]')].forEach((el, i) => el.setAttribute('aria-selected', String(i === active)));
    if (active >= 0) input.setAttribute('aria-activedescendant', 'topic-choice-' + active);
    else input.removeAttribute('aria-activedescendant');
  }
  function search() {
    const query = clean(input.value);
    results.replaceChildren(); choices = []; active = -1;
    if (!query || saving) { close(); return; }
    const matches = catalog.filter((topic) => fold(topic.name).includes(fold(query)));
    choices = matches.filter((topic) => !attached.some((item) => item.id === topic.id)).slice(0, 8);
    // Partial search matches should not prevent creating a distinct topic.
    const exact = [...catalog, ...attached].some((topic) => clean(topic.name).toLowerCase() === query.toLowerCase());
    if (!exact) choices.push({ create: true, name: query });
    for (const [i, topic] of choices.entries()) {
      const button = document.createElement('button');
      button.type = 'button'; button.tabIndex = -1; button.id = 'topic-choice-' + i;
      button.setAttribute('role', 'option'); button.setAttribute('aria-selected', 'false');
      button.textContent = topic.create ? `+ Create “${topic.name}”` : `${topic.official ? '✓' : '✧'} ${topic.name}`;
      button.addEventListener('click', () => choose(topic));
      results.append(button);
    }
    if (!choices.length) {
      const hint = document.createElement('p'); hint.textContent = 'Matching topics are already added.'; results.append(hint);
    }
    results.hidden = false; input.setAttribute('aria-expanded', 'true'); highlight(choices.length ? 0 : -1);
  }
  function renderChips() {
    chips.replaceChildren();
    for (const topic of attached) {
      const chip = document.createElement('span');
      chip.className = 'topic-chip editable-topic ' + (topic.official ? 'official' : 'suggested');
      if (!topic.official) {
        const approve = document.createElement('button'); approve.type = 'button'; approve.className = 'topic-approve';
        approve.textContent = '✧'; approve.title = 'Make this topic official everywhere';
        approve.setAttribute('aria-label', `Make ${topic.name} official everywhere`);
        approve.addEventListener('click', () => mutate(async () => {
          await api('/api/topics/' + topic.id + '/approve', { method: 'POST' });
          topic.official = true;
          catalog = catalog.map(item => item.id === topic.id ? { ...item, official: true } : item);
          status.textContent = `${topic.name} is now official everywhere.`;
          toast('Topic made official.');
        }));
        chip.append(approve);
      }
      const link = document.createElement('a'); link.href = '/topics/' + topic.id;
      link.textContent = `${topic.official ? '✓ ' : ''}${topic.name}`;
      link.title = topic.official ? 'Official topic' : 'AI suggestion'; chip.append(link);
      if (!topic.manual) {
        const keep = document.createElement('button'); keep.type = 'button'; keep.className = 'chip-action'; keep.textContent = '⌖';
        keep.title = 'Keep this topic when regenerating'; keep.setAttribute('aria-label', `Keep ${topic.name} when regenerating`);
        keep.addEventListener('click', () => mutate(async () => {
          await api(path + '/' + topic.id, { method: 'PUT' }); topic.manual = true;
          status.textContent = `${topic.name} will be kept when regenerating.`;
        })); chip.append(keep);
      }
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'chip-action remove-topic'; remove.textContent = '×';
      remove.title = 'Remove from this source'; remove.setAttribute('aria-label', `Remove ${topic.name} from this source`);
      remove.addEventListener('click', () => mutate(async () => {
        await api(path + '/' + topic.id, { method: 'DELETE' }); attached = attached.filter((item) => item.id !== topic.id);
        status.textContent = `${topic.name} removed from this source.`;
      })); chip.append(remove); chips.append(chip);
    }
  }
  async function mutate(action) {
    if (saving) return;
    saving = true; close(); input.disabled = true;
    chips.querySelectorAll('button').forEach((button) => { button.disabled = true; });
    try { await action(); document.dispatchEvent?.(new CustomEvent('signal:topics-changed')); }
    catch (error) { toast(error.message, true); }
    finally {
      saving = false; input.disabled = false; renderChips(); if (root.isConnected !== false) input.focus(); search();
    }
  }
  async function choose(topic) {
    await mutate(async () => {
      let selected = topic;
      if (topic.create) {
        selected = await api(path, json('POST', { name: topic.name }));
        catalog = catalog.filter((item) => item.id !== selected.id).concat(selected);
      } else await api(path + '/' + topic.id, { method: 'PUT' });
      attached = attached.filter((item) => item.id !== selected.id).concat({ ...selected, manual: true });
      input.value = ''; status.textContent = `${selected.name} added.`;
    });
  }
  input.addEventListener('input', search);
  input.addEventListener('focus', async () => {
    search();
    const version = ++refreshVersion;
    try {
      const latest = await api('/api/topics');
      if (version !== refreshVersion) return;
      catalog = latest;
      if (document.activeElement === input && !saving) search();
    } catch { /* The page's catalog remains available if refreshing fails. */ }
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault(); if (results.hidden) search();
      if (choices.length) highlight((active + (event.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length);
    }
    if (event.key === 'Enter' && !results.hidden && active >= 0) { event.preventDefault(); choose(choices[active]); }
  });
  root.addEventListener('focusout', (event) => { if (!root.contains(event.relatedTarget)) close(); });
  renderChips();
}
initTopicEditor();
