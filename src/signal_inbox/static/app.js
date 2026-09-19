const $ = (selector) => document.querySelector(selector);
let toastTimer;
function toast(message, error = false) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.toggle('error', error);
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 6500);
}
async function api(path, options = {}) {
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); } catch { throw new Error('Could not connect to Signal.'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check the details and try again.');
  if (options.method && options.method !== 'GET') void refreshNavigationCounts();
  return data;
}
const json = (method, data) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
function busy(form, value) { form.querySelectorAll('button, textarea, input, select').forEach((e) => { e.disabled = value; }); }
let listRevision = 0;
async function refreshInbox() {
  if ($('#source-list')?.dataset.mutating === 'true') return;
  const revision = listRevision;
  const response = await fetch(location.pathname + location.search);
  if (!response.ok) return;
  const documentCopy = new DOMParser().parseFromString(await response.text(), 'text/html');
  if (revision !== listRevision || $('#source-list')?.dataset.mutating === 'true') return;
  if ($('#source-list')) {
    const updated = documentCopy.querySelector('#source-list').innerHTML;
    const current = $('#source-list').cloneNode(true);
    current.querySelectorAll('.keyboard-selected').forEach(item => item.classList.remove('keyboard-selected'));
    if (current.innerHTML !== updated) $('#source-list').innerHTML = updated;
    $('.count').textContent = documentCopy.querySelector('.count').textContent;
    $('.pagination').innerHTML = documentCopy.querySelector('.pagination').innerHTML;
    if ($('.search-topic-results')) {
      $('.search-topic-results').innerHTML = documentCopy.querySelector('.search-topic-results').innerHTML;
    }
  }
}
$('#url-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const url = $('#url').value;
  busy(form, true);
  try {
    const data = await api('/api/sources', json('POST', { url }));
    toast(data.created ? pickCopy('saved') : 'Already in your stash. Good taste is consistent.');
    form.reset();
    await refreshInbox();
  } catch (error) { toast(error.message, true); }
  finally { busy(form, false); $('#url').focus(); }
});
$('#file')?.addEventListener('change', async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 100 * 1024 * 1024) { toast('This file is over the 100 MB limit.', true); event.target.value = ''; return; }
  const body = new FormData(); body.append('file', file);
  $('#file-name').textContent = `Uploading ${file.name}…`;
  event.target.disabled = true;
  try {
    const data = await api('/api/sources/upload', { method: 'POST', body });
    toast(data.created ? pickCopy('saved') : 'This file is already in your stash.');
    await refreshInbox();
  } catch (error) { toast(error.message, true); }
  finally { event.target.value = ''; event.target.disabled = false; $('#file-name').textContent = 'PDFs, docs, text or media · up to 100 MB'; }
});
$('#focus-url')?.addEventListener('click', () => $('#url').focus());
$('#settings-toggle')?.addEventListener('click', (event) => {
  $('#settings').hidden = !$('#settings').hidden;
  event.currentTarget.setAttribute('aria-expanded', String(!$('#settings').hidden));
});
const preferenceFields = ['language', 'llm_provider', 'llm_model', 'embedding_provider', 'embedding_model', 'url_engine', 'document_engine', 'stt_provider', 'stt_model'];
$('#preferences-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = Object.fromEntries(preferenceFields.filter((key) => $('#' + key.replaceAll('_', '-'))).map((key) => [key, $('#' + key.replaceAll('_', '-')).value]));
  form.dataset.saving = 'true';
  busy(form, true);
  try {
    const saved = await api('/api/preferences', json('PUT', values));
    for (const key of preferenceFields) if (key in saved) $('#' + key.replaceAll('_', '-')).value = saved[key];
    toast('Preferences saved. Your next find gets the new setup.');
  } catch (error) { toast(error.message, true); }
  finally { form.dataset.saving = 'false'; busy(form, false); }
});
document.querySelectorAll('select[data-model-prefix]').forEach((select) => {
  select.addEventListener('change', () => {
    const prefix = select.dataset.modelPrefix;
    $('#' + prefix + '-model').value = '';
    $('#' + prefix + '-models').replaceChildren();
    $('#' + prefix + '-model-status').textContent = 'New provider, new brain. Enter a model ID or fetch suggestions.';
  });
});
document.querySelectorAll('.find-models').forEach((button) => {
  button.addEventListener('click', async () => {
    const prefix = button.dataset.modelPrefix;
    const provider = $('#' + prefix + '-provider').value;
    const status = $('#' + prefix + '-model-status');
    button.disabled = true;
    status.textContent = 'Asking the provider for its lineup…';
    try {
      const data = await api('/api/models?provider=' + encodeURIComponent(provider) + '&kind=' + button.dataset.modelKind);
      if ($('#' + prefix + '-provider').value !== provider) return;
      const list = $('#' + prefix + '-models');
      list.replaceChildren();
      for (const id of data.models) { const option = document.createElement('option'); option.value = id; list.append(option); }
      status.textContent = data.models.length ? `${data.models.length} suggestions loaded. Pick a model above or enter your own ID.` : 'No suggestions returned. You can still enter a model ID manually.';
    } catch (error) { if ($('#' + prefix + '-provider').value === provider) status.textContent = error.message; }
    finally { button.disabled = ($('#preferences-form') || $('#reprocess-form'))?.dataset.saving === 'true'; }
  });
});
const source = $('#source-detail');
const sourcePath = source ? `/api/sources/${source.dataset.sourceId}` : '';
function rememberSourceReturn(identifier) {
  if (!identifier || !location.pathname) return;
  try { sessionStorage.setItem('signal-source-return-' + identifier, location.pathname + location.search); }
  catch { /* Storage is optional. */ }
}
function sourceReturnUrl() {
  const fallback = $('.back-link')?.getAttribute('href') || '/';
  if (!source) return fallback;
  try {
    const key = 'signal-source-return-' + source.dataset.sourceId;
    const stored = sessionStorage.getItem(key);
    sessionStorage.removeItem(key);
    if (stored?.startsWith('/') && !stored.startsWith('//') && !stored.startsWith('/sources/')) return stored;
  } catch { /* Fall back to the source collection. */ }
  return fallback;
}
document.addEventListener?.('click', (event) => {
  const link = event.target.closest?.('a[href^="/sources/"]');
  const match = link?.getAttribute('href')?.match(/^\/sources\/([a-f0-9]{64})(?:[/?#]|$)/);
  if (match) rememberSourceReturn(match[1]);
});
$('#reprocess-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const fields = ['url_engine', 'document_engine', 'stt_provider', 'stt_model'];
  const values = Object.fromEntries(fields.map((key) => [key, $('#reprocess-' + key.replaceAll('_', '-')).value]));
  form.dataset.saving = 'true';
  busy(form, true);
  try { await api(sourcePath + '/reprocess', json('POST', values)); location.reload(); }
  catch (error) { toast(error.message, true); }
  finally { form.dataset.saving = 'false'; busy(form, false); }
});
async function refreshChat() {
  const response = await fetch(location.pathname);
  if (!response.ok) throw new Error('Your answer is saved. Reload the page to see it.');
  const copy = new DOMParser().parseFromString(await response.text(), 'text/html');
  $('#chat-history').innerHTML = copy.querySelector('#chat-history').innerHTML;
  $('#chat-history').scrollTop = $('#chat-history').scrollHeight;
  if ($('#archived-history-slot') && copy.querySelector('#archived-history-slot')) {
    $('#archived-history-slot').innerHTML = copy.querySelector('#archived-history-slot').innerHTML;
  }
}
$('#chat-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const question = $('#question').value.trim();
  if (!question) return;
  busy(form, true); $('#clear-chat').disabled = true;
  const button = form.querySelector('button'); button.textContent = 'Thinking…';
  try {
    await api(sourcePath + '/chat', json('POST', { question }));
    $('#question').value = '';
    await refreshChat();
  } catch (error) { toast(error.message, true); }
  finally { busy(form, false); $('#clear-chat').disabled = false; button.textContent = 'Ask away ↗'; $('#question').focus(); }
});
$('#question')?.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || !event.altKey || event.isComposing) return;
  event.preventDefault();
  $('#chat-form').requestSubmit();
});
$('#clear-chat')?.addEventListener('click', async (event) => {
  if (!confirm('Clear this source’s entire chat history? This cannot be undone.')) return;
  const button = event.currentTarget;
  button.disabled = true;
  try { await api(sourcePath + '/chat', { method: 'DELETE' }); await refreshChat(); toast('Fresh slate. Chat cleared.'); }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
$('#retry-button')?.addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try { await api(sourcePath + '/retry', { method: 'POST' }); location.reload(); }
  catch (error) { toast(error.message, true); button.disabled = false; }
});
async function poll() {
  if (document.hidden) return;
  void refreshNavigationCounts();
  try {
    if ($('#source-list') && location.pathname !== '/search') await refreshInbox();
    if (source && ['pending', 'processing'].includes(source.dataset.status)) {
      const data = await api(sourcePath);
      if ($('#processing-heading')) {
        const waiting = data.stage === 'youtube_wait';
        $('#processing-heading').textContent = waiting ? 'A short YouTube breather' : 'Connecting the dots';
        $('#processing-description').textContent = waiting ? 'This video is waiting for its turn. YouTube extractions are spaced 3–5 minutes apart; other finds keep moving.' : 'Reading your find, taking notes and making it chat-ready. This page updates automatically.';
      }
      if (['ready', 'error'].includes(data.status) && !document.querySelector('[data-inline-editor]:not([hidden])')) location.reload();
    }
  } catch { /* Keep the existing view during temporary disconnections. */ }
}
setInterval(poll, 4000);

