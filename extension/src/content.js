// Runs in every frame, in the extension's isolated world. It never clicks and never changes the page, except with
// "Reject banners" on (off by default): then it presses the reject button of a cookie banner and shows a small
// notice of its own. The only page text it reads is the label of buttons when a consent banner is involved.
// It reports:
// - the time of the first real interaction (trusted click or key press; scrolling does not count) and, if
//   the click landed on a known consent banner, which tool and which button (reject / accept / pay / other).
//   Custom buttons are named by their own text, matched exactly against the engine's phrase lists (only the
//   clicked button's text is read, and only when a consent banner is involved);
// - which known consent banners are on screen (documented ids from the engine's consent table);
// - on a search results page (top frame): how result links record a click (counts only).
// The tables come from content-data.js (generated at build time).
(() => {
  const api = globalThis.browser ?? globalThis.chrome;
  const data = globalThis.__lensData || { consent: [], engines: [], buttons: { reject: [], accept: [], paid: '$^' } };
  const rejectTexts = new Set(data.buttons.reject);
  const acceptTexts = new Set(data.buttons.accept);
  const paidRe = new RegExp(data.buttons.paid, 'i');
  const contextRe = new RegExp(data.buttons.context || '$^', 'i');
  const norm = (s) => String(s || '').toLowerCase().replace(/\s+/g, ' ').trim().replace(/[.!\u2026]+$/, '');
  const send = (msg) => { try { api.runtime.sendMessage(msg); } catch { /* extension reloaded */ } };
  const matches = (node, selector) => { try { return Boolean(node && node.closest && node.closest(selector)); } catch { return false; } };

  function textChoice(target) {
    const button = target && target.closest
      ? target.closest('button, a, [role="button"], input[type="button"], input[type="submit"]') : null;
    if (!button) return null;
    const text = norm(button.innerText || button.value || button.getAttribute('aria-label'));
    if (!text || text.length > 80) return null;
    if (paidRe.test(text)) return 'pay';
    if (rejectTexts.has(text)) return 'reject';
    if (acceptTexts.has(text)) return 'accept';
    return null;
  }

  function clickedOn(target) {
    for (const tool of data.consent) {
      if (matches(target, tool.reject)) return { tool: tool.name, choice: 'reject' };
      if (matches(target, tool.accept)) return { tool: tool.name, choice: 'accept' };
      if (matches(target, tool.banner)) return { tool: tool.name, choice: textChoice(target) || 'other' };
    }
    // a custom button outside the tool's documented container, while one of its banners is on screen
    const shown = data.consent.find((tool) => {
      let el = null;
      try { el = document.querySelector(tool.banner); } catch { el = null; }
      return el && visible(el);
    });
    const choice = textChoice(target);
    if (shown && choice) return { tool: shown.name, choice };
    // a banner of an unknown tool: the button's label is a consent phrase and a small block around it talks
    // about cookies or consent (the engine's rule); only that block's text is read, once, on the first click
    if (choice && aroundTalksAboutConsent(target)) return { tool: null, choice };
    return null;
  }

  function aroundTalksAboutConsent(target) {
    let node = target;
    for (let i = 0; i < 8 && node && node !== document.body; i++, node = node.parentElement) {
      const text = (node.innerText || '').slice(0, 3000);
      if (text.length > 3000) break;
      if (contextRe.test(text)) return true;
    }
    return false;
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
  // "Accept or pay": the banner's only way to refuse is a subscription (as on elmundo.es). Only the labels of
  // the banner's own buttons are read.
  function payOrAccept(banner) {
    let pay = false;
    for (const b of banner.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]')) {
      const text = norm(b.innerText || b.value || b.getAttribute('aria-label'));
      if (!text || text.length > 80) continue;
      if (paidRe.test(text)) pay = true;
      else if (rejectTexts.has(text)) return false;
    }
    return pay;
  }
  function scanBanners() {
    for (const tool of data.consent) {
      if (reported.has(tool.name)) continue;
      let el = null;
      try { el = document.querySelector(tool.banner); } catch { el = null; }
      if (el && visible(el)) {
        reported.add(tool.name);
        send({ type: 'banner', tool: tool.name, payOrAccept: payOrAccept(el) });
      }
    }
  }

  // Script behaviours noticed by content-main.js (MAIN world). Only a known kind and a host name pass.
  const KINDS = new Set(['canvas_read', 'geolocation_request', 'webrtc_connection']);
  let relayed = 0;
  document.addEventListener('trackerwatch-lens:behaviour', (e) => {
    let d = null;
    try { d = JSON.parse(e.detail); } catch { return; }
    if (!d || !KINDS.has(d.kind) || typeof d.host !== 'string' || !/^[a-z0-9.-]{0,253}$/i.test(d.host)) return;
    if (relayed++ >= 50) return;
    send({ type: 'behaviour', kind: d.kind, host: d.host.toLowerCase() });
  });

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

  // "Reject banners" (opt-in). A known tool's documented reject button, or, inside its banner, a button whose
  // label is exactly a refusal; a banner of an unknown tool only when the button's label is a refusal and the
  // block around it talks about cookies. Never "accept", never a refusal that is a subscription.
  const answered = new Set();
  const buttonsIn = (root) => root.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]');
  const labelOf = (b) => norm(b.innerText || b.value || b.getAttribute('aria-label'));
  const isRefusal = (b) => { const t = labelOf(b); return Boolean(t) && t.length <= 80 && rejectTexts.has(t) && !paidRe.test(t); };
  // a documented reject selector can also match a "more options" button (Quantcast's secondary button is either):
  // press it only when its label is a refusal, or at least not an acceptance, a payment or a way into settings
  const OPTIONS_RE = /option|setting|preferen|config|personali|manage|more|m\u00e1s|mehr|plus|einstell|gestion|ajust|detail|purpose|partner|vendor|socio/i;
  const safeToPress = (b) => {
    const t = labelOf(b);
    return isRefusal(b) || (!paidRe.test(t) && !acceptTexts.has(t) && !OPTIONS_RE.test(t));
  };
  // the setting is asked once, and only when there is something to answer (not from every ad frame)
  let allowed = null;
  const mayAnswer = () => (allowed ??= api.runtime.sendMessage({ type: 'autoreject:check' }).then(Boolean, () => false));
  const press = async (button, tool) => {
    if (!(await mayAnswer())) return;
    answered.add(tool || '?');
    button.click();
    send({ type: 'autoreject:result', tool, outcome: 'rejected' });
  };
  const tell = async (tool, outcome) => {
    if (!(await mayAnswer())) return;
    answered.add(tool || '?');
    send({ type: 'autoreject:result', tool, outcome });
  };
  // Tools whose first layer has no refusal but whose settings layer has a "Reject all" (documented ids/classes)
  const TWO_STEP = { OneTrust: { open: '#onetrust-pc-btn-handler', reject: '.ot-pc-refuse-all-handler' } };
  const opened = new Set();
  const firstVisible = (selector) => { try { return [...document.querySelectorAll(selector)].find(visible) || null; } catch { return null; } };
  async function autoReject(last) {
    if (allowed !== null && !(await allowed)) return; // the setting is off (or paused here): nothing to look for
    for (const tool of data.consent) {
      if (answered.has(tool.name)) continue;
      let documented = null;
      let banner = null;
      try { documented = [...document.querySelectorAll(tool.reject)].find(visible) || null; } catch { documented = null; }
      try { banner = document.querySelector(tool.banner); } catch { banner = null; }
      if (banner && !visible(banner)) banner = null;
      // a tool that draws its buttons in a frame of its own (Sourcepoint): inside that frame there is no banner
      // container, but its accept button is there; the frame's page is then the banner
      if (!banner && window !== window.top) {
        let acceptButton = null;
        try { acceptButton = [...document.querySelectorAll(tool.accept)].find(visible) || null; } catch { acceptButton = null; }
        if (acceptButton && document.body) banner = document.body;
      }
      if (documented && safeToPress(documented)) { await press(documented, tool.name); continue; }
      // the tool's own reject button is a subscription ("Reject all and subscribe"): accept or pay, also when it
      // sits in a frame of its own without the banner container (Sourcepoint)
      if (documented && paidRe.test(labelOf(documented))) { await tell(tool.name, 'payOrAccept'); continue; }
      const step = TWO_STEP[tool.name];
      const second = step && opened.has(tool.name) ? firstVisible(step.reject) : null;
      if (second && safeToPress(second)) { await press(second, tool.name); continue; }
      if (!banner) continue;
      const custom = [...buttonsIn(banner)].find((b) => isRefusal(b) && visible(b));
      if (custom) { await press(custom, tool.name); continue; }
      if (payOrAccept(banner)) { await tell(tool.name, 'payOrAccept'); continue; }
      const open = step && !opened.has(tool.name) ? firstVisible(step.open) : null;
      if (open && (await mayAnswer())) { opened.add(tool.name); open.click(); setTimeout(scan, 900); continue; }
      // the buttons may live in a frame inside the banner (Sourcepoint): that frame answers for itself
      if (last && !banner.querySelector('iframe')) await tell(tool.name, 'noReject');
    }
    if (answered.size) return;
    // a banner of an unknown tool: only pages that mention cookies or consent at all, labels checked first
    if (!contextRe.test((document.body && document.body.textContent || '').slice(0, 300000))) return;
    if (answered.has('?')) return;
    for (const b of buttonsIn(document)) {
      if (!rejectTexts.has(norm(b.textContent || b.value))) continue;
      if (visible(b) && isRefusal(b) && aroundTalksAboutConsent(b)) { await press(b, null); return; }
    }
    // "Reject and subscribe" in a banner of its own (as on marca.com): the only refusal is paid, so say so
    for (const b of buttonsIn(document)) {
      const text = labelOf(b);
      if (text && text.length <= 80 && paidRe.test(text) && visible(b) && aroundTalksAboutConsent(b)) { await tell(null, 'payOrAccept'); return; }
    }
  }

  // The notice Lens shows in the page (top frame only): its own small card, in a closed shadow root so the page
  // cannot style or read it. Texts come from the extension's own messages.
  const NOTICE = { rejected: 'noticeRejected', payOrAccept: 'noticePayOrAccept', noReject: 'noticeNoReject' };
  function showNotice(tool, outcome) {
    if (window !== window.top || !NOTICE[outcome] || document.getElementById('trackerwatch-lens-notice')) return;
    const host = document.createElement('div');
    host.id = 'trackerwatch-lens-notice';
    const root = host.attachShadow({ mode: 'closed' });
    const style = document.createElement('style');
    style.textContent = `.card{position:fixed;right:16px;bottom:16px;z-index:2147483647;max-width:340px;font:14px/1.4 system-ui,sans-serif;
      background:#1d2129;color:#fff;border-radius:10px;padding:12px 36px 12px 14px;box-shadow:0 6px 24px rgba(0,0,0,.3)}
      b{display:block;margin-bottom:4px;font-size:12px;letter-spacing:.02em;color:#8fb8e6}
      button{position:absolute;top:6px;right:8px;background:none;border:0;color:#fff;font-size:18px;cursor:pointer}`;
    const card = document.createElement('div');
    card.className = 'card';
    card.setAttribute('role', 'status');
    const title = document.createElement('b');
    title.textContent = api.i18n.getMessage('extName');
    const text = document.createElement('div');
    text.textContent = api.i18n.getMessage(NOTICE[outcome], [tool || api.i18n.getMessage('noticeUnknownTool')]);
    const close = document.createElement('button');
    close.textContent = '\u00d7';
    close.setAttribute('aria-label', api.i18n.getMessage('noticeClose'));
    close.addEventListener('click', () => host.remove());
    card.append(title, text, close);
    root.append(style, card);
    (document.body || document.documentElement).appendChild(host);
    if (outcome === 'rejected') setTimeout(() => host.remove(), 8000);
  }
  try {
    api.runtime.onMessage.addListener((msg) => {
      if (msg && msg.type === 'lens:notice') showNotice(typeof msg.tool === 'string' ? msg.tool : null, msg.outcome);
    });
  } catch { /* no runtime */ }

  let scans = 0;
  const scan = async () => {
    if (!document.prerendering) scans += 1;
    scanBanners();
    if (!document.prerendering) await autoReject(scans >= 4); // a page loaded in the background is answered when shown
    if (engine) scanResults();
  };
  const schedule = () => { for (const ms of [300, 1500, 4000, 7000]) setTimeout(scan, ms); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', schedule, { once: true });
  else schedule();
  // Chromium may load a page in the background (prerendering) and show it later: look again when it is shown
  if (document.prerendering) document.addEventListener('prerenderingchange', schedule, { once: true });
})();
