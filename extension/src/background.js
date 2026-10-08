// Tracker Watch Lens background: feeds webRequest events to the core reducer, one report per tab.
// Chromium runs it as a service worker and Firefox as an event page; both put it to sleep when idle,
// so every tab report is kept in storage.session (written with a short delay) and reloaded on wake.
// Nothing leaves the browser.

import { buildSuffixTrie, registrableDomain } from './core/psl.js';
import { createTrackerList, isTracking, lookup } from './core/classify.js';
import { blockedDomains, markInteraction, noteAutoReject, noteBanner, noteBehaviour, notePing, noteSerp, onCompleted, onError, onRequest, onSent, redirectPage,
  startPage } from './core/page.js';
import { commonOperators, compareWithBaseline, findSite, journeyEntry, summarizePage } from './core/report.js';
import { cookiesByService } from './core/activity.js';
import { hostOf } from './core/requests.js';
import { differenceReasons } from './core/differ.js';
import { classifyEngineUrl, compileEngines, engineCookies, engineForHost, searchParams, signedIn } from './core/search.js';
import { DAY_OPTIONS, STORE_KEY, carryTest, currentRun, exportStore, normalizeStore, purge, saveRun, siteView,
  startTest } from './core/mytests.js';
import { cnameMatch, noteCloaked, worthResolving } from './core/cname.js';
import { SUMMARY_KEY, addPage, normalizeSummary, periodView, purgeSummary } from './core/summary.js';
import { hostPlan, styleSheet } from './core/cosmetic.js';
import { LEARN_KEY, idLikeCookie, learnView, learnedActions, normalizeLearn, noteSignal } from './core/learn.js';

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
let cnameTargets = {};
const resolved = new Map(); // host -> match or null, for this browser session
const MAX_RESOLVED = 2000;

/**
 * Firefox only: is this host of the site itself a tracker in disguise? Resolved once per host; with the
 * extended clean mode on, the host is then blocked for the rest of the session.
 */
function checkCloaked(tabId, host) {
  if (!api.dns || !host) return;
  const page = own(tabId);
  if (!page || !worthResolving(page, host, registrableDomain(ctx.trie, host))) return;
  const apply = (m) => {
    const p = own(tabId);
    if (!m || !p || !p.firstParty.includes(registrableDomain(ctx.trie, host))) return;
    noteCloaked(p, host, m);
    touch(tabId);
    if (extendedOn() && dnr && dnr.updateSessionRules) {
      dnr.getSessionRules().then((rules) => {
        if (rules.some((r) => r.condition.requestDomains && r.condition.requestDomains.includes(host))) return;
        const id = Math.max(0, ...rules.map((r) => r.id)) + 1;
        return dnr.updateSessionRules({ addRules: [{ id, priority: P_LIST, action: { type: 'block' },
          condition: { requestDomains: [host], excludedResourceTypes: ['main_frame'] } }] });
      }).catch(() => {});
    }
  };
  if (resolved.has(host)) { apply(resolved.get(host)); return; }
  if (resolved.size >= MAX_RESOLVED) return;
  resolved.set(host, null);
  api.dns.resolve(host, ['canonical_name']).then((r) => {
    const m = r && r.canonicalName && r.canonicalName !== host ? cnameMatch(cnameTargets, r.canonicalName) : null;
    resolved.set(host, m);
    apply(m);
  }).catch(() => {});
}
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
const COUNTING_MS = 20000;
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
  const local = await api.storage.local.get([STORE_KEY, SUMMARY_KEY, LEARN_KEY]);
  learnStore = normalizeLearn(local[LEARN_KEY]);
  learnedSet = new Set(learnedActions(learnStore).block); // blocked ones (Firefox attribution, site-only list)
  myStore = purge(normalizeStore(local[STORE_KEY]), Date.now());
  summaryStore = purgeSummary(normalizeSummary(local[SUMMARY_KEY]), Date.now());
  profiles = engineProfiles;
  cnameTargets = await loadJson('data/cname_trackers.json').catch(() => ({}));
  engines = compileEngines(engineProfiles);
  consentNames = new Set(g.consent_tools.map((c) => c.name));
  ctx = { trie: buildSuffixTrie(psl.rules), list: createTrackerList(trackers) };
  glossary = g;
  sites = s;
  index = i;
  for (const [key, value] of Object.entries(stored)) {
    if (key.startsWith('tab:') && value && value.v === 2) tabs[key.slice(4)] = value;
  }
  await migratePauses().catch(() => null);
  await cleanState().catch(() => null); // which list clean mode uses, if any
  await syncLearnRule().catch(() => null);
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
const cookieCache = new Map(); // page -> { at, value }: the panel refreshes every second while a page loads
async function serviceCookies(page) {
  const hit = cookieCache.get(page);
  if (hit && Date.now() - hit.at < 5000) return structuredClone(hit.value);
  const value = await readServiceCookies(page);
  if (cookieCache.size > 20) cookieCache.clear();
  cookieCache.set(page, { at: Date.now(), value });
  return structuredClone(value);
}

