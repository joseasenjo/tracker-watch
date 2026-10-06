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
 *   v: 1, host: string, site: string, firstParty: string[], mainRequestId: string | null,
 *   startedAt: number, interactionAt: number | null, interaction: string | null,
 *   events: number, truncated: boolean,
 *   totals: { requests: number, third: number, cached: number },
 *   stopped: Stopped,
 *   bytes: { sum: number, exact: number, approx: number, unknown: number },
 *   services: Record<string, ServiceState>, domains: Record<string, DomainState>,
 *   pending: Record<string, Pending>, recent: Pending[]
 * }} PageState
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
 * @param {{ url: string, now: number, requestId?: string | null, declared?: string[] }} nav
 *   declared: first_party_domains of the site when it is in the weekly snapshot
 * @param {Ctx} ctx
 * @returns {PageState}
 */
export function startPage({ url, now, requestId = null, declared = [] }, ctx) {
  const host = hostOf(url) ?? '';
  return {
    v: 1, host, site: registrableDomain(ctx.trie, host),
    firstParty: [...firstPartySet(ctx.trie, host, declared)].sort(),
    mainRequestId: requestId, startedAt: now, interactionAt: null, interaction: null,
    events: 0, truncated: false,
    totals: { requests: 0, third: 0, cached: 0 },
    stopped: stopped(),
    bytes: { sum: 0, exact: 0, approx: 0, unknown: 0 },
    services: {}, domains: {}, pending: {}, recent: [],
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
 * @param {{ now: number, kind: string }} ev
 */
export function markInteraction(page, { now, kind }) {
  if (page.interactionAt !== null) return page;
  page.interactionAt = now;
  page.interaction = kind;
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
export function onRequest(page, { requestId, url, type, now }, ctx) {
  const host = hostOf(url);
  if (!host) return page;
  if (page.events >= LIMITS.events) {
    page.truncated = true;
    return page;
  }
  page.events += 1;
  if (has(page.pending, requestId)) delete page.pending[requestId];
  if (Object.keys(page.pending).length >= LIMITS.pending) {
    page.truncated = true;
    return page;
  }
  const reg = registrableDomain(ctx.trie, host);
  const third = page.firstParty.includes(reg) ? 0 : 1;
  /** @type {Window} */
  const w = page.interactionAt !== null && now >= page.interactionAt ? 'after' : 'before';
  let service = null;
  let domain = null;
  if (third) {
    const match = lookup(ctx.list, host);
    if (match && ensureService(page, match, ctx)) service = match.service;
    if (ensureDomain(page, reg)) domain = reg;
  }
  put(page.pending, requestId, { k: kindOf(type), w, t: third, s: service, d: domain, c: 0, n: now });
  return page;
}

/**
 * onSendHeaders: the request went out.
 * @param {PageState} page
 * @param {{ requestId: string }} ev
 */
export function onSent(page, { requestId }) {
  const p = own(page.pending, requestId);
  if (p && !p.c) count(page, p, false);
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
      if (p.d) page.domains[p.d].stopped += 1;
    }
  }
  delete page.pending[requestId];
  return page;
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
