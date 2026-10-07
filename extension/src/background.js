// Tracker Watch Lens background: feeds webRequest events to the core reducer, one report per tab.
// Chromium runs it as a service worker and Firefox as an event page; both put it to sleep when idle,
// so every tab report is kept in storage.session (written with a short delay) and reloaded on wake.
// Nothing leaves the browser.

import { buildSuffixTrie, registrableDomain } from './core/psl.js';
import { createTrackerList } from './core/classify.js';
import { markInteraction, noteBanner, noteBehaviour, notePing, noteSerp, onCompleted, onError, onRequest, onSent, redirectPage,
  startPage } from './core/page.js';
import { commonOperators, compareWithBaseline, findSite, journeyEntry, summarizePage } from './core/report.js';
import { cookiesByService } from './core/activity.js';
import { hostOf } from './core/requests.js';
import { differenceReasons } from './core/differ.js';
import { classifyEngineUrl, compileEngines, engineCookies, engineForHost, searchParams, signedIn } from './core/search.js';
import { DAY_OPTIONS, STORE_KEY, carryTest, currentRun, exportStore, normalizeStore, purge, saveRun, siteView,
  startTest } from './core/mytests.js';
import { SUMMARY_KEY, addPage, normalizeSummary, periodView, purgeSummary } from './core/summary.js';

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
// "Your own banner test" (opt-in): kept in storage.local, written at most every MYTEST_DELAY_MS per tab.
let myStore = normalizeStore(null);
// F8 "Your week" (opt-in): daily aggregates by company, written a moment after a page is left.
let summaryStore = normalizeSummary(null);
let summaryTimer = null;
const myDirty = new Set();
let myTimer = null;
const MYTEST_DELAY_MS = 1500;
const CLEARED_VALID_MS = 10 * 60000;

const loadJson = async (path) => (await fetch(api.runtime.getURL(path))).json();