async function readServiceCookies(page) {
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


/** Is the count of this page still settling? (requests in flight or just made, in its first COUNTING_MS) */
function isCounting(page) {
  const inFlight = Object.values(page.pending).some((p) => !p.c);
  const loading = inFlight || (typeof page.lastAt === 'number' && Date.now() - page.lastAt < 2000);
  return { loading, counting: loading && Date.now() - Math.max(page.startedAt, page.shownAt || 0) < COUNTING_MS };
}

const badgeTimers = new Map();
/**
 * The number on the toolbar icon. While the page is still counting: "15…" on amber, so it does not look final;
 * then the plain number on grey. Looked at again shortly after, since a page can go quiet without any event.
 */
function updateBadge(tabId) {
  const page = tabs[tabId];
  if (!page) return;
  const n = summarizePage(page, glossary).trackingBefore;
  const id = Number(tabId);
  const { counting } = isCounting(page);
  api.action.setBadgeText({ tabId: id, text: page.host ? String(n) + (counting ? '…' : '') : '' }).catch(() => {});
  api.action.setBadgeBackgroundColor({ tabId: id, color: counting ? '#a35a00' : '#3a3f4b' }).catch(() => {});
  api.action.setTitle({ tabId: id, title: counting ? api.i18n.getMessage('badgeCounting') : api.i18n.getMessage('extName') }).catch(() => {});
  clearTimeout(badgeTimers.get(tabId));
  if (counting) badgeTimers.set(tabId, setTimeout(() => updateBadge(tabId), 2500));
  else badgeTimers.delete(tabId);
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
      if (page) {
        onRequest(page, { requestId: d.requestId, url: d.url, type: d.type, now: d.timeStamp, frameId: d.frameId,
          parentFrameId: d.parentFrameId }, ctx);
      }
      return;
    }
    if (d.type === 'main_frame') navigate(id, d);
    const page = own(id);
    if (!page) return;
    onRequest(page, { requestId: d.requestId, url: d.url, type: d.type, now: d.timeStamp, frameId: d.frameId,
      parentFrameId: d.parentFrameId }, ctx);
    checkCloaked(id, hostOf(d.url));
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
    if (learnStore.enabled && !isPrerender(d)) {
      // step 4: an identifier-like cookie sent to a third party that is on no list (the value is read here only)
      const p = Object.prototype.hasOwnProperty.call(page.pending, d.requestId) ? page.pending[d.requestId] : null;
      if (p && p.t && p.d && !p.s) {
        const cookie = (d.requestHeaders || []).find((h) => h.name.toLowerCase() === 'cookie');
        if (cookie && idLikeCookie(cookie.value)) learnSignal(p.d, page.site, 'cookie');
      }
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
    if (page) {
      onError(page, { requestId: d.requestId, error: blockedByClean(d, page) ? 'net::ERR_BLOCKED_BY_CLIENT' : d.error });
      touch(d.tabId);
    }
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
    page.shownAt = d.timeStamp; // the visitor sees it only now (for the "Counting" state)
    tabs[tab] = carryJourney(page, from);
    delete prerendered[tab];
    touch(tab);
  });
});

/**
 * "Block ads": hide empty ad slots in each page and frame as it commits, with EasyList's element hiding rules for
 * that host, as a user style sheet (the page cannot undo it). Not on sites where clean mode is paused.
 */