const copyChoices = {
  welcome: ['That rabbit hole deserves a home.', 'For everything you swore you’d read later.', 'Less tab chaos. More happy accidents.', 'Collect the good stuff. Connect it later.'],
  url: ['Paste your latest obsession…', 'Drop the link that sent you down a rabbit hole…', 'Found something weirdly fascinating? Paste it here…', 'Give that “read later” tab a home…'],
  question: ['What caught your curiosity?', 'What’s the big idea here?', 'Pull on a thread. Ask a question…', 'What should we make of this?'],
  chatEmpty: ['There’s a good question hiding in here.', 'Follow a hunch. See where it goes.', 'You brought the find. Bring the questions.'],
  saved: ['Saved. Future you says thanks.', 'Into the stash it goes.', 'Good find. We’ll take it from here.', 'One less tab. One more possibility.'],
};
const lastCopy = {};
function pickCopy(key) {
  const choices = copyChoices[key];
  let previous = lastCopy[key];
  try { previous = sessionStorage.getItem('signal-copy-' + key) || previous; } catch { /* Storage is optional. */ }
  const candidates = choices.filter((text) => text !== previous);
  const chosen = candidates[Math.floor(Math.random() * candidates.length)];
  lastCopy[key] = chosen;
  try { sessionStorage.setItem('signal-copy-' + key, chosen); } catch { /* Keep the in-memory choice. */ }
  return chosen;
}
document.querySelectorAll('[data-copy]').forEach((element) => {
  const text = pickCopy(element.dataset.copy);
  if (element.matches('input, textarea')) element.placeholder = text;
  else element.textContent = text;
});

