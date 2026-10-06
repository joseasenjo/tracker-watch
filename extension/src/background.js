// Tracker Watch Lens background: feeds webRequest events to the core reducer, one report per tab.
// Chromium runs it as a service worker and Firefox as an event page; both put it to sleep when idle,
// so every tab report is kept in storage.session (written with a short delay) and reloaded on wake.
// Nothing leaves the browser.

import { buildSuffixTrie, registrableDomain } from './core/psl.js';
import { createTrackerList } from './core/classify.js';
import { markInteraction, noteBanner, notePing, noteSerp, onCompleted, onError, onRequest, onSent, redirectPage,
  startPage } from './core/page.js';
import { compareWithBaseline, findSite, summarizePage } from './core/report.js';
import { cookiesByService } from './core/activity.js';
import { hostOf } from './core/requests.js';
import { differenceReasons } from './core/differ.js';
import { classifyEngineUrl, compileEngines, engineCookies, engineForHost, searchParams } from './core/search.js';

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
let engines = [];
let profiles = null;
let consentNames = new Set();
const queue = [];
const ENV_BROWSER = typeof (globalThis.browser ?? {}).runtime?.getBrowserInfo === 'function' ? 'firefox' : 'chromium';
const CHOICES = new Set(['reject', 'accept', 'pay', 'other']);

// Chromium prerenders pages it expects you to open (search results, the top result): their requests arrive
// with documentLifecycle "prerender" before the page is shown. Each one gets its own report, keyed by its
// outermost frame, and becomes the tab's report when Chromium shows it (webNavigation reports the same
// documentId as "active"). Kept in memory only; reports never shown are dropped.
/** @type {Record<string, Record<string, import('./core/page.js').PageState>>} tabId -> frameId -> page */
const prerendered = {};
/** @type {Record<string, string>} `${tabId}:${frameId}` -> outermost prerendered frameId */
const frameRoot = {};
/** @type {Record<string, { tab: string, frame: string }>} documentId -> prerendered page */
const prerenderDocs = {};
const MAX_PRERENDERS = 4;
const PING_WINDOW_MS = 5000;
const RELOAD_AFTER_ANSWER_MS = 30000;

const loadJson = async (path) => (await fetch(api.runtime.getURL(path))).json();

