// Tracker Watch Lens background: feeds webRequest events to the core reducer, one report per tab.
// Chromium runs it as a service worker and Firefox as an event page; both put it to sleep when idle,
// so every tab report is kept in storage.session (written with a short delay) and reloaded on wake.
// Nothing leaves the browser.

import { buildSuffixTrie } from './core/psl.js';
import { createTrackerList } from './core/classify.js';
import { markInteraction, onCompleted, onError, onRequest, onSent, redirectPage, startPage } from './core/page.js';
import { compareWithBaseline, findSite, summarizePage } from './core/report.js';
import { cookiesByService } from './core/activity.js';

const api = globalThis.browser ?? globalThis.chrome;
const TEST_HOOKS = false; // set to true only by tools/build.py --test
const SAVE_DELAY_MS = 400;

/** @type {Record<string, import('./core/page.js').PageState>} */
const tabs = {};
const dirty = new Set();
let saveTimer = null;
let ctx = null;
let glossary = null;
let sites = null;
let index = null;
const queue = [];

const loadJson = async (path) => (await fetch(api.runtime.getURL(path))).json();

const ready = (async () => {
  const [psl, trackers, g, s, i, stored] = await Promise.all([
    loadJson('data/psl.json'), loadJson('data/trackers.json'), loadJson('data/glossary.json'),
    loadJson('data/sites.json'), loadJson('data/index.json'), api.storage.session.get(null),
  ]);
  ctx = { trie: buildSuffixTrie(psl.rules), list: createTrackerList(trackers) };
  glossary = g;
  sites = s;
  index = i;
  for (const [key, value] of Object.entries(stored)) {
    if (key.startsWith('tab:') && value && value.v === 1) tabs[key.slice(4)] = value;
  }
  for (const fn of queue.splice(0)) fn();
})();

/** Run now if the data is loaded, otherwise after (events can arrive while the worker wakes up). */
function whenReady(fn) {
  if (ctx) fn();
  else queue.push(fn);
}

function touch(tabId) {
  dirty.add(String(tabId));
  if (!saveTimer) saveTimer = setTimeout(save, SAVE_DELAY_MS);
}

async function save() {
  saveTimer = null;
  const ids = [...dirty];
  dirty.clear();
  const data = {};
  for (const id of ids) if (tabs[id]) data['tab:' + id] = tabs[id];
  if (Object.keys(data).length) await api.storage.session.set(data);
  for (const id of ids) updateBadge(id);
}

function updateBadge(tabId) {
  const page = tabs[tabId];
  if (!page) return;
  const n = summarizePage(page, glossary).trackingBefore;
  const id = Number(tabId);
  api.action.setBadgeText({ tabId: id, text: page.host ? String(n) : '' }).catch(() => {});
  api.action.setBadgeBackgroundColor({ tabId: id, color: '#3a3f4b' }).catch(() => {});
}

const own = (id) => (Object.prototype.hasOwnProperty.call(tabs, id) ? tabs[id] : undefined);
const skip = (d) => d.tabId < 0 || d.documentLifecycle === 'prerender';

api.webRequest.onBeforeRequest.addListener((d) => {
  if (skip(d)) return;
  whenReady(() => {
    const id = String(d.tabId);
    if (d.type === 'main_frame') {
      const page = own(id);
      if (page && page.mainRequestId === d.requestId) {
        redirectPage(page, d.url, ctx);
      } else {
        const site = findSite(sites, d.url, ctx.trie);
        tabs[id] = startPage({ url: d.url, now: d.timeStamp, requestId: d.requestId,
          declared: site ? site.first_party_domains : [] }, ctx);
      }
    }
    const page = own(id);
    if (!page) return;
    onRequest(page, { requestId: d.requestId, url: d.url, type: d.type, now: d.timeStamp }, ctx);
    touch(id);
  });
}, { urls: ['<all_urls>'] });

api.webRequest.onSendHeaders.addListener((d) => {
  if (skip(d)) return;
  whenReady(() => {
    const page = own(String(d.tabId));
    if (page) { onSent(page, { requestId: d.requestId }); touch(d.tabId); }
  });
}, { urls: ['<all_urls>'] });

api.webRequest.onCompleted.addListener((d) => {
  if (skip(d)) return;
  whenReady(() => {
    const page = own(String(d.tabId));
    if (!page) return;
    let size = null;
    let sizeExact = false;
    if (typeof d.responseSize === 'number') { // Firefox: bytes actually transferred
      size = d.responseSize;
      sizeExact = true;
    } else {
      const h = (d.responseHeaders || []).find((x) => x.name.toLowerCase() === 'content-length');
      const n = h ? Number(h.value) : NaN;
      if (Number.isFinite(n)) size = n;
    }
    onCompleted(page, { requestId: d.requestId, fromCache: Boolean(d.fromCache), size, sizeExact });
    touch(d.tabId);
  });
}, { urls: ['<all_urls>'] }, ['responseHeaders']);

api.webRequest.onErrorOccurred.addListener((d) => {
  if (skip(d)) return;
  whenReady(() => {
    const page = own(String(d.tabId));
    if (page) { onError(page, { requestId: d.requestId, error: d.error }); touch(d.tabId); }
  });
}, { urls: ['<all_urls>'] });

api.tabs.onRemoved.addListener((tabId) => {
  delete tabs[String(tabId)];
  api.storage.session.remove('tab:' + tabId);
});

/** Everything the panel shows for one tab. */
async function report(tabId, url) {
  await ready;
  const page = own(String(tabId));
  if (!page) return { page: null, index };
  let cookies = {};
  try {
    const all = await api.cookies.getAll({ partitionKey: {} });
    cookies = cookiesByService(all, ctx, page, Date.now());
  } catch {
    try { cookies = cookiesByService(await api.cookies.getAll({}), ctx, page, Date.now()); } catch { /* no cookies */ }
  }
  const contacted = new Set(Object.keys(page.services));
  for (const key of Object.keys(cookies)) if (!contacted.has(key)) delete cookies[key];
  const summary = summarizePage(page, glossary, { cookies });
  const site = url ? findSite(sites, url, ctx.trie) : null;
  return { page: summary, baseline: compareWithBaseline(summary, site), index, categories: glossary.categories };
}

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Content scripts run inside web pages; our own pages (the panel) have an extension URL.
  const fromPage = !String(sender.url || '').startsWith(api.runtime.getURL(''));
  if (msg && msg.type === 'interaction' && fromPage) {
    whenReady(() => {
      const page = own(String(sender.tab.id));
      // the page's own clock, never in the future and at most a few seconds back
      const now = Date.now();
      const at = Number.isFinite(msg.at) ? Math.min(now, Math.max(msg.at, now - 3000)) : now;
      if (page && sender.frameId === 0) { markInteraction(page, { now: at, kind: String(msg.kind) }); touch(sender.tab.id); }
    });
    return false;
  }
  if (msg && msg.type === 'mark' && !fromPage) {
    whenReady(() => {
      const page = own(String(msg.tabId));
      if (page) { markInteraction(page, { now: Date.now(), kind: 'manual' }); touch(msg.tabId); }
      sendResponse(true);
    });
    return true;
  }
  if (msg && msg.type === 'report' && !fromPage) {
    report(msg.tabId, msg.url).then(sendResponse);
    return true;
  }
  if (TEST_HOOKS && msg && msg.type === 'test:reports') {
    ready.then(async () => {
      const out = {};
      for (const id of Object.keys(tabs)) out[id] = await report(id, null);
      sendResponse(out);
    });
    return true;
  }
  return false;
});