// Small native dialogs keep maintenance actions out of the reading flow.
document.querySelectorAll('[data-open-dialog]').forEach((button) => {
  button.addEventListener('click', () => $('#' + button.dataset.openDialog).showModal());
});
document.querySelectorAll('[data-close-dialog]').forEach((button) => {
  button.addEventListener('click', () => button.closest('dialog').close());
});
function actionForm(selector, action) {
  $(selector)?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    busy(form, true);
    try { await action(); }
    catch (error) { toast(error.message, true); }
    finally { busy(form, false); }
  });
}
function actionButton(selector, action) {
  document.querySelectorAll(selector).forEach((button) => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try { await action(button); }
      catch (error) { toast(error.message, true); }
      finally { button.disabled = false; }
    });
  });
}
actionForm('#regenerate-form', async () => {
  await api(sourcePath + '/regenerate', json('POST', { steering: $('#summary-steering').value }));
  location.reload();
});
actionForm('#create-topic-form', async () => {
  const topic = await api('/api/topics', json('POST', { name: $('#topic-name').value }));
  location.href = '/topics/' + topic.id;
});
const topicPath = $('#topic-detail') ? '/api/topics/' + $('#topic-detail').dataset.topicId : '';
actionButton('#approve-topic', async () => { await api(topicPath + '/approve', { method: 'POST' }); location.reload(); });
actionForm('#rename-topic-form', async () => {
  await api(topicPath, json('PUT', { name: $('#rename-topic-name').value })); location.reload();
});
actionForm('#merge-topic-form', async () => {
  if (!confirm('Merge this topic into the selected one across your library? This cannot be undone.')) return;
  const result = await api(topicPath + '/merge', json('POST', { target: $('#merge-target').value }));
  location.href = '/topics/' + result.target;
});
actionButton('#delete-topic', async () => {
  if (!confirm('Delete this topic everywhere? Your saved finds will stay.')) return;
  await api(topicPath, { method: 'DELETE' }); location.href = '/topics';
});

