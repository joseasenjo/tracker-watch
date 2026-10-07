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
 *           behaviours?: Record<string, string[]>, behavioursOther?: Record<string, string[]>,
 *           liveCookies?: boolean }} [extra]  cookies and script behaviours per service; liveCookies: the
 *   cookies were read from the user's own browser (worded "holds ... set on this visit or earlier")
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
      stopped: s.stopped, bytes: s.bytes || null, requests,
      // requests made from inside embedded third-party frames, by the frame's site (the rest: the page itself)
      via: Object.entries(s.via || {}).map(([site, n]) => ({ site, requests: n })).sort((a, b) => b.requests - a.requests),
      phrases: phrases(activity, glossary, { live: Boolean(extra.liveCookies) }),
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

  // embedded frames that contacted tracking services, and which
  const frameMap = {};
  for (const s of tracking) {
    for (const v of s.via) {
      const f = (frameMap[v.site] ??= { site: v.site, services: [], requests: 0 });
      f.services.push(s.service);
      f.requests += v.requests;
    }
  }
  const frames = Object.values(frameMap).map((f) => ({ ...f, services: f.services.sort(byName) }))
    .sort((a, b) => b.services.length - a.services.length || byName(a.site, b.site));
  const sized = page.bytes.exact + page.bytes.approx;
  // F6: what our own filter lists (trackerwatch-verified.txt / -full.txt: one `||domain^$third-party` rule per
  // tracking service of the list) would have stopped directly. Simulated: nothing is blocked.
  const simulate = (list) => ({
    services: list.length,
    requests: list.reduce((n, s) => n + sum(s.requests), 0),
    bytes: list.reduce((n, s) => n + (s.bytes || 0), 0),
  });
  return {
    site: page.site, host: page.host,
    interaction: page.interactionAt === null ? null : { kind: page.interaction, at: page.interactionAt },
    trackingBefore: trackingBefore.length,
    // one visit, no repeated passes: shown with the label "one visit", never as "high confidence"
    band: bandFor(trackingBefore.length, glossary.bands),
    trackingNewAfter: tracking.filter((s) => s.newAfterInteraction).map((s) => s.service).sort(byName),
    trackingTotal: tracking.length,
    thirdPartyRequestsAfter: domains.reduce((n, d) => n + (d.requests - (d.before || 0)), 0),
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
    frames,
    // tracking services contacted only from inside embedded frames, never by the page itself
    onlyFromFrames: tracking.filter((s) => s.via.length && s.via.reduce((n, v) => n + v.requests, 0) >= sum(s.requests))
      .map((s) => s.service).sort(byName),
    behaviourServices: Object.keys(behaviours).filter((k) => behaviours[k].length).length,
    behavioursOther: Object.entries(extra.behavioursOther ?? {}).map(([domain, kinds]) => ({ domain, kinds }))
      .sort((a, b) => byName(a.domain, b.domain)),
    truncated: page.truncated,
    blocking: {
      verified: simulate(tracking.filter((s) => s.verified)), full: simulate(tracking),
      thirdRequests: page.totals.third, thirdBytes: sized ? page.bytes.sum : 0,
    },
  };
}

/**
 * F5: one row of the tab's journey, kept in session memory only: the registrable domain, its count and the
 * operators (companies) of the tracking services it contacted before the first interaction. No address, no query.
 * @param {ReturnType<typeof summarizePage>} summary
 */
export function journeyEntry(summary) {
  return {
    site: summary.site, tracking: summary.trackingBefore,
    // same window as the count: tracking services contacted before the first interaction
    operators: summary.operators.filter((o) => o.services.some((s) => s.tracking && s.before > 0))
      .map((o) => o.entity).sort(byName),
  };
}

/** Operators present both on the previous page of the journey and on this one. */
export function commonOperators(previous, current) {
  const here = new Set(current.operators);
  return previous.operators.filter((o) => here.has(o));
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
