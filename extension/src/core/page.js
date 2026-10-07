// The live report of one tab: a reducer over webRequest events.
//
// The state is plain JSON (no Map, Set or class) so the background can keep it in storage.session and
// rebuild it after the browser puts the service worker / event page to sleep (spec §12). Only hosts,
// registrable domains and counts are kept: never paths, query strings, header values or cookie values.
//
// Counting follows the engine (traceguard.report.passive_requests): a request counts once it actually
// goes out, so requests blocked by an extension or by the browser are not counted as contacted; they are
// listed apart. "Goes out" = headers sent (onSendHeaders) or a response arrived (onCompleted, which also
// covers answers from the cache). A request that fails before that is "stopped", with the reason.

import { registrableDomain } from './psl.js';
import { firstPartySet, isTracking, lookup } from './classify.js';
import { errorReason, hostOf, KINDS, kindOf } from './requests.js';
import { countParams, countThirdParty, describeSelf, emptyTold, trackingParams } from './headers.js';

/** Caps per tab, as in the scanner: a hostile or endless page cannot grow the state without limit. */
export const LIMITS = { events: 5000, pending: 2000, services: 400, domains: 1000, recent: 300 };
/** How far back an interaction can re-window requests (the click message may arrive after its requests). */
export const RECENT_MS = 5000;

/**
 * @typedef {{ trie: import('./psl.js').SuffixNode, list: import('./classify.js').TrackerList }} Ctx
 * @typedef {'before' | 'after'} Window
 * @typedef {Record<typeof KINDS[number], number>} KindCounts
 * @typedef {{ client: number, browser: number, cancelled: number, failed: number }} Stopped
 * @typedef {{ entity: string, category: string, tracking: boolean, verified: boolean, window: Window | null,
 *             before: KindCounts, after: KindCounts, stopped: Stopped, bytes: number }} ServiceState
 * @typedef {{ window: Window | null, requests: number, before: number, stopped: number }} DomainState
 * @typedef {{ k: string, w: Window, t: 0 | 1, s: string | null, d: string | null, c: 0 | 1, n: number }} Pending
 * @typedef {{
 *   v: 2, host: string, site: string, firstParty: string[], mainRequestId: string | null,
 *   startedAt: number, interactionAt: number | null, interaction: string | null,
 *   events: number, truncated: boolean,
 *   totals: { requests: number, third: number, cached: number },
 *   stopped: Stopped,
 *   bytes: { sum: number, exact: number, approx: number, unknown: number },
 *   services: Record<string, ServiceState>, domains: Record<string, DomainState>,
 *   pending: Record<string, Pending>, recent: Pending[], told: import('./headers.js').Told,
 *   consent: { banners: string[], click: { tool: string | null, choice: string } | null,
 *              previous?: { tool: string | null, choice: string } | null },
 *   search: { engine: string, params: Array<{ name: string, isQuery: boolean }>,
 *             links: { total: number, ping: number, redirect: number, mousedown: number }, pings: number,
 *             lastPingAt: number | null } | null,
 *   arrival: Arrival | null, engineRedirect: string | null
 * }} PageState
 * @typedef {{ engine: string | null, fromSite: string | null, redirect: boolean, ping: boolean }} Arrival
 */

// Keys come from the network (host names, request ids): never read inherited properties and never assign
// with obj[key] = ..., or a host called "__proto__" or "constructor" could reach Object.prototype.
const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
const own = (obj, key) => (has(obj, key) ? obj[key] : undefined);
const put = (obj, key, value) => Object.defineProperty(obj, key, { value, enumerable: true, writable: true, configurable: true });

const kinds = () => /** @type {KindCounts} */ (Object.fromEntries(KINDS.map((k) => [k, 0])));
const stopped = () => ({ client: 0, browser: 0, cancelled: 0, failed: 0 });

/**
 * A new page in a tab, from its top-level navigation request.
 * @param {{ url: string, now: number, requestId?: string | null, declared?: string[], arrival?: Arrival | null }} nav
 *   declared: first_party_domains of the site when it is in the weekly snapshot
 * @param {Ctx} ctx
 * @returns {PageState}
 */
export function startPage({ url, now, requestId = null, declared = [], arrival = null }, ctx) {
  const host = hostOf(url) ?? '';
  return {
    v: 2, host, site: registrableDomain(ctx.trie, host),
    firstParty: [...firstPartySet(ctx.trie, host, declared)].sort(),
    mainRequestId: requestId, startedAt: now, interactionAt: null, interaction: null,
    events: 0, truncated: false,
    totals: { requests: 0, third: 0, cached: 0 },
    stopped: stopped(),
    bytes: { sum: 0, exact: 0, approx: 0, unknown: 0 },
    services: {}, domains: {}, pending: {}, recent: [], told: emptyTold(),
    consent: { banners: [], click: null }, search: null, arrival, engineRedirect: null,
  };
}