actionButton('[data-source-collection]', async (button) => {
  const controls = document.querySelectorAll('[data-source-collection]');
  controls.forEach((control) => { control.disabled = true; });
  try {
    await api(sourcePath + '/collection', json('PUT', { collection: button.dataset.sourceCollection }));
    location.reload();
  } finally {
    controls.forEach((control) => { control.disabled = control.getAttribute('aria-pressed') === 'true'; });
  }
});
actionButton('#delete-source', async () => {
  const returnUrl = sourceReturnUrl();
  await api(sourcePath, json('DELETE', { confirm: true }));
  location.replace(returnUrl);
});

actionButton('#focus-source', async (button) => {
  const focused = button.getAttribute('aria-pressed') !== 'true';
  await api(sourcePath + '/focus', json('PUT', { focused }));
  button.setAttribute('aria-pressed', String(focused));
  button.querySelector('span').textContent = focused ? '★' : '☆';
  button.title = focused ? 'Remove from Focus' : 'Add to Focus';
  toast(focused ? 'In Focus. Give this one a closer look.' : 'Removed from Focus. Your find stays saved.');
});

// Delegate to the stable list container: polling replaces its rows.
$('#source-list')?.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-row-action]');
  if (!button || button.disabled) return;
  event.preventDefault(); event.stopPropagation();
  const list = $('#source-list');
  if (list.dataset.mutating === 'true') return;
  const row = button.closest('.source-row');
  const action = button.dataset.rowAction;
  const title = row.querySelector('.source-title-link').textContent;
  if (action === 'delete' && !confirm(`Permanently delete “${title}”? This removes its content, summary, embeddings, chat and stored file copy. Global topics stay. This cannot be undone.`)) return;
  listRevision++;
  list.dataset.mutating = 'true'; list.setAttribute('aria-busy', 'true');
  const controls = [...row.querySelectorAll('button')].map(control => [control, control.disabled]);
  controls.forEach(([control]) => { control.disabled = true; });
  const path = '/api/sources/' + row.dataset.sourceId;
  let saved = false;
  try {
    if (action === 'collection') await api(path + '/collection', json('PUT', { collection: button.dataset.value }));
    else if (action === 'focus') await api(path + '/focus', json('PUT', { focused: button.getAttribute('aria-pressed') !== 'true' }));
    else if (action === 'delete') await api(path, json('DELETE', { confirm: true }));
    saved = true;
  } catch (error) { toast(error.message, true); }
  finally {
    list.dataset.mutating = 'false'; list.setAttribute('aria-busy', 'false');
    controls.forEach(([control, disabled]) => { control.disabled = disabled; });
  }
  if (saved) {
    try { await refreshInbox(); }
    catch { toast('Saved. Refresh the list to see the change.'); }
  }
});

async function refreshNavigationCounts() {
  const badges = document.querySelectorAll('[data-nav-count]');
  if (!badges.length) return;
  try {
    const response = await fetch('/api/navigation-counts');
    if (!response.ok) return;
    const counts = await response.json();
    badges.forEach(element => {
      const count = counts[element.dataset.navCount];
      if (Number.isInteger(count)) element.textContent = count.toLocaleString();
    });
  } catch { /* Keep last known counts when offline. */ }
}
void refreshNavigationCounts();