const ready = (async () => {
  const [psl, trackers, g, s, i, engineProfiles, stored] = await Promise.all([
    loadJson('data/psl.json'), loadJson('data/trackers.json'), loadJson('data/glossary.json'),
    loadJson('data/sites.json'), loadJson('data/index.json'), loadJson('data/search_engines.json'),
    api.storage.session.get(null),
  ]);
  const local = await api.storage.local.get([STORE_KEY, SUMMARY_KEY]);
  myStore = purge(normalizeStore(local[STORE_KEY]), Date.now());
  summaryStore = purgeSummary(normalizeSummary(local[SUMMARY_KEY]), Date.now());
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
  for (const id of ids) {
    updateBadge(id);
    if (myStore.settings.enabled && tabs[id] && tabs[id].myTest) myDirty.add(id);
  }
  if (myDirty.size && !myTimer) myTimer = setTimeout(saveMyTests, MYTEST_DELAY_MS);
}

/** Cookies per contacted service, as now in the browser (names and lifetimes, never values). */
async function serviceCookies(page) {
  try {
    return cookiesByService(await api.cookies.getAll({ partitionKey: {} }), ctx, page, Date.now());
  } catch {
    try { return cookiesByService(await api.cookies.getAll({}), ctx, page, Date.now()); } catch { return {}; }
  }
}

async function saveMyTests() {
  myTimer = null;
  const ids = [...myDirty];
  myDirty.clear();
  if (!myStore.settings.enabled) return;
  for (const id of ids) {
    const page = own(id);
    if (!page || !page.myTest) continue;
    const run = currentRun(page.myTest, summarizePage(page, glossary), page, await serviceCookies(page),
      Boolean(page.myTestContinued));
    saveRun(myStore, page.site, page.myTest.choice, run);
  }
  purge(myStore, Date.now());
  await api.storage.local.set({ [STORE_KEY]: myStore });
}

/** F8: a page is counted once, when it is left or its tab is closed (only the companies, never the page). */
function countForSummary(page) {
  if (!summaryStore.enabled || !page || !page.host || page.engineRedirect || page.summarized) return;
  page.summarized = true;
  addPage(summaryStore, journeyEntry(summarizePage(page, glossary)).operators, Date.now());
  if (!summaryTimer) {
    summaryTimer = setTimeout(() => {
      summaryTimer = null;
      purgeSummary(summaryStore, Date.now());
      api.storage.local.set({ [SUMMARY_KEY]: summaryStore });
    }, 1000);
  }
}

/** Lens cleared this site's data a moment ago (the test then starts from a first visit). */
async function wasCleared(site) {
  const key = 'cleared:' + site;
  const at = (await api.storage.session.get(key))[key];
  return typeof at === 'number' && Date.now() - at < CLEARED_VALID_MS;
}

/**
 * Step 2 of the guided test: remove this site's cookies (and, with the optional browsingData permission,
 * its other site data) so its banner shows again, then reload. Third-party cookies outside this site's
 * partition are left alone: they belong to every site.
 */
async function clearSite(tabId) {
  const page = own(String(tabId));
  if (!page || !page.site) return false;
  const domains = [...new Set([page.site, ...page.firstParty])];
  const mine = (d) => domains.some((x) => d === x || d.endsWith('.' + x));
  let all = [];
  try { all = await api.cookies.getAll({ partitionKey: {} }); } catch { all = await api.cookies.getAll({}); }
  for (const c of all) {
    const top = c.partitionKey && c.partitionKey.topLevelSite ? hostOf(c.partitionKey.topLevelSite) || '' : '';
    if (!mine(c.domain.replace(/^\./, '')) && !(top && mine(top))) continue;
    const details = { url: `http${c.secure ? 's' : ''}://${c.domain.replace(/^\./, '')}${c.path}`, name: c.name,
      storeId: c.storeId };
    if (c.partitionKey) details.partitionKey = c.partitionKey;
    try { await api.cookies.remove(details); } catch { /* already gone */ }
  }
  if (api.browsingData && await api.permissions.contains({ permissions: ['browsingData'] })) {
    const kinds = { cookies: true, localStorage: true, indexedDB: true, cacheStorage: true, serviceWorkers: true };
    try {
      await api.browsingData.remove({ origins: domains.flatMap((d) => [`https://${d}`, `https://www.${d}`]) }, kinds);
    } catch {
      try { await api.browsingData.remove({ hostnames: domains.flatMap((d) => [d, 'www.' + d]) }, { cookies: true, localStorage: true }); } catch { /* not supported */ }
    }
  }
  await api.storage.session.set({ ['cleared:' + page.site]: Date.now() });
  await api.tabs.reload(Number(tabId), { bypassCache: true });
  return true;
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
    tabs[id] = carryJourney(begin(d, arrival), page);
    return;
  }
  delete prerendered[id]; // a normal navigation: pages prerendered so far were not used
  const next = begin(d, arrivalFrom(page, d));
  // Some sites reload the whole page right after you answer their banner: keep that answer in view.
  if (page && page.consent.click && page.consent.click.choice !== 'other' && page.site === next.site
      && page.interactionAt !== null && d.timeStamp - page.interactionAt < RELOAD_AFTER_ANSWER_MS) {
    next.consent.previous = page.consent.click;
    if (page.myTest) {
      const run = currentRun(page.myTest, summarizePage(page, glossary), page, {}, Boolean(page.myTestContinued));
      next.myTest = carryTest(page.myTest, run);
      next.myTestContinued = true;
    }
  }
  if (target && target.kind === 'redirect') next.engineRedirect = target.engine.id;
  if (target && target.kind === 'results') {
    noteSerp(next, { engine: target.engine.id, params: searchParams(target.engine, d.url),
      links: { total: 0, ping: 0, redirect: 0, mousedown: 0 } });
  }
  tabs[id] = carryJourney(next, page);
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

const MAX_JOURNEY = 8;
/** F5: the page being left becomes the last row of the new page's journey (session memory, this tab only). */
function carryJourney(next, prev) {
  countForSummary(prev);
  if (!prev || !prev.host || prev.engineRedirect) {
    if (prev && prev.journey) next.journey = prev.journey;
    return next;
  }
  next.journey = [...(prev.journey || []), journeyEntry(summarizePage(prev, glossary))].slice(-MAX_JOURNEY);
  return next;
}

function begin(d, arrival) {
  const site = findSite(sites, d.url, ctx.trie);
  return startPage({ url: d.url, now: d.timeStamp, requestId: d.requestId,
    declared: site ? site.first_party_domains : [], arrival }, ctx);
}

/**
 * Hyperlink-auditing pings (<a ping>) to a search engine around a click on a result. Chromium gives the same
 * request type "ping" to navigator.sendBeacon, so only requests carrying the Ping-To header (sent with
 * hyperlink pings only) count. On the results page they are noted; on the page the click led to they belong
 * to the click and are not counted as contacts of that page.
 */
function isClickPing(d) {
  return d.type === 'ping' && (d.requestHeaders || []).some((h) => h.name.toLowerCase() === 'ping-to');
}

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
    onRequest(page, { requestId: d.requestId, url: d.url, type: d.type, now: d.timeStamp }, ctx);
    touch(id);
  });
}, { urls: ['<all_urls>'] });

