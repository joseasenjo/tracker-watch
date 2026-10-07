// "Your own banner test" (opt-in): per site, what was counted before you answered its consent banner, after
// you rejected and after you accepted, kept only in this browser. Stored: the site's registrable domain, the
// date, service names from our list and cookie names; never URLs, cookie values or anything else.
// Counts, not verdicts: a run is "not clean" when its numbers are not comparable (a blocker stopped requests).
// Tracking services that already recognised the browser from earlier visits are a note, not a fault: with
// third-party cookies on, that is the normal case, and it does not depend on this answer.

export const STORE_KEY = 'mytests';
export const KEEP_RUNS = 3;
export const DEFAULT_DAYS = 30;
export const DAY_OPTIONS = [7, 30, 90];
const DAY = 86400000;
const CHOICES = ['reject', 'accept'];
const MAX_SITES = 200;
const byName = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

/**
 * @typedef {{ id: string, at: number, date: string, tool: string | null, before: number,
 *   beforeServices: string[], after: string[], newAfter: string[], cookies: Record<string, string[]>,
 *   clean: boolean, reasons: string[], notes: string[], cleared: boolean, payOrAccept?: boolean }} Run
 * @typedef {{ reject: Run[], accept: Run[] }} SiteTests
 * @typedef {{ v: 1, settings: { enabled: boolean, days: number }, sites: Record<string, SiteTests> }} Store
 * @typedef {{ id: string, choice: 'reject' | 'accept', tool: string | null, startedAt: number,
 *   beforeServices: string[], carriedAfter: string[], carriedCookies: Record<string, string[]>,
 *   carriedReasons: string[], cleared: boolean, payOrAccept?: boolean }} Pending   kept on the page while the test runs
 */

/** @returns {Store} */
export function emptyStore() {
  return { v: 1, settings: { enabled: false, days: DEFAULT_DAYS }, sites: {} };
}

/** A stored value read back from storage, repaired if it is missing or malformed. @returns {Store} */
export function normalizeStore(value) {
  const out = emptyStore();
  if (!value || typeof value !== 'object' || value.v !== 1) return out;
  const s = value.settings || {};
  out.settings.enabled = s.enabled === true;
  out.settings.days = DAY_OPTIONS.includes(s.days) ? s.days : DEFAULT_DAYS;
  for (const [site, t] of Object.entries(value.sites || {})) {
    if (site === '__proto__' || !t || typeof t !== 'object') continue;
    out.sites[site] = { reject: Array.isArray(t.reject) ? t.reject : [], accept: Array.isArray(t.accept) ? t.accept : [] };
  }
  return out;
}

const trackingContacted = (summary) => summary.operators.flatMap((o) => o.services)
  .filter((s) => s.tracking && s.contacted);

/**
 * Start a test when the first interaction was a click on "reject" or "accept" of a consent banner.
 * @param {ReturnType<import('./report.js').summarizePage>} summary  the page at the moment of the click
 * @param {{ choice: string, tool: string | null } | null} click
 * @param {{ nowMs: number, cleared: boolean, payOrAccept?: boolean }} env  cleared: Lens cleared this site's data
 *   just before; payOrAccept: the banner's only refusal was a subscription
 * @returns {Pending | null}
 */
export function startTest(summary, click, env) {
  if (!click || !CHOICES.includes(click.choice)) return null;
  const before = trackingContacted(summary).filter((s) => s.before > 0).map((s) => s.service).sort(byName);
  return { id: `${env.nowMs.toString(36)}-${Math.floor(Math.random() * 1e6).toString(36)}`, choice: click.choice,
    tool: click.tool, startedAt: env.nowMs, beforeServices: before, carriedAfter: [], carriedCookies: {},
    carriedReasons: [], cleared: Boolean(env.cleared), payOrAccept: Boolean(env.payOrAccept) };
}

/**
 * The site reloaded right after the answer: everything on the new page belongs to "after".
 * @param {Pending} pending  @param {Run} run  the run as computed from the page being left
 * @returns {Pending}
 */
export function carryTest(pending, run) {
  return { ...pending, carriedAfter: run.after, carriedCookies: run.cookies, carriedReasons: run.reasons };
}

/**
 * The run as it stands now.
 * @param {Pending} pending
 * @param {ReturnType<import('./report.js').summarizePage>} summary
 * @param {import('./page.js').PageState} page
 * @param {Record<string, import('./activity.js').CookieInfo[]>} cookies  per service, as now in the browser
 * @param {boolean} continued  this page is the reload after the answer (all of it counts as "after")
 * @returns {Run}
 */
