let target;
let base = 'http://127.0.0.1:8020';
async function init() {
  const saved = await chrome.storage.local.get({ apiUrl: base });
  base = saved.apiUrl;
  document.querySelector('#inbox').href = base;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  target = tab?.url;
  document.querySelector('#title').textContent = tab?.title || 'Current page';
  document.querySelector('#url').textContent = target || '';
  if (!target || !/^https?:\/\//i.test(target)) {
    document.querySelector('#status').textContent = 'Open an HTTP or HTTPS page to save it.';
    return;
  }
  document.querySelector('#capture').disabled = false;
}
document.querySelector('#capture').addEventListener('click', async (event) => {
  event.target.disabled = true;
  const status = document.querySelector('#status');
  status.textContent = 'Saving…';
  try {
    const response = await fetch(base + '/api/sources', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: target }), signal: AbortSignal.timeout(15000),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save this link.');
    status.textContent = data.created ? 'Saved. Future you says thanks.' : 'Already in your stash. Good taste is consistent.';
    event.target.textContent = '✓ In your stash';
    document.querySelector('#inbox').href = base + '/sources/' + data.source.id;
  } catch (error) {
    status.textContent = error instanceof TypeError || error.name === 'TimeoutError'
      ? 'Signal is offline. Start it with “uv run signal serve” and try again.' : error.message;
    event.target.disabled = false;
  }
});
init().catch(() => { document.querySelector('#status').textContent = 'Could not read this tab. Reopen the extension and try again.'; });
