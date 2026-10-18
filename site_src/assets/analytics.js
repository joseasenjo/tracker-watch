// TEMPORARY visit counter. It is only included in the pages when the site is built with an analytics address.
// It sets no cookie and uses no storage and no identifier: it sends, for each page view, the page (without query
// string or fragment), the website the visitor came from (the host only), and the seconds the page was visible.
// It sends nothing when the browser asks not to be tracked (Global Privacy Control or Do Not Track), nor on
// localhost. The receiving side adds only the country, the day and the hour; it keeps no address.

export const SECTIONS = ['web', 'addon', 'check'];
const STORE_HOSTS = { 'addons.mozilla.org': 'firefox', 'microsoftedge.microsoft.com': 'edge', 'chromewebstore.google.com': 'chrome',
  'chrome.google.com': 'chrome', 'addons.opera.com': 'opera' };
const ACTIONS = { 'bc-read': 'read', 'bc-test': 'tracker-test', 'bc-conn': 'connection-test', 'bc-summary-btn': 'summary' };
export const MAX_SECONDS = 1800;

// The part of the path after the site root ("tracker-watch/" on GitHub Pages), without query string or fragment.
export function pagePath(pathname, root) {
  let path = String(pathname || '/');
  if (root && path.startsWith(root)) path = path.slice(root.length);
  path = path.replace(/^\/+/, '');
  return (path || 'index.html').slice(0, 120);
}

// web: every page except the extension pages (addon) and the Browser check (check).
export function sectionOf(path) {
  if (path === 'extension.html' || path === 'extension-privacy.html') return 'addon';
  if (path === 'browser-check.html') return 'check';
  return 'web';
}

// "direct" without a referrer, "internal" from another page of this site, otherwise the host without "www.".
export function refHost(referrer, ownHost) {
  if (!referrer) return 'direct';
  let host;
  try {
    host = new URL(referrer).hostname.toLowerCase();
  } catch (error) {
    return 'direct';
  }
  if (!host) return 'direct';
  if (host === ownHost) return 'internal';
  return host.replace(/^www\./, '').slice(0, 100);
}

export function clampSeconds(ms) {
  const seconds = Math.round(Number(ms) / 1000);
  if (!Number.isFinite(seconds) || seconds < 0) return 0;
  return Math.min(seconds, MAX_SECONDS);
}

export function storeOf(url) {
  try {
    return STORE_HOSTS[new URL(url).hostname] || null;
  } catch (error) {
    return null;
  }
}

export function shouldSend({ hostname, gpc, dnt }) {
  if (gpc === true || dnt === '1' || dnt === 'yes') return false;
  return !(hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]' || String(hostname).endsWith('.local'));
}

export function buildEvent(kind, path, extra = {}) {
  return { k: kind, p: path, s: sectionOf(path), ...extra };
}

function start() {
  const tag = document.querySelector('script[data-endpoint]');
  const endpoint = tag && tag.dataset.endpoint;
  if (!endpoint) return;
  const dnt = navigator.doNotTrack || window.doNotTrack || navigator.msDoNotTrack;
  if (!shouldSend({ hostname: location.hostname, gpc: navigator.globalPrivacyControl, dnt })) return;

  const root = new URL('../', tag.src).pathname;
  const path = pagePath(location.pathname, root);
  const send = (event) => {
    const body = JSON.stringify(event);
    try {
      if (!(navigator.sendBeacon && navigator.sendBeacon(endpoint, new Blob([body], { type: 'text/plain' })))) {
        fetch(endpoint, { method: 'POST', body, keepalive: true, mode: 'no-cors', credentials: 'omit', referrerPolicy: 'no-referrer' });
      }
    } catch (error) { /* a counter must never break the page */ }
  };

  send(buildEvent('view', path, { r: refHost(document.referrer, location.hostname) }));

  // Time the page was visible, sent once when the visitor leaves or hides the tab.
  let visibleMs = 0;
  let since = document.visibilityState === 'visible' ? Date.now() : null;
  let sent = false;
  const flush = () => {
    if (since !== null) { visibleMs += Date.now() - since; since = null; }
    if (sent || visibleMs < 500) return;
    sent = true;
    send(buildEvent('time', path, { d: clampSeconds(visibleMs) }));
  };
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush();
    else if (!sent) since = Date.now();
  });
  addEventListener('pagehide', flush);

  // Clicks on the store buttons and on the Browser check buttons (only which one, never what the visitor read).
  document.addEventListener('click', (event) => {
    const link = event.target.closest && event.target.closest('a[href]');
    const store = link && storeOf(link.href);
    if (store) send(buildEvent('click', path, { x: store }));
    const button = event.target.closest && event.target.closest('button[id]');
    if (button && ACTIONS[button.id]) send(buildEvent('act', path, { x: ACTIONS[button.id] }));
  }, true);
}

if (typeof document !== 'undefined') start();