const sentListener = (d) => {
  if (skip(d)) return;
  whenReady(() => {
    const page = pageOf(d);
    if (!page) return;
    if (isClickPing(d) && pingFromEngine(page, d)) {
      if (Object.prototype.hasOwnProperty.call(page.pending, d.requestId)) delete page.pending[d.requestId];
      touch(d.tabId);
      return;
    }
    onSent(page, { requestId: d.requestId, headers: d.requestHeaders }, ctx); touch(d.tabId);
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
    tabs[tab] = carryJourney(page, from);
    delete prerendered[tab];
    touch(tab);
  });
});

api.tabs.onRemoved.addListener((tabId) => {
  whenReady(() => countForSummary(own(String(tabId))));
  delete prerendered[String(tabId)];
  delete tabs[String(tabId)];
  api.storage.session.remove('tab:' + tabId);
});

/** Settings page: "Your week" and "Delete all my data". */
async function dataMessage(msg) {
  const now = Date.now();
  if (msg.type === 'summary:get') {
    const sites = Object.keys(myStore.sites).length;
    return { summary: { enabled: summaryStore.enabled, week: periodView(summaryStore, now, 7),
      month: periodView(summaryStore, now, 30) }, mytests: { settings: myStore.settings, sites } };
  }
  if (msg.type === 'summary:settings' && typeof msg.enabled === 'boolean') {
    summaryStore.enabled = msg.enabled;
  } else if (msg.type === 'summary:delete') {
    summaryStore.days = {};
  } else if (msg.type === 'data:deleteAll') {
    // everything Lens keeps beyond the open tabs' reports (those live in session memory and end with the browser)
    await api.storage.local.clear();
    myStore = normalizeStore(null);
    summaryStore = normalizeSummary(null);
    return true;
  }
  await api.storage.local.set({ [SUMMARY_KEY]: summaryStore });
  return true;
}

async function myTestsMessage(msg) {
  if (msg.type === 'mytests:settings') {
    if (typeof msg.enabled === 'boolean') myStore.settings.enabled = msg.enabled;
    if (DAY_OPTIONS.includes(msg.days)) myStore.settings.days = msg.days;
  } else if (msg.type === 'mytests:delete') {
    if (typeof msg.site === 'string') delete myStore.sites[msg.site];
    else myStore.sites = {};
  } else if (msg.type === 'mytests:export') {
    return exportStore(myStore, Date.now());
  } else if (msg.type === 'mytests:clearSite') {
    return clearSite(msg.tabId);
  }
  purge(myStore, Date.now());
  await api.storage.local.set({ [STORE_KEY]: myStore });
  return true;
}

function journeyView(page, summary) {
  const rows = page.journey || [];
  if (!rows.length) return null;
  const here = journeyEntry(summary);
  const previous = rows[rows.length - 1];
  return { rows: [...rows, { ...here, current: true }], previous: previous.site,
    common: commonOperators(previous, here) };
}

