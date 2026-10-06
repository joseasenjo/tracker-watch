// F12 "What your browser told this site": what the browser sends by default with every request.
// Kept: for the page itself, the self-description any server receives (user agent, language, client hints,
// privacy signals, the site you came from as a host); for third parties, only counts and names
// (how many requests carried your cookies or the page address, which tracking parameters appeared).
// Never kept: cookie values, parameter values, full addresses. The IP address is seen by every server but
// not by the browser, so it is explained, not shown.

import { registrableDomain } from './psl.js';

/** Query parameters documented by ad and analytics vendors as click or campaign identifiers. */
export const TRACKING_PARAMS = new Set([
  'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'utm_id',
  'gclid', 'gbraid', 'wbraid', 'dclid', 'gclsrc', '_gl', 'fbclid', 'msclkid', 'ttclid', 'twclid',
  'li_fat_id', 'mc_eid', 'yclid', 'igshid', 'epik', 'srsltid',
]);

/**
 * Names of known tracking parameters in a URL (values are dropped).
 * @param {string} url
 * @returns {string[]}
 */
export function trackingParams(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return [];
  }
  const names = new Set();
  for (const name of parsed.searchParams.keys()) {
    const n = name.toLowerCase();
    if (TRACKING_PARAMS.has(n)) names.add(n);
  }
  return [...names].sort();
}

/**
 * @typedef {{ userAgent: string | null, language: string | null, hints: Record<string, string>,
 *   gpc: boolean, dnt: boolean, cameFrom: string | null }} SelfDescription
 * @typedef {{ self: SelfDescription | null, thirdWithCookies: number, thirdWithReferer: number,
 *   cookieServices: Record<string, number>, params: Record<string, number> }} Told
 */

/** @returns {Told} */
export function emptyTold() {
  return { self: null, thirdWithCookies: 0, thirdWithReferer: 0, cookieServices: {}, params: {} };
}

const MAX_VALUE = 300;
const clip = (v) => (v === undefined || v === null ? null : String(v).slice(0, MAX_VALUE));

/**
 * @param {Array<{ name: string, value?: string }>} list
 * @returns {Record<string, string>}
 */
function lower(list) {
  const out = {};
  for (const h of list || []) {
    const name = String(h.name).toLowerCase();
    if (!Object.prototype.hasOwnProperty.call(out, name)) {
      Object.defineProperty(out, name, { value: String(h.value ?? ''), enumerable: true, writable: true, configurable: true });
    }
  }
  return out;
}

/**
 * The page's own document request: what the browser said about itself.
 * @param {Array<{ name: string, value?: string }>} headers
 * @param {import('./psl.js').SuffixNode} trie
 * @returns {SelfDescription}
 */
export function describeSelf(headers, trie) {
  const h = lower(headers);
  const hints = {};
  for (const name of ['sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform']) {
    if (Object.prototype.hasOwnProperty.call(h, name)) hints[name] = clip(h[name]);
  }
  let cameFrom = null;
  if (h.referer) {
    try { cameFrom = registrableDomain(trie, new URL(h.referer).hostname) || null; } catch { /* not a URL */ }
  }
  return {
    userAgent: clip(h['user-agent']), language: clip(h['accept-language']), hints,
    gpc: h['sec-gpc'] === '1', dnt: h.dnt === '1', cameFrom,
  };
}

/**
 * A third-party request's headers: did it carry cookies (how many names) and the page address?
 * @param {Told} told
 * @param {Array<{ name: string, value?: string }>} headers
 * @param {string | null} service  tracking-list service of the request, if any
 */
export function countThirdParty(told, headers, service) {
  const h = lower(headers);
  if (h.cookie) {
    told.thirdWithCookies += 1;
    if (service) {
      const names = h.cookie.split(';').map((c) => c.split('=')[0].trim()).filter(Boolean).length;
      const prev = Object.prototype.hasOwnProperty.call(told.cookieServices, service) ? told.cookieServices[service] : 0;
      Object.defineProperty(told.cookieServices, service, { value: Math.max(prev, names), enumerable: true, writable: true, configurable: true });
    }
  }
  if (h.referer) told.thirdWithReferer += 1;
}

/**
 * @param {Told} told
 * @param {string[]} names
 */
export function countParams(told, names) {
  for (const n of names) {
    const prev = Object.prototype.hasOwnProperty.call(told.params, n) ? told.params[n] : 0;
    Object.defineProperty(told.params, n, { value: prev + 1, enumerable: true, writable: true, configurable: true });
  }
}
