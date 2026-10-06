// Normalising what webRequest reports, which differs between Chromium and Firefox (M0 spike, spec §12).

/** Plain-language request kinds, the same as traceguard.behaviour.KINDS. */
export const KINDS = /** @type {const} */ (['script', 'image', 'background', 'frame', 'other']);

/**
 * webRequest resource type -> kind. Mirrors traceguard.behaviour.KIND_OF, which uses Playwright's names
 * (xhr/fetch/ping/websocket/eventsource -> background, document -> frame). Chromium reports
 * navigator.sendBeacon as "ping" and Firefox as "beacon"; both are background requests.
 * @type {Record<string, typeof KINDS[number]>}
 */
const KIND_OF = {
  script: 'script',
  image: 'image', imageset: 'image',
  xmlhttprequest: 'background', ping: 'background', beacon: 'background', websocket: 'background',
  main_frame: 'frame', sub_frame: 'frame',
};

/** @param {string} type */
export function kindOf(type) {
  return KIND_OF[type] ?? 'other';
}

/**
 * Why a request failed before it went out.
 * - "client": blocked by an extension (Chromium says so explicitly).
 * - "browser": blocked by the browser's own tracking protection (Firefox names the list).
 * - "cancelled": Firefox reports a block by another extension with the same generic code as an ordinary
 *   cancellation, so it can only be called "cancelled, possibly by a blocker".
 * - "failed": anything else (network error, DNS, TLS...).
 * @param {string} error
 * @returns {'client' | 'browser' | 'cancelled' | 'failed'}
 */
export function errorReason(error) {
  const e = String(error || '');
  if (e === 'net::ERR_BLOCKED_BY_CLIENT') return 'client';
  if (/^NS_ERROR_(TRACKING|FINGERPRINTING|CRYPTOMINING|SOCIALTRACKING|EMAILTRACKING)_URI$/.test(e)) return 'browser';
  if (e === 'NS_ERROR_ABORT' || e === 'net::ERR_ABORTED') return 'cancelled';
  return 'failed';
}

/**
 * Lower-case host of an http(s) or ws(s) URL, or null for anything else (data:, blob:, extension pages...).
 * Only the host is kept: paths and query strings never enter the state.
 * @param {string} url
 * @returns {string | null}
 */
export function hostOf(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (!['http:', 'https:', 'ws:', 'wss:'].includes(parsed.protocol) || !parsed.hostname) return null;
  return parsed.hostname.toLowerCase();
}
