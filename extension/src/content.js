// Runs in every frame, in the extension's isolated world. It never clicks, never changes the page and never
// reads page text. It reports:
// - the time of the first real interaction (trusted click or key press; scrolling does not count) and, if
//   the click landed on a known consent banner, which tool and which button (reject / accept / other);
// - which known consent banners are on screen (documented ids from the engine's consent table);
// - on a search results page (top frame): how result links record a click (counts only).
// The tables come from content-data.js (generated at build time).
(() => {
  const api = globalThis.browser ?? globalThis.chrome;
  const data = globalThis.__lensData || { consent: [], engines: [] };
  const send = (msg) => { try { api.runtime.sendMessage(msg); } catch { /* extension reloaded */ } };
  const matches = (node, selector) => { try { return Boolean(node && node.closest && node.closest(selector)); } catch { return false; } };

  function clickedOn(target) {
    for (const tool of data.consent) {
      if (matches(target, tool.reject)) return { tool: tool.name, choice: 'reject' };
      if (matches(target, tool.accept)) return { tool: tool.name, choice: 'accept' };
      if (matches(target, tool.banner)) return { tool: tool.name, choice: 'other' };
    }
    return null;
  }

  let sent = false;
  const handler = (e) => {
    if (sent || !e.isTrusted) return;
    sent = true;
    window.removeEventListener('pointerdown', handler, true);
    window.removeEventListener('keydown', handler, true);
    send({ type: 'interaction', kind: e.type === 'keydown' ? 'key' : 'click', at: Date.now(),
      on: e.type === 'keydown' ? null : clickedOn(e.target) });
  };
  window.addEventListener('pointerdown', handler, true);
  window.addEventListener('keydown', handler, true);

  const reported = new Set();
  function visible(el) {
    if (!el.getClientRects().length) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity) !== 0;
  }
  function scanBanners() {
    for (const tool of data.consent) {
      if (reported.has(tool.name)) continue;
      let el = null;
      try { el = document.querySelector(tool.banner); } catch { el = null; }
      if (el && visible(el)) {
        reported.add(tool.name);
        send({ type: 'banner', tool: tool.name });
      }
    }
  }

  const engine = window === window.top
    ? data.engines.find((e) => new RegExp(e.hosts).test(location.hostname) && e.results_paths.includes(location.pathname))
    : null;
  function scanResults() {
    const links = { total: 0, ping: 0, redirect: 0, mousedown: 0 };
    const re = new RegExp(engine.hosts);
    for (const a of document.querySelectorAll('a[href]')) {
      let u;
      try { u = new URL(a.getAttribute('href'), location.href); } catch { continue; }
      if (!/^https?:$/.test(u.protocol)) continue;
      const viaRedirect = re.test(u.hostname) && engine.redirect_paths.some((p) => u.pathname === p || (p.endsWith('/') && u.pathname.startsWith(p)));
      if (re.test(u.hostname) && !viaRedirect) continue; // the engine's own navigation, not a result
      links.total += 1;
      if (viaRedirect) links.redirect += 1;
      if (a.hasAttribute('ping')) links.ping += 1;
      if (a.hasAttribute('onmousedown')) links.mousedown += 1;
    }
    send({ type: 'serp', links });
  }

  const scan = () => {
    scanBanners();
    if (engine) scanResults();
  };
  const schedule = () => { for (const ms of [300, 1500, 4000]) setTimeout(scan, ms); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', schedule, { once: true });
  else schedule();
})();
