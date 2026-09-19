const input = document.querySelector('#api-url');
chrome.storage.local.get({ apiUrl: 'http://127.0.0.1:8020' }).then((saved) => { input.value = saved.apiUrl; });
document.querySelector('#options').addEventListener('submit', async (event) => {
  event.preventDefault();
  const status = document.querySelector('#status');
  try {
    const url = new URL(input.value);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Use a local HTTP address with no path, like http://127.0.0.1:8020.');
    await chrome.storage.local.set({ apiUrl: url.origin });
    status.textContent = 'Address saved. You’re good to go.';
  } catch (error) { status.textContent = error.message; }
});
