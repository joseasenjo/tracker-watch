// The tab reducer with the event sequences each browser produced in the M0 spike (spec §12).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildSuffixTrie } from '../src/core/psl.js';
import { createTrackerList } from '../src/core/classify.js';
import { errorReason, hostOf, kindOf } from '../src/core/requests.js';
import { LIMITS, markInteraction, onCompleted, onError, onRequest, onSent, redirectPage, startPage } from '../src/core/page.js';
import { compareWithBaseline, findSite, summarizePage } from '../src/core/report.js';
import { cookiesByService } from '../src/core/activity.js';

const load = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const glossary = load('../data/glossary.json');
const sites = load('../data/sites.json');
const trie = buildSuffixTrie(load('../data/psl.json').rules);
const ctx = {
  trie,
  list: createTrackerList({
    tracking_categories: ['advertising', 'analytics', 'social', 'session_replay', 'audience_measurement'],
    domains: {
      'doubleclick.net': { entity: 'Google', category: 'advertising', verified: true },
      'google-analytics.com': { entity: 'Google', category: 'analytics', verified: true },
      'adnxs.com': { entity: 'Microsoft (Xandr)', category: 'advertising', verified: false },
      'googletagmanager.com': { entity: 'Google', category: 'tag_manager', verified: true },
      'facebook.net': { entity: 'Meta', category: 'social', verified: true },
    },
  }),
};

let seq = 0;
/** Feed one request through the events a browser fires; returns its id. */
function request(page, url, type, { now = 1, chain = 'ok', error, size = 100, exact = false, fromCache = false } = {}) {
  const requestId = String(++seq);
  onRequest(page, { requestId, url, type, now }, ctx);
  if (chain === 'ok') {
    onSent(page, { requestId });
    onCompleted(page, { requestId, size, sizeExact: exact });
  } else if (chain === 'cache') {
    onCompleted(page, { requestId, fromCache: true, size: null });
  } else if (chain === 'blocked') {
    onError(page, { requestId, error });
  } else if (chain === 'sent-then-failed') {
    onSent(page, { requestId });
    onError(page, { requestId, error });
  }
  return requestId;
}

const newPage = (url = 'https://www.example.co.uk/news', declared = ['excdn.com']) =>
  startPage({ url, now: 0, requestId: 'nav', declared }, ctx);

test('normalisation: kinds, error reasons, hosts', () => {
  assert.equal(kindOf('ping'), 'background'); // Chromium sendBeacon
  assert.equal(kindOf('beacon'), 'background'); // Firefox sendBeacon
  assert.equal(kindOf('sub_frame'), 'frame');
  assert.equal(kindOf('stylesheet'), 'other');
  assert.equal(errorReason('net::ERR_BLOCKED_BY_CLIENT'), 'client');
  assert.equal(errorReason('NS_ERROR_TRACKING_URI'), 'browser');
  assert.equal(errorReason('NS_ERROR_ABORT'), 'cancelled');
  assert.equal(errorReason('net::ERR_CONNECTION_REFUSED'), 'failed');
  assert.equal(hostOf('https://WWW.Example.com:8443/a?b=c'), 'www.example.com');
  assert.equal(hostOf('data:text/plain,x'), null);
  assert.equal(hostOf('chrome-extension://abc/x.js'), null);
  assert.equal(hostOf('not a url'), null);
});

test('first party: page, declared domains and a redirected final site', () => {
  const page = newPage();
  request(page, 'https://static.excdn.com/a.js', 'script');
  request(page, 'https://img.example.co.uk/b.png', 'image');
  assert.equal(page.totals.third, 0);
  redirectPage(page, 'https://www.example.com/', ctx);
  assert.deepEqual(page.firstParty, ['example.co.uk', 'example.com', 'excdn.com']);
});

test('counts tracking services and lists the others without counting them', () => {
  const page = newPage();
  request(page, 'https://www.googletagmanager.com/gtm.js', 'script');
  request(page, 'https://stats.g.doubleclick.net/collect', 'xmlhttprequest');
  request(page, 'https://www.google-analytics.com/g/collect', 'ping');
  request(page, 'https://cdn.unknown-third.net/x.js', 'script');
  const s = summarizePage(page, glossary);
  assert.equal(s.trackingBefore, 2);
  assert.equal(s.band, 'A');
  assert.equal(s.thirdPartyDomains, 4);
  const google = s.operators.find((o) => o.entity === 'Google');
  assert.equal(google.trackingServices, 2);
  assert.ok(google.services.some((x) => x.service === 'googletagmanager.com' && !x.tracking));
});

