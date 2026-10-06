// From the tab state to what the panel shows, and the comparison with the weekly baseline.
// Counts, not verdicts: the headline is the number of tracking services contacted before the first
// interaction, which is comparable with the weekly measurement (it never interacts).

import { bandFor } from './bands.js';
import { KINDS } from './requests.js';
import { phrases } from './activity.js';
import { registrableDomain } from './psl.js';

const sum = (counts) => KINDS.reduce((n, k) => n + (counts[k] || 0), 0);
const byName = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

/**
 * @param {import('./page.js').PageState} page
 * @param {{ bands: import('./bands.js').Band[] } & import('./activity.js').Glossary} glossary
 * @param {{ cookies?: Record<string, import('./activity.js').CookieInfo[]>,
 *           behaviours?: Record<string, string[]> }} [extra]  cookies and script behaviours per service
 */
export function summarizePage(page, glossary, extra = {}) {
  const cookies = extra.cookies ?? {};
  const behaviours = extra.behaviours ?? {};
  const services = Object.entries(page.services).map(([service, s]) => {
    const before = sum(s.before);
    const after = sum(s.after);
    const requests = Object.fromEntries(KINDS.map((k) => [k, s.before[k] + s.after[k]]));
    const activity = { requests, cookies: cookies[service] ?? [], behaviours: behaviours[service] ?? [] };
    return {
      service, entity: s.entity, category: s.category, tracking: s.tracking, verified: s.verified,
      contacted: before + after > 0, before, after, newAfterInteraction: before === 0 && after > 0,
      stopped: s.stopped, bytes: s.bytes || null, requests, phrases: phrases(activity, glossary),
    };
  });
  const contacted = services.filter((s) => s.contacted);
  const tracking = contacted.filter((s) => s.tracking);
  const trackingBefore = tracking.filter((s) => s.before > 0);
  const domains = Object.values(page.domains);

  const operators = {};
  for (const s of contacted) (operators[s.entity] ??= []).push(s);
  const operatorList = Object.entries(operators).map(([entity, list]) => ({
    entity,
    trackingServices: list.filter((s) => s.tracking).length,
    services: list.sort((a, b) => byName(a.service, b.service)),
  })).sort((a, b) => b.trackingServices - a.trackingServices || byName(a.entity, b.entity));

  const sized = page.bytes.exact + page.bytes.approx;
  return {
    site: page.site, host: page.host,
    interaction: page.interactionAt === null ? null : { kind: page.interaction, at: page.interactionAt },
    trackingBefore: trackingBefore.length,
    // one visit, no repeated passes: shown with the label "one visit", never as "high confidence"
    band: bandFor(trackingBefore.length, glossary.bands),
    trackingNewAfter: tracking.filter((s) => s.newAfterInteraction).map((s) => s.service).sort(byName),
    trackingTotal: tracking.length,
    thirdPartyDomains: domains.filter((d) => d.requests > 0).length,
    thirdPartyDomainsBefore: domains.filter((d) => d.window === 'before').length,
    thirdPartyRequests: page.totals.third,
    cachedRequests: page.totals.cached,
    bytes: {
      total: sized ? page.bytes.sum : null,
      // exact only when every sized response was a transfer size (Firefox); otherwise a minimum
      exact: sized > 0 && page.bytes.approx === 0 && page.bytes.unknown === 0,
      unknown: page.bytes.unknown,
    },
    stopped: page.stopped,
    // listed services that never got through (every request stopped before going out)
    stoppedServices: services.filter((s) => !s.contacted).map((s) => s.service).sort(byName),
    operators: operatorList,
    unverifiedServices: contacted.filter((s) => !s.verified).length,
    truncated: page.truncated,
  };
}

/**
 * The weekly-snapshot entry for a page, if the site is measured: same registrable domain and, when several
 * entries share it, the one whose URL path is the longest prefix of the page's path.
 * @param {{ sites: Record<string, any> }} sitesData
 * @param {string} url
 * @param {import('./psl.js').SuffixNode} trie
 */
export function findSite(sitesData, url, trie) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  const reg = registrableDomain(trie, parsed.hostname);
  let best = null;
  let bestLen = -1;
  for (const [id, site] of Object.entries(sitesData.sites)) {
    const declared = new Set([site.host, ...site.first_party_domains.map((d) => registrableDomain(trie, d))]);
    if (!declared.has(reg)) continue;
    let path = '/';
    try { path = new URL(site.url).pathname; } catch { /* keep "/" */ }
    const prefix = path.endsWith('/') ? path : path + '/';
    const len = site.host === reg && (parsed.pathname + '/').startsWith(prefix) ? prefix.length : 0;
    if (len > bestLen) {
      best = { id, ...site };
      bestLen = len;
    }
  }
  return best;
}

/**
 * What the live page and the weekly baseline have in common and where they differ. Services only seen here
 * or only in the baseline are facts to show, with the reasons a number can differ; not an error either way.
 * @param {ReturnType<typeof summarizePage>} summary
 * @param {any} site  entry from findSite
 */
export function compareWithBaseline(summary, site) {
  if (!site) return null;
  const here = new Set(summary.operators.flatMap((o) => o.services.filter((s) => s.tracking && s.before > 0).map((s) => s.service)));
  const weekly = new Set(site.services);
  return {
    name: site.name, date: site.date, vantage: site.vantage, browser: site.browser,
    weekly: site.status === 'ok' ? site.tracking_services : null, weeklyBand: site.band,
    here: summary.trackingBefore,
    history: site.history,
    onlyHere: [...here].filter((s) => !weekly.has(s)).sort(byName),
    onlyWeekly: [...weekly].filter((s) => !here.has(s)).sort(byName),
    inBoth: [...here].filter((s) => weekly.has(s)).length,
  };
}
