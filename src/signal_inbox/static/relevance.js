document.addEventListener('click', async event => {
  const button = event.target.closest('[data-refresh-relevance]');
  if (!button || button.disabled) return;
  const section = button.closest('[data-relevance-source]');
  button.disabled = true; button.textContent = 'Connecting…';
  try {
    const data = await api('/api/sources/' + section.dataset.relevanceSource + '/relevance', { method: 'POST' });
    section.querySelector('.relevance-body').innerHTML = data.html || '<p>Add your interests to an official topic first.</p>';
    section.querySelector('.relevance-notice').textContent = 'Personal interpretation · grounded in this find' + (data.stale ? ' · Your topic context changed during generation. Refresh for a current perspective.' : '');
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = 'Refresh ↗'; }
});