api.webNavigation.onCommitted.addListener((d) => {
  if (!adsOn || d.tabId < 0 || !/^https?:/.test(d.url) || !api.scripting) return;
  whenReady(async () => {
    const host = hostOf(d.url) || '';
    const top = d.frameId === 0 ? registrableDomain(ctx.trie, host) : own(String(d.tabId))?.site;
    if (!host || (top && pausedSites.includes(top))) return;
    if (!cosmeticData) cosmeticData = await loadJson('data/cosmetic.json').catch(() => null);
    if (!cosmeticData) return;
    genericCss ??= styleSheet(cosmeticData.generic); // built once: the large part, the same for nearly every host
    let plan = cssCache.get(host);
    if (plan === undefined) {
      const p = hostPlan(cosmeticData, host);
      plan = { generic: p.generic, css: styleSheet(p.own) };
      if (cssCache.size > 500) cssCache.clear();
      cssCache.set(host, plan);
    }
    const target = { tabId: d.tabId, frameIds: [d.frameId] };
    for (const css of [plan.generic ? genericCss : '', plan.css]) {
      if (css) api.scripting.insertCSS({ target, css, origin: 'USER' }).catch(() => {});
    }
  });
});

api.tabs.onRemoved.addListener((tabId) => {
  whenReady(() => countForSummary(own(String(tabId))));
  delete prerendered[String(tabId)];
  delete tabs[String(tabId)];
  api.storage.session.remove('tab:' + tabId);
});

/**
 * Firefox reports a request stopped by declarativeNetRequest as NS_ERROR_ABORT, like any cancelled request
 * (Chromium says ERR_BLOCKED_BY_CLIENT). With clean mode on, a cancelled request to a tracking service of the
 * active list, made by another site, was stopped by it.
 */
let cleanBlocking = null;
// "siteonly" = the extended lists plus the "site only" rules; both use EasyPrivacy
const extendedOn = () => cleanBlocking === 'extended' || cleanBlocking === 'siteonly';
let easyprivacyDomains = null; // loaded only when the extended list is on (Firefox attribution of its blocks)
let siteonlySafe = null; // domains "site only" lets through everywhere (Firefox attribution of its blocks)
let siteAllowed = []; // [{ site, domain }] allowed from the panel, mirrored from the browser's rules
let learnStore = normalizeLearn(null); // step 4 (opt-in), kept in storage.local
let learnedSet = new Set();
let learnTimer = null;
let adsOn = false; // "Block ads" (EasyList), mirrored from the browser's enabled rule sets
let pausedSites = [];
let easylistDomains = null; // loaded only when ads are blocked (attribution of its blocks)
let cosmeticData = null;
const cssCache = new Map(); // host -> { generic: whether the shared sheet applies, css: the host's own sheet }
let genericCss = null;
async function loadEasylist() {
  if (easylistDomains) return;
  const rules = await loadJson('rules/easylist.json').catch(() => []);
  easylistDomains = new Set(rules.filter((r) => r.action.type === 'block' && !r.condition.initiatorDomains)
    .flatMap((r) => r.condition.requestDomains || []));
}
async function loadEasyprivacy() {
  if (easyprivacyDomains) return;
  const rules = await loadJson('rules/easyprivacy.json').catch(() => []);
  easyprivacyDomains = new Set(rules.filter((r) => r.action.type === 'block').flatMap((r) => r.condition.requestDomains));
}
async function loadSiteonly() {
  if (siteonlySafe) return;
  const rules = await loadJson('rules/siteonly.json').catch(() => []);
  siteonlySafe = new Set(rules.filter((r) => r.action.type === 'allow' && !r.condition.initiatorDomains)
    .flatMap((r) => r.condition.requestDomains || []));
}
const inSet = (set, host) => {
  if (!set) return false;
  const labels = host.split('.');
  for (let i = 0; i < labels.length - 1; i++) if (set.has(labels.slice(i).join('.'))) return true;
  return false;
};
// Firefox webRequest types that "site only" stops (its declarativeNetRequest types, with beacon for ping)
const SITEONLY_TYPES = new Set(['script', 'sub_frame', 'xmlhttprequest', 'ping', 'beacon', 'websocket', 'object',
  'object_subrequest', 'other']);
