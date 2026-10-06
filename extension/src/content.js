// Detects the user's first real interaction with the page (a trusted click or key press; scrolling does not
// count), so the report can split "before" and "after". It reads nothing from the page and never clicks.
(() => {
  const api = globalThis.browser ?? globalThis.chrome;
  let sent = false;
  const handler = (e) => {
    if (sent || !e.isTrusted) return;
    sent = true;
    window.removeEventListener('pointerdown', handler, true);
    window.removeEventListener('keydown', handler, true);
    try {
      api.runtime.sendMessage({ type: 'interaction', kind: e.type === 'keydown' ? 'key' : 'click', at: Date.now() });
    } catch { /* extension reloaded */ }
  };
  window.addEventListener('pointerdown', handler, true);
  window.addEventListener('keydown', handler, true);
})();
