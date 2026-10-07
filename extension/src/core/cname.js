// Disguised trackers (CNAME cloaking): a host that looks like part of the site (metrics.site.example) whose DNS name
// points to a tracking company. Only Firefox lets extensions resolve names; the targets come from AdGuard's
// cname-trackers list (MIT), bundled as data/cname_trackers.json. What is kept: the host and the company.

/**
 * The tracking company behind a canonical name, if its target (or a parent of it) is listed.
 * @param {Record<string, string>} targets  target domain -> company
 * @param {string} canonical  the canonical name the browser resolved
 * @returns {{ target: string, company: string } | null}
 */
export function cnameMatch(targets, canonical) {
  const labels = String(canonical || '').toLowerCase().replace(/\.$/, '').split('.');
  for (let i = 0; i < labels.length - 1; i++) {
    const suffix = labels.slice(i).join('.');
    if (Object.prototype.hasOwnProperty.call(targets, suffix)) return { target: suffix, company: targets[suffix] };
  }
  return null;
}

/**
 * Should this host be checked? Only hosts of the site itself other than the page's own host: third parties are
 * already named by the list, and the page's own host is the site.
 * @param {import('./page.js').PageState} page  @param {string} host  @param {string} reg  registrable domain of host
 */
export function worthResolving(page, host, reg) {
  return Boolean(host) && host !== page.host && page.firstParty.includes(reg);
}

/**
 * Record a disguised tracker on the page (at most 30).
 * @param {import('./page.js').PageState} page  @param {string} host  @param {{ target: string, company: string }} m
 */
export function noteCloaked(page, host, m) {
  const map = (page.cloaked ??= {});
  if (Object.prototype.hasOwnProperty.call(map, host) || Object.keys(map).length >= 30) return page;
  Object.defineProperty(map, host, { value: { target: m.target, company: m.company }, enumerable: true, writable: true, configurable: true });
  return page;
}