/** Everything the panel shows for one tab. */
async function report(tabId, url) {
  await ready;
  const page = own(String(tabId));
  if (!page) return { page: null, index };
  const cookies = await serviceCookies(page);
  const contacted = new Set(Object.keys(page.services));
  for (const key of Object.keys(cookies)) if (!contacted.has(key)) delete cookies[key];
  const summary = summarizePage(page, glossary, { cookies, liveCookies: true, behaviours: page.behaviours ?? {},
    behavioursOther: page.behavioursOther ?? {} });
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
      server: engine.server, signedIn: signedIn(engine, engineCookieList), checked: profiles.checked };
  }
  const consentTools = [...new Set(summary.operators.flatMap((o) => o.services)
    .filter((x) => x.category === 'consent_management').map((x) => x.entity))];
  const inFlight = Object.values(page.pending).filter((p) => !p.c).length;
  const loading = inFlight > 0 || (typeof page.lastAt === 'number' && Date.now() - page.lastAt < 2000);
  return {
    page: summary, told: page.told, baseline, index, categories: glossary.categories, loading,
    consent: { banners: page.consent.banners, click: page.consent.click, previous: page.consent.previous ?? null,
      payOrAccept: Boolean(page.consent.payOrAccept),
      toolsContacted: consentTools },
    search, arrival: page.arrival ? { ...page.arrival, engineName: name(page.arrival.engine) } : null,
    journey: journeyView(page, summary),
    mytests: { settings: myStore.settings, site: page.site, view: siteView(myStore, page.site),
      running: page.myTest ? { choice: page.myTest.choice, continued: Boolean(page.myTestContinued) } : null },
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
      if (!page) return;
      const first = page.interactionAt === null;
      markInteraction(page, { now: at, kind: msg.kind === 'key' ? 'key' : 'click', on });
      if (first && myStore.settings.enabled && page.consent.click && !page.myTest) {
        wasCleared(page.site).then((cleared) => {
          page.myTest = startTest(summarizePage(page, glossary), page.consent.click,
            { nowMs: Date.now(), cleared, payOrAccept: Boolean(page.consent.payOrAccept) });
          touch(sender.tab.id);
        });
      }
      touch(sender.tab.id);
    });
    return false;
  }
  if (msg && msg.type === 'banner' && fromPage) {
    whenReady(() => {
      const page = own(String(sender.tab.id));
      if (page && consentNames.has(msg.tool)) { noteBanner(page, msg.tool, msg.payOrAccept === true); touch(sender.tab.id); }
    });
    return false;
  }
  if (msg && msg.type === 'behaviour' && fromPage) {
    whenReady(() => {
      const page = own(String(sender.tab.id));
      if (page && typeof msg.kind === 'string' && typeof msg.host === 'string') {
        noteBehaviour(page, { kind: msg.kind, host: msg.host }, ctx);
        touch(sender.tab.id);
      }
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
  if (msg && typeof msg.type === 'string' && (msg.type.startsWith('summary:') || msg.type.startsWith('data:'))
      && !fromPage) {
    ready.then(() => dataMessage(msg)).then(sendResponse);
    return true;
  }
  if (msg && typeof msg.type === 'string' && msg.type.startsWith('mytests:') && !fromPage) {
    ready.then(() => myTestsMessage(msg)).then(sendResponse);
    return true;
  }
  if (msg && msg.type === 'report' && !fromPage) {
    report(msg.tabId, msg.url).then(sendResponse);
    return true;
  }
  if (TEST_HOOKS && msg && msg.type === 'test:summary-on') {
    ready.then(() => dataMessage({ type: 'summary:settings', enabled: true })).then(sendResponse);
    return true;
  }
  if (TEST_HOOKS && msg && msg.type === 'test:mytests-on') {
    ready.then(() => myTestsMessage({ type: 'mytests:settings', enabled: true })).then(sendResponse);
    return true;
  }
  if (TEST_HOOKS && msg && msg.type === 'test:reports') {
    ready.then(async () => {
      const out = {};
      for (const id of Object.keys(tabs)) out[id] = await report(id, null);
      out._summary = periodView(summaryStore, Date.now(), 7);
      sendResponse(out);
    });
    return true;
  }
  return false;
});