export function currentRun(pending, summary, page, cookies, continued) {
  const contacted = trackingContacted(summary);
  const now = contacted.filter((s) => (continued ? s.before + s.after : s.after) > 0).map((s) => s.service);
  const after = [...new Set([...pending.carriedAfter, ...now])].sort(byName);
  const was = new Set(pending.beforeServices);
  const cookieMap = { ...pending.carriedCookies };
  for (const svc of after) {
    const names = (has(cookies, svc) ? cookies[svc] : []).map((c) => c.name);
    if (names.length) cookieMap[svc] = [...new Set([...(cookieMap[svc] || []), ...names])].sort(byName).slice(0, 20);
  }
  const reasons = new Set(pending.carriedReasons);
  const tracking = new Set(contacted.map((s) => s.service));
  if (summary.stopped.client) reasons.add('blocker');
  return {
    id: pending.id, at: pending.startedAt, date: new Date(pending.startedAt).toISOString().slice(0, 10),
    tool: pending.tool, before: pending.beforeServices.length, beforeServices: pending.beforeServices,
    after, newAfter: after.filter((s) => !was.has(s)), cookies: cookieMap,
    clean: reasons.size === 0, reasons: [...reasons].sort(byName),
    notes: Object.keys(page.told.recognised || {}).filter((s) => tracking.has(s)).sort(byName), cleared: pending.cleared,
    payOrAccept: Boolean(pending.payOrAccept),
  };
}

/** Add or update a run of one site, keeping the latest KEEP_RUNS per answer. Mutates and returns the store. */
export function saveRun(store, site, choice, run) {
  if (!site || site === '__proto__' || !CHOICES.includes(choice)) return store;
  const t = has(store.sites, site) ? store.sites[site] : (store.sites[site] = { reject: [], accept: [] });
  const list = t[choice].filter((r) => r.id !== run.id);
  list.push(run);
  list.sort((a, b) => a.at - b.at);
  t[choice] = list.slice(-KEEP_RUNS);
  const names = Object.keys(store.sites);
  if (names.length > MAX_SITES) {
    const last = (n) => Math.max(0, ...store.sites[n].reject.map((r) => r.at), ...store.sites[n].accept.map((r) => r.at));
    names.sort((a, b) => last(a) - last(b));
    for (const n of names.slice(0, names.length - MAX_SITES)) delete store.sites[n];
  }
  return store;
}

/** Drop runs older than the chosen number of days. Mutates and returns the store. */
export function purge(store, nowMs) {
  const limit = nowMs - store.settings.days * DAY;
  for (const site of Object.keys(store.sites)) {
    const t = store.sites[site];
    t.reject = t.reject.filter((r) => r.at >= limit);
    t.accept = t.accept.filter((r) => r.at >= limit);
    if (!t.reject.length && !t.accept.length) delete store.sites[site];
  }
  return store;
}

/** min–max of a list of numbers, or null. */
function range(values) {
  return values.length ? [Math.min(...values), Math.max(...values)] : null;
}

/**
 * What the panel shows for one site: per answer, the runs, the range of tracking services after the answer
 * (all runs and clean runs only), the services seen after it in every clean run; and the next step to take.
 * @param {Store} store  @param {string} site
 */
export function siteView(store, site) {
  const t = has(store.sites, site) ? store.sites[site] : { reject: [], accept: [] };
  const part = (runs) => {
    const clean = runs.filter((r) => r.clean);
    const every = clean.length
      ? clean.map((r) => new Set(r.after)).reduce((acc, s) => new Set([...acc].filter((x) => s.has(x))))
      : new Set();
    return {
      runs: [...runs].reverse(), // newest first
      range: range(runs.map((r) => r.after.length)),
      cleanRange: range(clean.map((r) => r.after.length)),
      cleanRuns: clean.length,
      inEveryCleanRun: [...every].sort(byName),
    };
  };
  const reject = part(t.reject);
  const accept = part(t.accept);
  // "accept or pay": there is no free refusal to test, so accepting is the whole test
  const payOrAccept = [...t.reject, ...t.accept].some((r) => r.payOrAccept);
  let next = 'reject';
  if (payOrAccept) next = accept.cleanRuns ? 'repeat' : 'accept';
  else if (reject.cleanRuns && !accept.cleanRuns) next = 'accept';
  else if (reject.cleanRuns && accept.cleanRuns) next = 'repeat';
  // services after rejecting that were also there after accepting, from the latest clean runs
  let comparison = null;
  if (reject.cleanRuns && accept.cleanRuns) {
    const r = t.reject.filter((x) => x.clean).at(-1);
    const a = t.accept.filter((x) => x.clean).at(-1);
    const aSet = new Set(a.after);
    comparison = { reject: r.after.length, accept: a.after.length,
      both: r.after.filter((s) => aSet.has(s)).length, onlyAccept: a.after.filter((s) => !r.after.includes(s)).length };
  }
  return { reject, accept, next, comparison, payOrAccept };
}

/** Everything stored, for "Export my tests" (already only aggregates and names). */
export function exportStore(store, nowMs) {
  return { format: 'tracker-watch-lens/my-banner-tests', version: 1, exported: new Date(nowMs).toISOString(),
    keepDays: store.settings.days, sites: store.sites };
}
