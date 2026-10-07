// What a service did in the browser, in the same words as the website (traceguard.behaviour.phrases).
// It describes what happened, not what the service does with the data afterwards.

import { KINDS } from './requests.js';
import { lookup } from './classify.js';
import { registrableDomain } from './psl.js';

/**
 * @typedef {{ name: string, persistent: boolean, days: number | null, partitioned?: boolean }} CookieInfo
 * @typedef {{ requests: Record<string, number>, cookies: CookieInfo[], behaviours: string[] }} Activity
 * @typedef {{ kind_phrases: Record<string, { singular: string, plural: string, text: string }>,
 *             script_behaviours: Record<string, string> }} Glossary
 */

/**
 * Plain-language list of what one service did, most significant first. Must give exactly the engine's text
 * (tests/parity.test.js).
 * @param {Activity} activity
 * @param {Glossary} glossary
 * @param {{ live?: boolean }} [opts]  live: the cookies are those now in a real browser, which may have been set
 *   on an earlier visit to any site (the engine starts each pass with an empty browser, so it can say "set")
 * @returns {string[]}
 */
export function phrases(activity, glossary, opts = {}) {
  const out = [];
  for (const kind of KINDS) {
    const n = activity.requests[kind] || 0;
    if (n) {
      const p = glossary.kind_phrases[kind];
      out.push(p.text.replace('{n}', String(n)).replace('{noun}', n === 1 ? p.singular : p.plural));
    }
  }
  const cookies = activity.cookies;
  if (cookies.length) {
    const persistent = cookies.filter((c) => c.persistent);
    let text = opts.live
      ? `holds ${cookies.length} cookie${cookies.length !== 1 ? 's' : ''} in your browser, set on this visit or earlier`
      : `set ${cookies.length} cookie${cookies.length !== 1 ? 's' : ''}`;
    if (persistent.length) {
      const days = persistent.map((c) => c.days).filter((d) => d);
      const longest = days.length ? `, the longest lasting about ${Math.max(...days)} days` : '';
      text += ` (${persistent.length} that stay${persistent.length === 1 ? 's' : ''} after you close the browser${longest})`;
    } else {
      text += ' (all end when you close the browser)';
    }
    out.push(text);
  }
  for (const kind of activity.behaviours) out.push(`a script from this domain ${glossary.script_behaviours[kind]}`);
  return out;
}

/**
 * Third-party cookies stored in the browser, grouped by the tracking-list service of their domain.
 * Includes partitioned cookies (the caller must read them with cookies.getAll({ partitionKey: {} }), or
 * Firefox's Total Cookie Protection hides almost all of them). `thisSite` says whether the cookie is kept
 * only for the current site (partitioned under it) rather than shared across sites. Values are never read here.
 * @param {Array<{ name: string, domain: string, session: boolean, expirationDate?: number,
 *                 partitionKey?: { topLevelSite?: string } | null }>} cookies
 * @param {{ trie: import('./psl.js').SuffixNode, list: import('./classify.js').TrackerList }} ctx
 * @param {{ site: string, firstParty: string[] }} page
 * @param {number} nowMs
 * @returns {Record<string, Array<CookieInfo & { thisSite: boolean }>>}
 */
export function cookiesByService(cookies, ctx, page, nowMs) {
  /** @type {Record<string, Array<CookieInfo & { thisSite: boolean }>>} */
  const out = {};
  for (const c of cookies) {
    const domain = String(c.domain || '').replace(/^\./, '');
    if (page.firstParty.includes(registrableDomain(ctx.trie, domain))) continue;
    const match = lookup(ctx.list, domain);
    if (!match) continue;
    const top = c.partitionKey && c.partitionKey.topLevelSite;
    const partitioned = Boolean(top);
    const topHost = top ? top.replace(/^[a-z]+:\/\//, '').replace(/:\d+$/, '') : '';
    const days = !c.session && c.expirationDate ? Math.max(0, Math.round((c.expirationDate * 1000 - nowMs) / 86400000)) : null;
    (out[match.service] ??= []).push({
      name: c.name, persistent: !c.session, days, partitioned,
      thisSite: partitioned && registrableDomain(ctx.trie, topHost) === page.site,
    });
  }
  for (const list of Object.values(out)) list.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  return out;
}
