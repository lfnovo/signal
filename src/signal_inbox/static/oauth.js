(() => {
  const form = document.querySelector('#oauth-consent');
  let submitted = false;
  form?.addEventListener('submit', (event) => {
    if (submitted) {
      event.preventDefault();
      return;
    }
    submitted = true;
    // Keep the submitter enabled so its approve/deny value is sent.
    form.setAttribute('aria-busy', 'true');
  });
  const callback = document.querySelector('#oauth-callback');
  if (callback) window.location.replace(callback.href);
})();
