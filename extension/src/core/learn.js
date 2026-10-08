// Blocker step 4 (opt-in): learn trackers that are on no list from what they do, in the spirit of Privacy Badger
// (no code of it is used). A third party "behaves like a tracker" on a site when it receives a cookie that looks
// like an identifier, or when one of its scripts reads a canvas (a fingerprinting technique). Seen doing so on
// THRESHOLD different sites, it is learned. With "Block trackers" on, the browser then blocks a learned domain
// that read a canvas, and only removes the cookies of one learned from cookies alone (as Privacy Badger's
// "cookie block"): a group's shared login or image domain then keeps working on its sister sites.
//
// Privacy: sites are never stored by name. Each one becomes a short salted hash (the salt is random, made once and
// kept in this browser), at most THRESHOLD per domain, dropped once the domain is learned. Only third-party domain
// names, counts and dates are kept.

export const LEARN_KEY = 'learn';
export const THRESHOLD = 3;
export const SIGNALS = ['cookie', 'canvas'];
const MAX_DOMAINS = 3000;

// Never learned: code and font hosts, sign-in, payments, captchas and video players many sites need (blocking
// them would break pages, and the cookie or canvas use there is not cross-site tracking as such).
export const NEVER = new Set([
  'cloudflare.com', 'cloudfront.net', 'akamaihd.net', 'akamaized.net', 'fastly.net', 'jsdelivr.net', 'unpkg.com',
  'gstatic.com', 'googleapis.com', 'google.com', 'youtube.com', 'youtube-nocookie.com', 'ytimg.com', 'vimeo.com',
  'vimeocdn.com', 'apple.com', 'cdn-apple.com', 'microsoft.com', 'live.com', 'microsoftonline.com', 'stripe.com',
  'stripe.network', 'paypal.com', 'paypalobjects.com', 'hcaptcha.com', 'recaptcha.net', 'github.com',
  'githubusercontent.com', 'wp.com', 'wordpress.com', 'jquery.com', 'bootstrapcdn.com', 'fontawesome.com',
  'typekit.net', 'adobe.com', 'amazonaws.com', 'azureedge.net', 'twimg.com', 'fbcdn.net',
]);

const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
const put = (obj, key, value) => Object.defineProperty(obj, key, { value, enumerable: true, writable: true, configurable: true });

/**
 * @typedef {{ sites: string[], learned: boolean, signals: Record<string, number>, first: number, last: number }} Entry
 * @typedef {{ v: 1, enabled: boolean, salt: string, domains: Record<string, Entry> }} LearnStore
 */

/** @param {unknown} raw @param {() => string} [newSalt] @returns {LearnStore} */
export function normalizeLearn(raw, newSalt = randomSalt) {
  const r = raw && typeof raw === 'object' ? /** @type {any} */ (raw) : {};
  const out = { v: 1, enabled: r.enabled === true, salt: typeof r.salt === 'string' && r.salt.length >= 16 ? r.salt : newSalt(),
    domains: {} };
  for (const [domain, e] of Object.entries(r.domains && typeof r.domains === 'object' ? r.domains : {})) {
    if (!/^[a-z0-9.-]+\.[a-z0-9-]+$/.test(domain) || !e || typeof e !== 'object') continue;
    const sites = Array.isArray(e.sites) ? e.sites.filter((s) => typeof s === 'string' && /^[0-9a-f]{8}$/.test(s)) : [];
    const signals = {};
    for (const k of SIGNALS) if (Number.isFinite(e.signals?.[k]) && e.signals[k] > 0) signals[k] = Math.floor(e.signals[k]);
    put(out.domains, domain, { sites: sites.slice(0, THRESHOLD), learned: e.learned === true, signals,
      first: Number.isFinite(e.first) ? e.first : 0, last: Number.isFinite(e.last) ? e.last : 0 });
  }
  return out;
}

export function randomSalt() {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** 32-bit FNV-1a of salt + site, as 8 hex characters: tells sites apart without keeping their names. */
export function siteHash(salt, site) {
  let h = 0x811c9dc5;
  for (const ch of salt + '|' + site) {
    h ^= ch.charCodeAt(0);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}

/**
 * Does a Cookie header carry a value that looks like an identifier? Long enough, with letters and digits or
 * mostly hex, and not a plain word or a short setting ("en", "true", "1"). Values are read here only, never kept.
 * @param {string | undefined} header
 */
export function idLikeCookie(header) {
  if (!header) return false;
  for (const part of header.split(';')) {
    const value = part.slice(part.indexOf('=') + 1).trim().replace(/^"|"$/g, '');
    if (value.length < 10 || value.length > 400) continue;
    const letters = /[a-z]/i.test(value);
    const digits = (value.match(/[0-9]/g) || []).length;
    if (/^(true|false|null|undefined)$/i.test(value)) continue;
    if (letters && digits >= 3) return true;
    if (!letters && digits >= 14) return true; // long numbers; 10 to 13 digits are usually a date ("last visit")
  }
  return false;
}

/**
 * One signal of a third-party domain on a site. Returns true when this makes the domain newly learned, or changes
 * what is done with a learned one.
 * @param {LearnStore} store @param {string} domain registrable domain of the third party
 * @param {string} site registrable domain of the page @param {string} kind one of SIGNALS @param {number} now
 */
export function noteSignal(store, domain, site, kind, now) {
  if (!store.enabled || !SIGNALS.includes(kind) || !domain || !site || domain === site || NEVER.has(domain)) return false;
  let e = has(store.domains, domain) ? store.domains[domain] : null;
  if (!e) {
    if (Object.keys(store.domains).length >= MAX_DOMAINS) dropOldest(store);
    e = { sites: [], learned: false, signals: {}, first: now, last: now };
    put(store.domains, domain, e);
  }
  e.last = now;
  e.signals[kind] = (e.signals[kind] || 0) + 1;
  // already learned: only a first canvas read changes what happens to it (cookies removed -> blocked)
  if (e.learned) return kind === 'canvas' && e.signals.canvas === 1;
  const h = siteHash(store.salt, site);
  if (!e.sites.includes(h)) e.sites.push(h);
  if (e.sites.length < THRESHOLD) return false;
  e.learned = true;
  e.sites = []; // no longer needed
  return true;
}

function dropOldest(store) {
  const candidates = Object.entries(store.domains).filter(([, e]) => !e.learned).sort((a, b) => a[1].last - b[1].last);
  const victim = (candidates[0] || Object.entries(store.domains).sort((a, b) => a[1].last - b[1].last)[0]);
  if (victim) delete store.domains[victim[0]];
}

/** What the browser does with each learned domain: block it (it read a canvas) or remove its cookies. */
export function learnedActions(store) {
  const block = [];
  const strip = [];
  for (const [d, e] of Object.entries(store.domains)) if (e.learned) (e.signals.canvas ? block : strip).push(d);
  return { block: block.sort(), strip: strip.sort() };
}

/** @param {LearnStore} store @returns {string[]} */
export function learnedDomains(store) {
  return Object.entries(store.domains).filter(([, e]) => e.learned).map(([d]) => d).sort();
}

/** For the settings page: learned domains and how many are still being watched (fewer than THRESHOLD sites). */
export function learnView(store) {
  const learned = Object.entries(store.domains).filter(([, e]) => e.learned)
    .map(([domain, e]) => ({ domain, signals: e.signals, first: e.first, last: e.last, action: e.signals.canvas ? 'block' : 'strip' }))
    .sort((a, b) => b.last - a.last);
  const watching = Object.values(store.domains).filter((e) => !e.learned).length;
  return { enabled: store.enabled, threshold: THRESHOLD, learned, watching };
}
