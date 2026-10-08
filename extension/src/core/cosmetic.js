// Ads (blocker step 3): hide empty ad slots with EasyList's element hiding rules (data/cosmetic.json, built by
// tools/easylist.py). One style sheet per host: the generic selectors (unless the list turns them off there),
// the host's own selectors, minus its exceptions. Inserted by the browser as a user style sheet, so the page
// cannot undo it; Lens never reads or changes the page's content.

const CHUNK = 200;

/**
 * @typedef {{ generic: string[], sites: Record<string, string[]>, exceptions: Record<string, string[]>,
 *   generichide: string[], elemhide: string[] }} CosmeticData
 */

const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

/** The host and each parent domain (a.b.example.com -> a.b.example.com, b.example.com, example.com). */
function suffixes(host) {
  const labels = host.toLowerCase().split('.');
  const out = [];
  for (let i = 0; i < labels.length - 1; i++) out.push(labels.slice(i).join('.'));
  return out;
}

/**
 * Selectors to hide on a host.
 * @param {CosmeticData} data @param {string} host
 * @returns {string[]}
 */
export function selectorsFor(data, host) {
  const names = suffixes(host);
  const inList = (list) => names.some((n) => list.includes(n));
  if (inList(data.elemhide)) return [];
  const except = new Set(names.flatMap((n) => (has(data.exceptions, n) ? data.exceptions[n] : [])));
  const own = names.flatMap((n) => (has(data.sites, n) ? data.sites[n] : []));
  const all = inList(data.generichide) ? own : [...data.generic, ...own];
  return [...new Set(all)].filter((s) => !except.has(s));
}

/**
 * What to insert on a host: whether the shared generic sheet applies as it is, and the host's own selectors.
 * Hosts with exceptions or $generichide / $elemhide (a few hundred) get every selector they need in `own`
 * instead, so the generic sheet is built once and shared (it is large) rather than copied for every host.
 * @param {CosmeticData} data @param {string} host
 * @returns {{ generic: boolean, own: string[] }}
 */
export function hostPlan(data, host) {
  const names = suffixes(host);
  const special = names.some((n) => has(data.exceptions, n) || data.generichide.includes(n) || data.elemhide.includes(n));
  if (special) return { generic: false, own: selectorsFor(data, host) };
  return { generic: true, own: [...new Set(names.flatMap((n) => (has(data.sites, n) ? data.sites[n] : [])))] };
}

/**
 * A style sheet hiding those selectors. Grouped in :is() lists, which skip a selector the browser does not
 * understand instead of dropping the whole group.
 * @param {string[]} selectors
 */
export function styleSheet(selectors) {
  const parts = [];
  for (let i = 0; i < selectors.length; i += CHUNK) {
    parts.push(`:is(${selectors.slice(i, i + CHUNK).join(',\n')}){display:none!important}`);
  }
  return parts.join('\n');
}