/**
 * The top-level navigation was redirected (same request id, new URL): the final site is first party too,
 * as in the engine (the target's domain and the final domain both count as first party).
 * @param {PageState} page
 * @param {string} url
 * @param {Ctx} ctx
 */
export function redirectPage(page, url, ctx) {
  const host = hostOf(url);
  if (!host) return page;
  page.host = host;
  page.site = registrableDomain(ctx.trie, host);
  if (page.site && !page.firstParty.includes(page.site)) {
    page.firstParty.push(page.site);
    page.firstParty.sort();
  }
  return page;
}

/**
 * The first real interaction of the user (trusted click or key) or the "mark now" button.
 * Requests issued from then on belong to the "after" window.
 * @param {PageState} page
 * @param {{ now: number, kind: string, on?: { tool: string | null, choice: string } | null }} ev
 *   on: what the click landed on, when it was a consent banner (already validated by the caller)
 */
export function markInteraction(page, { now, kind, on = null }) {
  if (page.interactionAt !== null) return page;
  page.interactionAt = now;
  page.interaction = kind;
  if (on) page.consent.click = on;
  // Requests issued at or after the interaction but processed before its message arrived move to "after".
  for (const p of Object.values(page.pending)) if (p.n >= now) p.w = 'after';
  for (const p of page.recent) {
    if (p.n < now || p.w === 'after') continue;
    p.w = 'after';
    if (p.s && has(page.services, p.s)) {
      const svc = page.services[p.s];
      svc.before[p.k] -= 1;
      svc.after[p.k] += 1;
      svc.window = KINDS.some((k) => svc.before[k] > 0) ? 'before' : 'after';
    }
    if (p.d && has(page.domains, p.d)) {
      const dom = page.domains[p.d];
      dom.before -= 1;
      dom.window = dom.before > 0 ? 'before' : 'after';
    }
  }
  page.recent = [];
  return page;
}

/**
 * onBeforeRequest. A second event with the same id is the same request after a redirect: the hop that
 * already went out stays counted and the new URL is tracked from scratch.
 * @param {PageState} page
 * @param {{ requestId: string, url: string, type: string, now: number }} ev
 * @param {Ctx} ctx
 */
const MAX_FRAMES = 300;

/**
 * Where a request was made: '' for the page itself, or the site of the outermost third-party frame that
 * contains it (an embedded video, an ad slot). Scripts running in the page itself, including those loaded by
 * other scripts, count as the page: webRequest gives the frame, not the script.
 * @param {PageState} page  @param {number | undefined} frameId
 */
function viaFrame(page, frameId) {
  const frames = page.frames || {};
  let id = frameId;
  let outer = '';
  for (let i = 0; i < 12 && id !== undefined && id !== null && id > 0; i++) {
    const f = own(frames, String(id));
    if (!f) break;
    if (f.s && !page.firstParty.includes(f.s)) outer = f.s;
    id = f.p;
  }
  return outer;
}

/**
 * @param {PageState} page
 * @param {{ requestId: string, url: string, type: string, now: number, frameId?: number,
 *   parentFrameId?: number }} ev  frameId/parentFrameId as given by webRequest (0 = the page's own frame)
 * @param {Ctx} ctx
 */
export function onRequest(page, { requestId, url, type, now, frameId, parentFrameId }, ctx) {
  const host = hostOf(url);
  if (!host) return page;
  if (page.events >= LIMITS.events) {
    page.truncated = true;
    return page;
  }
  page.events += 1;
  page.lastAt = now;
  if (has(page.pending, requestId)) delete page.pending[requestId];
  if (Object.keys(page.pending).length >= LIMITS.pending) {
    page.truncated = true;
    return page;
  }
  const reg = registrableDomain(ctx.trie, host);
  const third = page.firstParty.includes(reg) ? 0 : 1;
  // a new frame: remember its parent and site; the frame's own document request belongs to its parent
  let via = '';
  if (type === 'sub_frame' && Number.isInteger(frameId) && frameId > 0) {
    const frames = (page.frames ??= {});
    if (has(frames, String(frameId)) || Object.keys(frames).length < MAX_FRAMES) {
      put(frames, String(frameId), { p: Number.isInteger(parentFrameId) ? parentFrameId : 0, s: reg });
    }
    via = viaFrame(page, parentFrameId);
  } else {
    via = viaFrame(page, frameId);
  }
  /** @type {Window} */
  const w = page.interactionAt !== null && now >= page.interactionAt ? 'after' : 'before';
  let service = null;
  let domain = null;
  if (third) {
    const match = lookup(ctx.list, host);
    if (match && ensureService(page, match, ctx)) service = match.service;
    if (ensureDomain(page, reg)) domain = reg;
  }
  put(page.pending, requestId, { k: kindOf(type), w, t: third, s: service, d: domain, c: 0, n: now, ...(via ? { f: via } : {}) });
  if (third || requestId === page.mainRequestId) countParams(page.told, trackingParams(url));
  return page;
}