function blockedByClean(d, page) {
  if ((!cleanBlocking && !adsOn) || d.error !== 'NS_ERROR_ABORT') return false;
  const host = hostOf(d.url) || '';
  const top = hostOf(d.documentUrl || d.originUrl || '') || '';
  const reg = registrableDomain(ctx.trie, host);
  if (registrableDomain(ctx.trie, top) === reg) return false;
  const match = lookup(ctx.list, host);
  const listed = cleanBlocking && match && isTracking(ctx.list, match.category) && (cleanBlocking !== 'verified' || match.verified);
  if (listed || (extendedOn() && inSet(easyprivacyDomains, host)) || (adsOn && inSet(easylistDomains, host))) return true;
  if (cleanBlocking && learnedSet.has(reg)) return true; // learned by Lens (step 4)
  // "site only": a script, frame or connection of another site, unless allowed (approximate: the page may
  // also have cancelled it itself)
  return cleanBlocking === 'siteonly' && SITEONLY_TYPES.has(d.type) && !page.firstParty.includes(reg)
    && !inSet(siteonlySafe, host) && !siteAllowed.some((a) => a.site === page.site && a.domain === reg);
}

// Clean mode (F14, opt-in): the browser's own rule sets (declarativeNetRequest), shipped disabled. Their state
// is read from the browser itself, never kept in storage, so deleting Lens's data cannot leave it inconsistent.
// Dynamic rules: ids PAUSE_BASE.. pause a site; ids ALLOW_BASE.. allow one domain on one site in "site only"
// mode (two rules each: its requests, and everything a frame of it loads). Priorities as in tools/rules.py.
const dnr = api.declarativeNetRequest;
const PAUSE_BASE = 1000;
const ALLOW_BASE = 100000;
const LEARN_RULE_ID = 900; // step 4: below the pause ids
const LAST_LEVEL_KEY = 'clean:lastLevel'; // a preference only: which list "Block trackers" turns on
const AUTOREJECT_KEY = 'autoreject:on'; // "Reject banners", off by default
const OUTCOMES = new Set(['rejected', 'payOrAccept', 'noReject']);
const P_ALLOW = 2;
const P_LIST = 3;
const P_PAUSE = 10;
const DOMAIN_RE = /^[a-z0-9.-]+\.[a-z0-9-]+$/;
const isPause = (r) => r.id >= PAUSE_BASE && r.id < ALLOW_BASE;
const isAllow = (r) => r.id >= ALLOW_BASE && r.action.type === 'allow';

// The panel refreshes every 1-3 s while a page loads: reuse the last state for a moment instead of asking the
// browser again each time; any change of a setting goes through cleanMessage / learnMessage, which refresh it.
let cleanCache = null;
async function cleanStateCached() {
  if (cleanCache && Date.now() - cleanCache.at < 5000) return cleanCache.state;
  return cleanState();
}

async function cleanState() {
  if (!dnr) return { available: false, blocking: null, params: false, ads: false, paused: [], allowed: [] };
  const enabled = await dnr.getEnabledRulesets();
  const dynamic = await dnr.getDynamicRules();
  cleanBlocking = enabled.includes('siteonly') ? 'siteonly' : enabled.includes('easyprivacy') ? 'extended'
    : enabled.includes('full') ? 'full' : enabled.includes('verified') ? 'verified' : null;
  if (extendedOn()) loadEasyprivacy();
  if (cleanBlocking === 'siteonly') loadSiteonly();
  adsOn = enabled.includes('easylist');
  if (adsOn) loadEasylist();
  siteAllowed = dynamic.filter(isAllow).map((r) => ({ site: r.condition.initiatorDomains[0], domain: r.condition.requestDomains[0] }))
    .sort((a, b) => (a.site + ' ' + a.domain < b.site + ' ' + b.domain ? -1 : 1));
  const state = {
    available: true,
    blocking: cleanBlocking,
    params: enabled.includes('params'),
    ads: adsOn,
    autoReject: Boolean((await api.storage.local.get(AUTOREJECT_KEY))[AUTOREJECT_KEY]),
    learnOn: learnStore.enabled,
    paused: pausedSites = dynamic.filter(isPause).map((r) => r.condition.requestDomains[0]).sort(),
    allowed: siteAllowed,
  };
  cleanCache = { at: Date.now(), state };
  return state;
}

/** Pauses saved before "site only" existed had priority 2: raise them above every list (once, on start). */
async function migratePauses() {
  if (!dnr) return;
  const old = (await dnr.getDynamicRules()).filter((r) => isPause(r) && r.priority !== P_PAUSE);
  if (!old.length) return;
  await dnr.updateDynamicRules({ removeRuleIds: old.map((r) => r.id), addRules: old.map((r) => ({ ...r, priority: P_PAUSE })) });
}

