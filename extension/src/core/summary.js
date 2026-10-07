// F8 "Your week" (opt-in, off by default): per day, how many pages were counted and, per company, on how many
// of them its tracking services were contacted before the first click. Which pages or sites is never kept:
// a list of domains would already be a browsing history.

export const SUMMARY_KEY = 'summary';
export const KEEP_DAYS = 35;
const DAY = 86400000;
const MAX_OPERATORS_PER_DAY = 300;
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

/**
 * @typedef {{ pages: number, operators: Record<string, number> }} Day
 * @typedef {{ v: 1, enabled: boolean, days: Record<string, Day> }} SummaryStore
 */

/** @returns {SummaryStore} */
export function emptySummary() {
  return { v: 1, enabled: false, days: {} };
}

/** A stored value read back from storage, repaired if missing or malformed. @returns {SummaryStore} */
export function normalizeSummary(value) {
  const out = emptySummary();
  if (!value || typeof value !== 'object' || value.v !== 1) return out;
  out.enabled = value.enabled === true;
  for (const [date, day] of Object.entries(value.days || {})) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !day || typeof day !== 'object') continue;
    const operators = {};
    for (const [name, n] of Object.entries(day.operators || {})) {
      if (name !== '__proto__' && Number.isInteger(n) && n > 0) operators[name] = n;
    }
    out.days[date] = { pages: Number.isInteger(day.pages) && day.pages > 0 ? day.pages : 0, operators };
  }
  return out;
}

const dateOf = (ms) => new Date(ms).toISOString().slice(0, 10);

/**
 * Count one page (when it is left or its tab closed). Pages with no tracking-list contact still count as pages.
 * @param {SummaryStore} store
 * @param {string[]} operators  companies whose tracking services this page contacted before the first click
 * @param {number} nowMs
 */
export function addPage(store, operators, nowMs) {
  const date = dateOf(nowMs);
  const day = has(store.days, date) ? store.days[date] : (store.days[date] = { pages: 0, operators: {} });
  day.pages += 1;
  for (const name of new Set(operators)) {
    if (name === '__proto__') continue;
    if (!has(day.operators, name) && Object.keys(day.operators).length >= MAX_OPERATORS_PER_DAY) continue;
    day.operators[name] = (has(day.operators, name) ? day.operators[name] : 0) + 1;
  }
  return store;
}

/** Drop days older than KEEP_DAYS. */
export function purgeSummary(store, nowMs) {
  const limit = dateOf(nowMs - KEEP_DAYS * DAY);
  for (const date of Object.keys(store.days)) if (date < limit) delete store.days[date];
  return store;
}

/**
 * The last `days` days (today included): pages counted and companies by the number of pages they reached.
 * @param {SummaryStore} store  @param {number} nowMs  @param {number} [days]
 */
export function periodView(store, nowMs, days = 7) {
  const from = dateOf(nowMs - (days - 1) * DAY);
  let pages = 0;
  const operators = {};
  const perDay = [];
  for (const [date, day] of Object.entries(store.days).sort()) {
    if (date < from) continue;
    pages += day.pages;
    perDay.push({ date, pages: day.pages });
    for (const [name, n] of Object.entries(day.operators)) operators[name] = (has(operators, name) ? operators[name] : 0) + n;
  }
  const ranked = Object.entries(operators).map(([name, n]) => ({ name, pages: n }))
    .sort((a, b) => b.pages - a.pages || (a.name < b.name ? -1 : 1));
  return { from, days, pages, perDay, operators: ranked };
}
