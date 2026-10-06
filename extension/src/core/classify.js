// Tracker lookup and first/third-party split, the same rules as the engine (traceguard.classify):
// the longest matching domain suffix in the list wins; a host is first party when its registrable
// domain is the page's own or one of the site's declared first-party domains.

import { registrableDomain } from './psl.js';

/**
 * @typedef {{ entity: string, category: string, verified: boolean }} ListEntry
 * @typedef {{ domains: Record<string, ListEntry>, tracking_categories: string[] }} TrackersData
 * @typedef {{ domains: Map<string, ListEntry>, tracking: Set<string> }} TrackerList
 * @typedef {{ service: string, entity: string, category: string, verified: boolean }} Match
 */

/**
 * @param {TrackersData} data  contents of data/trackers.json
 * @returns {TrackerList}
 */
export function createTrackerList(data) {
  return { domains: new Map(Object.entries(data.domains)), tracking: new Set(data.tracking_categories) };
}

/**
 * @param {TrackerList} list
 * @param {string} host
 * @returns {Match | null}
 */
export function lookup(list, host) {
  const labels = String(host).replace(/^\.+|\.+$/g, '').toLowerCase().split('.');
  for (let i = 0; i < labels.length - 1; i++) {
    const suffix = labels.slice(i).join('.');
    const entry = list.domains.get(suffix);
    if (entry) return { service: suffix, entity: entry.entity, category: entry.category, verified: entry.verified };
  }
  return null;
}

/**
 * Whether a category counts as a tracking service (tag managers, consent tools and paywalls do not).
 * @param {TrackerList} list
 * @param {string} category
 */
export function isTracking(list, category) {
  return list.tracking.has(category);
}

/**
 * Set of registrable domains treated as first party for a page.
 * @param {import('./psl.js').SuffixNode} trie
 * @param {string} pageHost
 * @param {string[]} [declared]  first_party_domains of the site, when it is in the weekly snapshot
 * @returns {Set<string>}
 */
export function firstPartySet(trie, pageHost, declared = []) {
  const set = new Set();
  for (const d of [pageHost, ...declared]) {
    const reg = registrableDomain(trie, d);
    if (reg) set.add(reg);
  }
  return set;
}

/**
 * @param {import('./psl.js').SuffixNode} trie
 * @param {Set<string>} firstParty
 * @param {string} host
 * @returns {'first' | 'third'}
 */
export function party(trie, firstParty, host) {
  return firstParty.has(registrableDomain(trie, host)) ? 'first' : 'third';
}