/**
 * Step 4, only while "Block trackers" and learning are on: learned domains that read a canvas are blocked (rule
 * LEARN_RULE_ID); those learned from cookies alone lose their cookies, both ways (rule LEARN_RULE_ID + 1). Same
 * priority as the lists: above the "site only" allows, below a pause. Calls are chained: two at once would both
 * try to add the same rule ids.
 */
let learnSync = Promise.resolve();
function syncLearnRule() {
  learnSync = learnSync.catch(() => {}).then(async () => {
    if (!dnr) return;
    const { block, strip } = learnStore.enabled && cleanBlocking ? learnedActions(learnStore) : { block: [], strip: [] };
    const ids = [LEARN_RULE_ID, LEARN_RULE_ID + 1];
    const existing = (await dnr.getDynamicRules()).filter((r) => ids.includes(r.id)).map((r) => r.id);
    const cond = (domains) => ({ requestDomains: domains, domainType: 'thirdParty', excludedResourceTypes: ['main_frame'] });
    const addRules = [];
    if (block.length) addRules.push({ id: ids[0], priority: P_LIST, action: { type: 'block' }, condition: cond(block) });
    if (strip.length) {
      addRules.push({ id: ids[1], priority: P_LIST, condition: cond(strip), action: { type: 'modifyHeaders',
        requestHeaders: [{ header: 'cookie', operation: 'remove' }],
        responseHeaders: [{ header: 'set-cookie', operation: 'remove' }] } });
    }
    if (!existing.length && !addRules.length) return;
    await dnr.updateDynamicRules({ removeRuleIds: existing, addRules });
  });
  return learnSync;
}

function saveLearn() {
  if (learnTimer) return;
  learnTimer = setTimeout(() => { learnTimer = null; api.storage.local.set({ [LEARN_KEY]: learnStore }); }, 2000);
}

function learnSignal(domain, site, kind) {
  if (noteSignal(learnStore, domain, site, kind, Date.now())) {
    learnedSet = new Set(learnedActions(learnStore).block);
    syncLearnRule().catch(() => {});
  }
  saveLearn();
}

async function learnMessage(msg) {
  if (msg.type === 'learn:settings' && typeof msg.enabled === 'boolean') {
    learnStore.enabled = msg.enabled;
  } else if (msg.type === 'learn:forget') {
    if (typeof msg.domain === 'string') delete learnStore.domains[msg.domain];
    else learnStore.domains = {};
  }
  if (msg.type !== 'learn:get') {
    learnedSet = new Set(learnedActions(learnStore).block);
    await api.storage.local.set({ [LEARN_KEY]: learnStore });
    await cleanState().catch(() => null);
    await syncLearnRule().catch(() => null);
  }
  return learnView(learnStore);
}