/**
 * onSendHeaders: the request went out. With its headers (F12): the page's own request tells what the
 * browser says about itself; third-party ones are only counted (cookies carried, page address carried).
 * @param {PageState} page
 * @param {{ requestId: string, headers?: Array<{ name: string, value?: string }> }} ev
 * @param {Ctx} [ctx]
 */
export function onSent(page, { requestId, headers }, ctx) {
  const p = own(page.pending, requestId);
  if (!p || p.c) return page;
  if (headers && ctx) {
    if (requestId === page.mainRequestId) {
      page.told.self = describeSelf(headers, ctx.trie);
      // "came from" only tells something when it is another site
      if (page.told.self.cameFrom && page.firstParty.includes(page.told.self.cameFrom)) page.told.self.cameFrom = null;
    }
    else if (p.t) countThirdParty(page.told, headers, p.s);
  }
  count(page, p, false);
  return page;
}

/**
 * onCompleted. size: bytes when known; sizeExact: true when it is the transfer size (Firefox responseSize),
 * false when it comes from Content-Length (Chromium; body only, a minimum).
 * @param {PageState} page
 * @param {{ requestId: string, fromCache?: boolean, size?: number | null, sizeExact?: boolean }} ev
 */
export function onCompleted(page, { requestId, fromCache = false, size = null, sizeExact = false }) {
  const p = own(page.pending, requestId);
  if (!p) return page;
  if (!p.c) count(page, p, fromCache);
  if (p.t && !fromCache) {
    if (typeof size === 'number' && Number.isFinite(size) && size >= 0) {
      page.bytes.sum += size;
      page.bytes[sizeExact ? 'exact' : 'approx'] += 1;
      if (p.s) page.services[p.s].bytes += size;
    } else {
      page.bytes.unknown += 1;
    }
  }
  delete page.pending[requestId];
  return page;
}

/**
 * onErrorOccurred. Failing after going out still counts as contacted; failing before is "stopped".
 * @param {PageState} page
 * @param {{ requestId: string, error: string }} ev
 */
export function onError(page, { requestId, error }) {
  const p = own(page.pending, requestId);
  if (!p) return page;
  if (!p.c) {
    const reason = errorReason(error);
    if (p.t) {
      page.stopped[reason] += 1;
      if (p.s) page.services[p.s].stopped[reason] += 1;
      if (p.d) {
        const dom = page.domains[p.d];
        dom.stopped += 1;
        // blocked by a blocker, and not as a tracking service of the list: what "site only" mode stops
        if (reason === 'client' && !(p.s && page.services[p.s].tracking)) {
          dom.blocked = (dom.blocked || 0) + 1;
          if (p.f && !dom.bvia) dom.bvia = p.f;
        }
      }
    }
  }
  delete page.pending[requestId];
  return page;
}

/**
 * Third-party domains whose requests a blocker stopped other than as tracking services of the list (with
 * "site only" mode on: scripts, frames and connections of other sites), most requests first.
 * @param {PageState} page
 * @returns {Array<{ domain: string, requests: number, via: string | null }>}
 */
export function blockedDomains(page) {
  return Object.entries(page.domains).filter(([, d]) => d.blocked > 0)
    .map(([domain, d]) => ({ domain, requests: d.blocked, via: d.bvia || null }))
    .sort((a, b) => b.requests - a.requests || (a.domain < b.domain ? -1 : 1));
}

/**
 * @param {PageState} page
 * @param {Pending} p
 * @param {boolean} cached
 */