test('Chromium: a request blocked by another extension is stopped, not contacted', () => {
  const page = newPage();
  request(page, 'https://ib.adnxs.com/ut', 'script', { chain: 'blocked', error: 'net::ERR_BLOCKED_BY_CLIENT' });
  const s = summarizePage(page, glossary);
  assert.equal(s.trackingBefore, 0);
  assert.equal(s.stopped.client, 1);
  assert.deepEqual(s.stoppedServices, ['adnxs.com']);
  assert.equal(s.thirdPartyDomains, 0);
});

test('Firefox: own protection is named; a generic abort before sending is "cancelled"', () => {
  const page = newPage();
  request(page, 'https://connect.facebook.net/sdk.js', 'script', { chain: 'blocked', error: 'NS_ERROR_TRACKING_URI' });
  request(page, 'https://ib.adnxs.com/ut', 'script', { chain: 'blocked', error: 'NS_ERROR_ABORT' });
  const s = summarizePage(page, glossary);
  assert.equal(s.stopped.browser, 1);
  assert.equal(s.stopped.cancelled, 1);
  assert.equal(s.trackingBefore, 0);
});

test('a request that went out and then failed still counts as contacted', () => {
  const page = newPage();
  request(page, 'https://ib.adnxs.com/ut', 'xmlhttprequest', { chain: 'sent-then-failed', error: 'net::ERR_CONNECTION_RESET' });
  assert.equal(summarizePage(page, glossary).trackingBefore, 1);
});

test('answers from the cache count once and have no size', () => {
  const page = newPage();
  request(page, 'https://www.google-analytics.com/analytics.js', 'script', { chain: 'cache' });
  const s = summarizePage(page, glossary);
  assert.equal(s.trackingBefore, 1);
  assert.equal(s.cachedRequests, 1);
  assert.equal(s.bytes.total, null);
});

test('sizes: exact only when every response had a transfer size', () => {
  const ff = newPage();
  request(ff, 'https://ib.adnxs.com/a', 'script', { size: 20195, exact: true });
  assert.deepEqual(summarizePage(ff, glossary).bytes, { total: 20195, exact: true, unknown: 0 });
  const cr = newPage();
  request(cr, 'https://ib.adnxs.com/a', 'script', { size: 20017 });
  request(cr, 'https://ib.adnxs.com/chunked', 'script', { size: null });
  assert.deepEqual(summarizePage(cr, glossary).bytes, { total: 20017, exact: false, unknown: 1 });
});

test('before and after the first interaction: new services after the click', () => {
  const page = newPage();
  request(page, 'https://stats.g.doubleclick.net/a', 'image', { now: 5 });
  markInteraction(page, { now: 10, kind: 'click' });
  markInteraction(page, { now: 20, kind: 'key' }); // only the first one counts
  request(page, 'https://stats.g.doubleclick.net/b', 'image', { now: 11 });
  request(page, 'https://ib.adnxs.com/c', 'script', { now: 12 });
  const s = summarizePage(page, glossary);
  assert.equal(s.trackingBefore, 1);
  assert.deepEqual(s.trackingNewAfter, ['adnxs.com']);
  assert.equal(s.trackingTotal, 2);
  assert.equal(s.thirdPartyDomainsBefore, 1);
  assert.deepEqual(s.interaction, { kind: 'click', at: 10 });
});

test('requests processed before the click message arrived move to "after"', () => {
  const page = newPage();
  request(page, 'https://stats.g.doubleclick.net/a', 'image', { now: 5 });
  request(page, 'https://connect.facebook.net/sdk.js', 'script', { now: 12 }); // issued by the click handler
  request(page, 'https://stats.g.doubleclick.net/b', 'image', { now: 13 });
  markInteraction(page, { now: 10, kind: 'click' }); // message arrives later, carrying the click time
  const s = summarizePage(page, glossary);
  assert.equal(s.trackingBefore, 1);
  assert.deepEqual(s.trackingNewAfter, ['facebook.net']);
  assert.equal(s.thirdPartyDomainsBefore, 1);
  assert.equal(page.services['doubleclick.net'].window, 'before');
  assert.equal(page.services['doubleclick.net'].after.image, 1);
});

test('a redirect reuses the request id: the first hop stays counted, the new host is tracked', () => {
  const page = newPage();
  onRequest(page, { requestId: 'r', url: 'https://ad.doubleclick.net/click', type: 'script', now: 1 }, ctx);
  onSent(page, { requestId: 'r' });
  onRequest(page, { requestId: 'r', url: 'https://ib.adnxs.com/land', type: 'script', now: 1 }, ctx);
  onSent(page, { requestId: 'r' });
  onCompleted(page, { requestId: 'r', size: 10 });
  assert.equal(summarizePage(page, glossary).trackingBefore, 2);
});

test('events for unknown request ids are ignored (they belong to the previous page)', () => {
  const page = newPage();
  onSent(page, { requestId: 'old' });
  onCompleted(page, { requestId: 'old', size: 5 });
  onError(page, { requestId: 'old', error: 'net::ERR_BLOCKED_BY_CLIENT' });
  assert.equal(page.totals.requests, 0);
  assert.equal(page.stopped.client, 0);
});