async function cleanMessage(msg) {
  if (!dnr) return cleanState();
  if (msg.type === 'clean:set') {
    const enable = [];
    const disable = [];
    if (msg.blocking !== undefined) {
      const LEVELS = { verified: ['verified'], full: ['full'], extended: ['full', 'easyprivacy'],
        siteonly: ['full', 'easyprivacy', 'siteonly'] };
      let level = msg.blocking;
      // the panel's "Block trackers" button: the level last chosen in the settings, or "extended"
      if (level === 'last') {
        const kept = (await api.storage.local.get(LAST_LEVEL_KEY))[LAST_LEVEL_KEY];
        level = Object.prototype.hasOwnProperty.call(LEVELS, kept) ? kept : 'extended';
      } else if (Object.prototype.hasOwnProperty.call(LEVELS, level)) {
        await api.storage.local.set({ [LAST_LEVEL_KEY]: level });
      }
      // "extended" = our full list plus the domain rules of EasyPrivacy; "siteonly" = extended plus the
      // "site only" rules (third-party scripts, frames and connections)
      const want = (Object.prototype.hasOwnProperty.call(LEVELS, level) && LEVELS[level]) || [];
      enable.push(...want);
      for (const id of ['verified', 'full', 'easyprivacy', 'siteonly']) if (!want.includes(id)) disable.push(id);
    }
    if (typeof msg.params === 'boolean') (msg.params ? enable : disable).push('params');
    if (typeof msg.ads === 'boolean') (msg.ads ? enable : disable).push('easylist');
    await dnr.updateEnabledRulesets({ enableRulesetIds: enable, disableRulesetIds: disable });
    if (msg.blocking !== undefined) { await cleanState(); await syncLearnRule(); }
  } else if (msg.type === 'clean:pause' && typeof msg.site === 'string' && DOMAIN_RE.test(msg.site)) {
    // pausing a site: everything that page loads is allowed, as if clean mode were off there
    const rules = await dnr.getDynamicRules();
    const existing = rules.find((r) => isPause(r) && r.condition.requestDomains[0] === msg.site);
    if (msg.paused && !existing) {
      const id = Math.max(PAUSE_BASE - 1, ...rules.filter(isPause).map((r) => r.id)) + 1;
      await dnr.updateDynamicRules({ addRules: [{ id, priority: P_PAUSE, action: { type: 'allowAllRequests' },
        condition: { requestDomains: [msg.site], resourceTypes: ['main_frame', 'sub_frame'] } }] });
    } else if (!msg.paused && existing) {
      await dnr.updateDynamicRules({ removeRuleIds: [existing.id] });
    }
  } else if (msg.type === 'clean:allow' && typeof msg.site === 'string' && DOMAIN_RE.test(msg.site)
      && typeof msg.domain === 'string' && DOMAIN_RE.test(msg.domain) && msg.site !== msg.domain) {
    // "site only": let one other domain work on one site (the tracker lists still apply to it)
    const rules = await dnr.getDynamicRules();
    const mine = rules.filter((r) => r.id >= ALLOW_BASE && r.condition.initiatorDomains?.[0] === msg.site
      && r.condition.requestDomains?.[0] === msg.domain);
    if (msg.allowed && !mine.length) {
      const id = Math.max(ALLOW_BASE - 1, ...rules.filter((r) => r.id >= ALLOW_BASE).map((r) => r.id)) + 1;
      const cond = { initiatorDomains: [msg.site], requestDomains: [msg.domain] };
      await dnr.updateDynamicRules({ addRules: [
        { id, priority: P_ALLOW, action: { type: 'allow' }, condition: cond },
        { id: id + 1, priority: P_ALLOW, action: { type: 'allowAllRequests' }, condition: { ...cond, resourceTypes: ['sub_frame'] } },
      ] });
    } else if (!msg.allowed && mine.length) {
      await dnr.updateDynamicRules({ removeRuleIds: mine.map((r) => r.id) });
    }
  }
  return cleanState();
}

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
    learnStore = normalizeLearn(null);
    learnedSet = new Set();
    await syncLearnRule().catch(() => null);
    cleanCache = null;
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

/**
 * When the results page was not seen in this tab (a result opened in a new tab, a search made before Lens loaded),
 * the address the browser itself sent to the site (Referer) still says which search engine the visitor came from.
 */
function referrerArrival(page) {
  const from = page.told && page.told.self ? page.told.self.cameFrom : null;
  if (!from) return null;
  const engine = engineForHost(engines, from) || engineForHost(engines, 'www.' + from);
  return engine ? { engine: engine.id, engineName: engine.name, fromSite: from, redirect: false, ping: false, referrer: true } : null;
}