const ready = (async () => {
  const [psl, trackers, g, s, i, engineProfiles, stored] = await Promise.all([
    loadJson('data/psl.json'), loadJson('data/trackers.json'), loadJson('data/glossary.json'),
    loadJson('data/sites.json'), loadJson('data/index.json'), loadJson('data/search_engines.json'),
    api.storage.session.get(null),
  ]);
  profiles = engineProfiles;
  engines = compileEngines(engineProfiles);
  consentNames = new Set(g.consent_tools.map((c) => c.name));
  ctx = { trie: buildSuffixTrie(psl.rules), list: createTrackerList(trackers) };
  glossary = g;
  sites = s;
  index = i;
  for (const [key, value] of Object.entries(stored)) {
    if (key.startsWith('tab:') && value && value.v === 2) tabs[key.slice(4)] = value;
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

/**
 * A top-level navigation. Same request id = a redirect of the current navigation: same-site hops stay one page
 * (as in the engine: target and final domain are both first party), but a cross-site hop such as a search
 * engine's click redirect starts the destination as its own page, remembering how the visitor arrived.
 */
function navigate(id, d) {
  const page = own(id);
  const target = classifyEngineUrl(engines, d.url);
  if (page && page.mainRequestId === d.requestId) {
    const reg = registrableDomain(ctx.trie, hostOf(d.url) || '');
    if (page.firstParty.includes(reg)) {
      redirectPage(page, d.url, ctx);
      return;
    }
    const arrival = {
      engine: page.engineRedirect ?? page.arrival?.engine ?? null, fromSite: page.arrival?.fromSite ?? page.site,
      redirect: Boolean(page.engineRedirect) || Boolean(page.arrival?.redirect), ping: Boolean(page.arrival?.ping),
    };
    tabs[id] = begin(d, arrival);
    return;
  }
  delete prerendered[id]; // a normal navigation: pages prerendered so far were not used
  const next = begin(d, arrivalFrom(page, d));
  // Some sites reload the whole page right after you answer their banner: keep that answer in view.
  if (page && page.consent.click && page.consent.click.tool && page.site === next.site
      && page.interactionAt !== null && d.timeStamp - page.interactionAt < RELOAD_AFTER_ANSWER_MS) {
    next.consent.previous = page.consent.click;
  }
  if (target && target.kind === 'redirect') next.engineRedirect = target.engine.id;
  if (target && target.kind === 'results') {
    noteSerp(next, { engine: target.engine.id, params: searchParams(target.engine, d.url),
      links: { total: 0, ping: 0, redirect: 0, mousedown: 0 } });
  }
  tabs[id] = next;
}

/** Start a prerendered page's own report, or map a frame inside it to its outermost frame. */
function notePrerenderRequest(tab, d) {
  const pages = (prerendered[tab] ??= {});
  if (d.frameType === 'outermost_frame' && d.type === 'main_frame') {
    const frame = String(d.frameId);
    if (!Object.prototype.hasOwnProperty.call(pages, frame)) {
      const keys = Object.keys(pages);
      if (keys.length >= MAX_PRERENDERS) delete pages[keys[0]];
      const from = own(tab);
      const next = begin(d, arrivalFrom(from, d));
      const target = classifyEngineUrl(engines, d.url);
      if (target && target.kind === 'redirect') next.engineRedirect = target.engine.id;
      if (target && target.kind === 'results') {
        noteSerp(next, { engine: target.engine.id, params: searchParams(target.engine, d.url),
          links: { total: 0, ping: 0, redirect: 0, mousedown: 0 } });
      }
      pages[frame] = next;
    }
  } else if (d.type === 'sub_frame') {
    const parent = String(d.parentFrameId);
    const root = Object.prototype.hasOwnProperty.call(pages, parent) ? parent : frameRoot[`${tab}:${parent}`];
    if (root !== undefined) frameRoot[`${tab}:${d.frameId}`] = root;
  }
}

/** How the visitor arrived from the page currently shown in the tab (only from a results page of another site). */
function arrivalFrom(from, d) {
  if (!from || !from.search) return null;
  const reg = registrableDomain(ctx.trie, hostOf(d.url) || '');
  if (reg && reg === from.site) return null; // another page of the search engine itself
  return { engine: from.search.engine, fromSite: from.site, redirect: false,
    ping: from.search.lastPingAt !== null && Math.abs(d.timeStamp - from.search.lastPingAt) < PING_WINDOW_MS };
}

function begin(d, arrival) {
  const site = findSite(sites, d.url, ctx.trie);
  return startPage({ url: d.url, now: d.timeStamp, requestId: d.requestId,
    declared: site ? site.first_party_domains : [], arrival }, ctx);
}

/**
 * Hyperlink-auditing pings to a search engine around a click on a result. On the results page they are noted
 * (and counted as usual); on the page the click led to they belong to the click, not to that page.
 */
function pingFromEngine(page, d) {
  const engine = engineForHost(engines, hostOf(d.url) || '');
  if (!engine) return false;
  if (page.search && page.search.engine === engine.id) {
    notePing(page, d.timeStamp);
    return false;
  }
  const from = page.arrival?.engine ?? page.engineRedirect;
  if (from === engine.id && d.timeStamp - page.startedAt < PING_WINDOW_MS) {
    if (page.arrival) page.arrival.ping = true;
    return true;
  }
  return false;
}

const own = (id) => (Object.prototype.hasOwnProperty.call(tabs, id) ? tabs[id] : undefined);
const skip = (d) => d.tabId < 0;
const isPrerender = (d) => d.documentLifecycle === 'prerender';

/** The report a webRequest event belongs to: the tab's, or that of a page being prerendered in it. */
function pageOf(d) {
  const tab = String(d.tabId);
  if (!isPrerender(d)) return own(tab);
  const pages = prerendered[tab] || {};
  const frame = String(d.frameId);
  const root = Object.prototype.hasOwnProperty.call(pages, frame) ? frame : frameRoot[`${tab}:${frame}`];
  return root !== undefined ? pages[root] : undefined;
}

api.webRequest.onBeforeRequest.addListener((d) => {
  if (skip(d)) return;
  whenReady(() => {
    const id = String(d.tabId);
    if (isPrerender(d)) {
      notePrerenderRequest(id, d);
      const page = pageOf(d);
      if (page) onRequest(page, { requestId: d.requestId, url: d.url, type: d.type, now: d.timeStamp }, ctx);
      return;
    }
    if (d.type === 'main_frame') navigate(id, d);
    const page = own(id);
    if (!page) return;
    if (d.type === 'ping' && pingFromEngine(page, d)) { touch(id); return; }
    onRequest(page, { requestId: d.requestId, url: d.url, type: d.type, now: d.timeStamp }, ctx);
    touch(id);
  });
}, { urls: ['<all_urls>'] });

const sentListener = (d) => {
  if (skip(d)) return;
  whenReady(() => {
    const page = pageOf(d);
    if (page) { onSent(page, { requestId: d.requestId, headers: d.requestHeaders }, ctx); touch(d.tabId); }
  });
};
// Chromium hides Cookie, Referer and Accept-Language unless asked with "extraHeaders"; Firefox has no such
// option and rejects it (M0 spike).
try {
  api.webRequest.onSendHeaders.addListener(sentListener, { urls: ['<all_urls>'] }, ['requestHeaders', 'extraHeaders']);
} catch {
  api.webRequest.onSendHeaders.addListener(sentListener, { urls: ['<all_urls>'] }, ['requestHeaders']);
}

api.webRequest.onCompleted.addListener((d) => {
  if (skip(d)) return;
  whenReady(() => {
    const page = pageOf(d);
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
    const page = pageOf(d);
    if (page) { onError(page, { requestId: d.requestId, error: d.error }); touch(d.tabId); }
  });
}, { urls: ['<all_urls>'] });

// A prerendered page is committed (still hidden): remember its document; when the same document becomes
// "active", Chromium has shown it in the tab and its report becomes the tab's report.
api.webNavigation.onCommitted.addListener((d) => {
  if (d.tabId < 0 || d.frameType !== 'outermost_frame' || !d.documentId) return;
  whenReady(() => {
    const tab = String(d.tabId);
    if (d.documentLifecycle === 'prerender') {
      const frame = String(d.frameId);
      if (prerendered[tab] && Object.prototype.hasOwnProperty.call(prerendered[tab], frame)) {
        prerenderDocs[d.documentId] = { tab, frame };
      }
      return;
    }
    const doc = prerenderDocs[d.documentId];
    if (!doc || doc.tab !== tab || d.documentLifecycle !== 'active') return;
    const page = prerendered[tab] && prerendered[tab][doc.frame];
    delete prerenderDocs[d.documentId];
    if (!page) return;
    const from = own(tab);
    // the click that showed it may have sent a ping from the results page
    if (page.arrival && from && from.search && from.search.lastPingAt !== null
        && Math.abs(d.timeStamp - from.search.lastPingAt) < PING_WINDOW_MS) page.arrival.ping = true;
    page.mainRequestId = null;
    tabs[tab] = page;
    delete prerendered[tab];
    touch(tab);
  });
});

api.tabs.onRemoved.addListener((tabId) => {
  delete prerendered[String(tabId)];
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
  const baseline = compareWithBaseline(summary, site);
  const name = (id) => engines.find((e) => e.id === id)?.name ?? null;
  let search = null;
  if (page.search) {
    const engine = engines.find((e) => e.id === page.search.engine);
    let engineCookieList = [];
    try {
      engineCookieList = engineCookies(engine, await api.cookies.getAll({ domain: page.site }), Date.now());
    } catch { /* cookies unavailable */ }
    search = { ...page.search, name: engine.name, cookies: engineCookieList, cookieSource: engine.cookie_source,
      accountLinks: engine.account_links, checked: profiles.checked };
  }
  const consentTools = [...new Set(summary.operators.flatMap((o) => o.services)
    .filter((x) => x.category === 'consent_management').map((x) => x.entity))];
  return {
    page: summary, told: page.told, baseline, index, categories: glossary.categories,
    consent: { banners: page.consent.banners, click: page.consent.click, previous: page.consent.previous ?? null,
      toolsContacted: consentTools },
    search, arrival: page.arrival ? { ...page.arrival, engineName: name(page.arrival.engine) } : null,
    reasons: differenceReasons(summary, page, baseline, { browser: ENV_BROWSER, nowMs: Date.now() }),
  };
}

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Content scripts run inside web pages; our own pages (the panel) have an extension URL.
  const fromPage = !String(sender.url || '').startsWith(api.runtime.getURL(''));
  if (fromPage && sender.documentLifecycle === 'prerender') return false; // not shown yet
  if (msg && msg.type === 'interaction' && fromPage) {
    whenReady(() => {
      const page = own(String(sender.tab.id));
      // the page's own clock, never in the future and at most a few seconds back
      const now = Date.now();
      const at = Number.isFinite(msg.at) ? Math.min(now, Math.max(msg.at, now - 3000)) : now;
      // a click inside a consent banner may happen in a sub-frame (several tools draw the banner in a frame)
      let on = null;
      if (msg.on && typeof msg.on === 'object' && CHOICES.has(msg.on.choice)) {
        on = { tool: consentNames.has(msg.on.tool) ? msg.on.tool : null, choice: msg.on.choice };
      }
      if (page) { markInteraction(page, { now: at, kind: msg.kind === 'key' ? 'key' : 'click', on }); touch(sender.tab.id); }
    });
    return false;
  }
  if (msg && msg.type === 'banner' && fromPage) {
    whenReady(() => {
      const page = own(String(sender.tab.id));
      if (page && consentNames.has(msg.tool)) { noteBanner(page, msg.tool); touch(sender.tab.id); }
    });
    return false;
  }
  if (msg && msg.type === 'serp' && fromPage) {
    whenReady(() => {
      const page = own(String(sender.tab.id));
      if (page && page.search && msg.links && typeof msg.links === 'object') {
        noteSerp(page, { engine: page.search.engine, params: page.search.params, links: msg.links });
        touch(sender.tab.id);
      }
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