function count(page, p, cached) {
  p.c = 1;
  if (page.interactionAt === null && p.t) {
    page.recent.push(p);
    while (page.recent.length > LIMITS.recent || (page.recent.length && page.recent[0].n < p.n - RECENT_MS)) page.recent.shift();
  }
  page.totals.requests += 1;
  if (cached) page.totals.cached += 1;
  if (!p.t) return;
  page.totals.third += 1;
  if (p.s) {
    const svc = page.services[p.s];
    svc[p.w][p.k] += 1;
    if (p.f) {
      svc.via ??= {};
      put(svc.via, p.f, (own(svc.via, p.f) || 0) + 1);
    }
    if (svc.window === null || (svc.window === 'after' && p.w === 'before')) svc.window = p.w;
  }
  if (p.d) {
    const dom = page.domains[p.d];
    dom.requests += 1;
    if (p.w === 'before') dom.before += 1;
    if (dom.window === null || (dom.window === 'after' && p.w === 'before')) dom.window = p.w;
  }
}

/**
 * @param {PageState} page
 * @param {import('./classify.js').Match} match
 * @param {Ctx} ctx
 */
function ensureService(page, match, ctx) {
  if (has(page.services, match.service)) return true;
  if (Object.keys(page.services).length >= LIMITS.services) {
    page.truncated = true;
    return false;
  }
  put(page.services, match.service, {
    entity: match.entity, category: match.category, tracking: isTracking(ctx.list, match.category),
    verified: match.verified, window: null, before: kinds(), after: kinds(), stopped: stopped(), bytes: 0,
  });
  return true;
}

/**
 * @param {PageState} page
 * @param {string} reg
 */
function ensureDomain(page, reg) {
  if (has(page.domains, reg)) return true;
  if (Object.keys(page.domains).length >= LIMITS.domains) {
    page.truncated = true;
    return false;
  }
  put(page.domains, reg, { window: null, requests: 0, before: 0, stopped: 0 });
  return true;
}

export const BEHAVIOUR_KINDS = ['canvas_read', 'geolocation_request', 'webrtc_connection'];

/**
 * A script behaviour noticed in the page (an indication: the page could fake it). Attributed like the engine:
 * to the listed third-party service of the calling script's host; other third-party hosts are kept apart by
 * registrable domain (at most 20), first-party scripts and unknown callers are ignored.
 * @param {PageState} page
 * @param {{ kind: string, host: string }} ev
 * @param {{ trie: import('./psl.js').SuffixNode, list: import('./classify.js').TrackerList }} ctx
 */
export function noteBehaviour(page, { kind, host }, ctx) {
  if (!BEHAVIOUR_KINDS.includes(kind) || !/^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(host)) return page;
  const reg = registrableDomain(ctx.trie, host);
  if (!reg || page.firstParty.includes(reg)) return page;
  const add = (map, key) => {
    const list = Object.prototype.hasOwnProperty.call(map, key) ? map[key] : [];
    if (!list.includes(kind)) Object.defineProperty(map, key, { value: [...list, kind].sort(), enumerable: true, writable: true, configurable: true });
  };
  const match = lookup(ctx.list, host);
  if (match) add(page.behaviours ??= {}, match.service);
  else if (Object.keys(page.behavioursOther ??= {}).length < 20 || Object.prototype.hasOwnProperty.call(page.behavioursOther, reg)) {
    add(page.behavioursOther, reg);
  }
  return page;
}

/**
 * A consent banner of a known tool was on screen (labelled only; the extension never clicks anything).
 * @param {PageState} page
 * @param {string} tool  a name from the consent_tools table (validated by the caller)
 */
export function noteBanner(page, tool, payOrAccept = false) {
  if (!page.consent.banners.includes(tool) && page.consent.banners.length < 10) page.consent.banners.push(tool);
  if (payOrAccept === true) page.consent.payOrAccept = true; // the only refusal offered is a subscription
  return page;
}

/**
 * What the results page of a search engine looks like from the browser (F13): parameter names of the
 * search address and how its result links record a click.
 * @param {PageState} page
 * @param {{ engine: string, params: Array<{ name: string, isQuery: boolean }>,
 *           links: { total: number, ping: number, redirect: number, mousedown: number } }} serp
 */
export function noteSerp(page, { engine, params, links }) {
  const n = (v) => (Number.isInteger(v) && v >= 0 ? Math.min(v, 10000) : 0);
  page.search = {
    engine, params: params.slice(0, 40), pings: page.search ? page.search.pings : 0,
    lastPingAt: page.search ? page.search.lastPingAt : null,
    links: { total: n(links.total), ping: n(links.ping), redirect: n(links.redirect), mousedown: n(links.mousedown) },
  };
  return page;
}

/**
 * A "ping" request (hyperlink auditing) sent by the results page when a result was clicked.
 * @param {PageState} page
 * @param {number} now
 */
export function notePing(page, now) {
  if (page.search) {
    page.search.pings += 1;
    page.search.lastPingAt = now;
  }
  return page;
}