/** Everything the panel shows for one tab. */
async function report(tabId, url) {
  await ready;
  const page = own(String(tabId));
  if (!page) return { page: null, index, clean: await cleanState().catch(() => null) };
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
  // the first seconds of a page: the count is still settling (news pages never stop loading ads, so after
  // COUNTING_MS the number is shown as it is, with a note that it can still grow)
  const { loading, counting } = isCounting(page);
  return {
    page: summary, told: page.told, baseline, index, categories: glossary.categories, loading, counting,
    consent: { banners: page.consent.banners, click: page.consent.click, previous: page.consent.previous ?? null,
      payOrAccept: Boolean(page.consent.payOrAccept), auto: page.consent.auto ?? null,
      toolsContacted: consentTools },
    search, arrival: page.arrival ? { ...page.arrival, engineName: name(page.arrival.engine) } : referrerArrival(page),
    journey: journeyView(page, summary),
    clean: await cleanStateCached().then((c) => {
      const out = { ...c, pausedHere: c.paused.includes(page.site) };
      if (c.blocking === 'siteonly') {
        // what "site only" stopped here, and what was allowed on this site from the panel
        const here = c.allowed.filter((a) => a.site === page.site).map((a) => a.domain);
        // ads stopped by EasyList are not "site only" blocks (allowing them would not help)
        out.siteonly = { blocked: blockedDomains(page).filter((b) => !here.includes(b.domain) && !(adsOn && inSet(easylistDomains, b.domain))
          && !learnedSet.has(b.domain)),
          allowedHere: here };
      }
      if (learnStore.enabled) {
        const { block, strip } = learnedActions(learnStore);
        const action = (d) => (block.includes(d) ? 'block' : strip.includes(d) ? 'strip' : null);
        out.learn = { enabled: true, here: Object.keys(page.domains).filter((d) => action(d)).sort()
          .map((d) => ({ domain: d, action: action(d), stopped: page.domains[d].stopped > 0, contacted: page.domains[d].requests > 0 })) };
      }
      return out;
    }).catch(() => null),
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
  // "Reject banners": may this frame answer the banner (setting on, blocking not paused on the tab's site)?
  if (msg && msg.type === 'autoreject:check' && fromPage) {
    whenReady(async () => {
      const on = Boolean((await api.storage.local.get(AUTOREJECT_KEY))[AUTOREJECT_KEY]);
      const page = own(String(sender.tab.id));
      const site = page ? page.site : registrableDomain(ctx.trie, hostOf(sender.url || '') || '');
      sendResponse(on && !pausedSites.includes(site));
    });
    return true;
  }
  if (msg && msg.type === 'autoreject:result' && fromPage) {
    whenReady(() => {
      if (!OUTCOMES.has(msg.outcome)) return;
      const tool = consentNames.has(msg.tool) ? msg.tool : null;
      const page = own(String(sender.tab.id));
      if (page) { noteAutoReject(page, { tool, outcome: msg.outcome }); touch(sender.tab.id); }
      // the notice is drawn by the page's top frame, also when the banner was in a frame
      api.tabs.sendMessage(sender.tab.id, { type: 'lens:notice', tool, outcome: msg.outcome }, { frameId: 0 }).catch(() => {});
    });
    return false;
  }
  if (msg && msg.type === 'autoreject:set' && !fromPage && typeof msg.enabled === 'boolean') {
    api.storage.local.set({ [AUTOREJECT_KEY]: msg.enabled }).then(() => { cleanCache = null; sendResponse(true); });
    return true;
  }
  if (TEST_HOOKS && msg && msg.type === 'test:autoreject') {
    api.storage.local.set({ [AUTOREJECT_KEY]: msg.enabled === true }).then(() => { cleanCache = null; sendResponse(true); });
    return true;
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
        // step 4: a script of a third party on no list read a canvas (fingerprinting)
        if (msg.kind === 'canvas_read' && /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(msg.host) && !lookup(ctx.list, msg.host)) {
          const reg = registrableDomain(ctx.trie, msg.host);
          if (reg && !page.firstParty.includes(reg)) learnSignal(reg, page.site, 'canvas');
        }
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
  if (msg && typeof msg.type === 'string' && msg.type.startsWith('learn:') && !fromPage) {
    ready.then(() => learnMessage(msg)).then(sendResponse);
    return true;
  }
  if (TEST_HOOKS && msg && msg.type === 'test:learn-on') {
    ready.then(() => learnMessage({ type: 'learn:settings', enabled: true })).then(sendResponse);
    return true;
  }
  if (msg && typeof msg.type === 'string' && msg.type.startsWith('clean:') && !fromPage) {
    (msg.type === 'clean:get' ? cleanState() : cleanMessage(msg)).then(sendResponse);
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
  if (TEST_HOOKS && msg && msg.type === 'test:clean') {
    cleanMessage({ type: 'clean:set', blocking: msg.blocking, params: msg.params, ads: msg.ads }).then(sendResponse);
    return true;
  }
  if (TEST_HOOKS && msg && msg.type === 'test:allow') {
    cleanMessage({ type: 'clean:allow', site: msg.site, domain: msg.domain, allowed: true }).then(sendResponse);
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
      out._learn = learnView(learnStore);
      sendResponse(out);
    });
    return true;
  }
  return false;
});