test('state is plain JSON and survives a round trip (storage.session)', () => {
  const page = newPage();
  request(page, 'https://stats.g.doubleclick.net/a', 'image');
  const restored = JSON.parse(JSON.stringify(page));
  request(restored, 'https://ib.adnxs.com/b', 'script');
  assert.equal(summarizePage(restored, glossary).trackingBefore, 2);
});

test('no paths, queries or values are stored', () => {
  const page = newPage();
  request(page, 'https://stats.g.doubleclick.net/collect?gclid=SECRET123&uid=42', 'image');
  const text = JSON.stringify(page);
  assert.ok(!text.includes('SECRET123') && !text.includes('collect') && !text.includes('/news'));
});

test('caps: a page cannot grow the state without limit', () => {
  const page = newPage();
  for (let i = 0; i < LIMITS.events + 50; i++) request(page, `https://h${i}.flood.example/x`, 'image');
  assert.equal(page.truncated, true);
  assert.ok(Object.keys(page.domains).length <= LIMITS.domains);
  assert.ok(page.events <= LIMITS.events);
  const hung = newPage();
  for (let i = 0; i < LIMITS.pending + 10; i++) {
    onRequest(hung, { requestId: `p${i}`, url: 'https://ib.adnxs.com/x', type: 'image', now: 1 }, ctx);
  }
  assert.ok(Object.keys(hung.pending).length <= LIMITS.pending);
  assert.equal(hung.truncated, true);
});

test('hostile host names are just strings: nothing is interpreted', () => {
  const page = newPage();
  request(page, 'https://xn--80ak6aa92e.com/<img src=x onerror=alert(1)>', 'script');
  request(page, 'https://__proto__.example/x', 'script');
  request(page, 'https://__proto__/x', 'script');
  request(page, 'https://constructor/x', 'script');
  onRequest(page, { requestId: '__proto__', url: 'https://hasownproperty/x', type: 'image', now: 1 }, ctx);
  onSent(page, { requestId: '__proto__' });
  assert.equal(Object.getPrototypeOf(page.domains), Object.prototype);
  assert.equal(Object.getPrototypeOf(page.pending), Object.prototype);
  assert.equal({}.requests, undefined);
  assert.equal(summarizePage(page, glossary).thirdPartyDomains, 5);
  const restored = JSON.parse(JSON.stringify(page)); // storage round trip keeps them as plain own keys
  assert.equal(restored.domains.__proto__.requests, 1);
  assert.equal({}.requests, undefined);
});

test('weekly baseline: find the site by domain and path, compare service sets', () => {
  const bbc = findSite(sites, 'https://www.bbc.co.uk/news/articles/x', trie);
  assert.equal(bbc.id, 'www-bbc-co-uk-news');
  assert.equal(findSite(sites, 'https://nothing-measured.example/', trie), null);
  const page = startPage({ url: 'https://www.bbc.co.uk/news', now: 0, declared: bbc.first_party_domains }, ctx);
  request(page, 'https://ib.adnxs.com/x', 'script');
  request(page, 'https://connect.facebook.net/x', 'script');
  const cmp = compareWithBaseline(summarizePage(page, glossary), bbc);
  assert.equal(cmp.weekly, bbc.tracking_services);
  assert.equal(cmp.here, 2);
  assert.ok(cmp.inBoth >= 1);
  assert.equal(cmp.inBoth + cmp.onlyWeekly.length, bbc.services.length);
});

test('cookies: grouped by service, partitioned ones kept for this site, first party skipped', () => {
  const page = newPage();
  const now = Date.UTC(2026, 9, 6);
  const grouped = cookiesByService([
    { name: 'IDE', domain: '.doubleclick.net', session: false, expirationDate: now / 1000 + 390 * 86400 },
    { name: 'uid', domain: 'ib.adnxs.com', session: true, partitionKey: { topLevelSite: 'https://example.co.uk' } },
    { name: 'pref', domain: 'www.example.co.uk', session: false, expirationDate: now / 1000 + 86400 },
    { name: 'x', domain: 'cdn.unknown-third.net', session: true },
  ], ctx, page, now);
  assert.deepEqual(Object.keys(grouped).sort(), ['adnxs.com', 'doubleclick.net']);
  assert.deepEqual(grouped['doubleclick.net'][0], { name: 'IDE', persistent: true, days: 390, partitioned: false, thisSite: false });
  assert.equal(grouped['adnxs.com'][0].thisSite, true);
  const s = summarizePage(page, glossary, { cookies: grouped });
  assert.equal(s.operators.length, 0); // cookies alone do not make a service "contacted" on this page
});
