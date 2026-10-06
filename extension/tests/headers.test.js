// F12: what the browser told the site. Names and counts only; values of cookies and parameters never kept.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildSuffixTrie } from '../src/core/psl.js';
import { createTrackerList } from '../src/core/classify.js';
import { countParams, describeSelf, emptyTold, trackingParams } from '../src/core/headers.js';
import { onRequest, onSent, startPage } from '../src/core/page.js';

const trie = buildSuffixTrie(['com', 'net', 'uk', 'co.uk']);
const ctx = { trie, list: createTrackerList({ tracking_categories: ['advertising'],
  domains: { 'doubleclick.net': { entity: 'Google', category: 'advertising', verified: true } } }) };

test('tracking parameters: known names only, values dropped', () => {
  assert.deepEqual(trackingParams('https://x.com/a?gclid=SECRET&utm_source=n&id=3&FBCLID=z'), ['fbclid', 'gclid', 'utm_source']);
  assert.deepEqual(trackingParams('not a url'), []);
  const told = emptyTold();
  countParams(told, ['gclid', '__proto__']);
  countParams(told, ['gclid']);
  assert.equal(told.params.gclid, 2);
  assert.equal({}.gclid, undefined);
});

test('self description from the page request', () => {
  const self = describeSelf([
    { name: 'User-Agent', value: 'Mozilla/5.0 Test' }, { name: 'Accept-Language', value: 'es-ES,es;q=0.9' },
    { name: 'sec-ch-ua-platform', value: '"Windows"' }, { name: 'Sec-GPC', value: '1' },
    { name: 'Referer', value: 'https://www.google.co.uk/search?q=private' },
  ], trie);
  assert.deepEqual(self, { userAgent: 'Mozilla/5.0 Test', language: 'es-ES,es;q=0.9',
    hints: { 'sec-ch-ua-platform': '"Windows"' }, gpc: true, dnt: false, cameFrom: 'google.co.uk' });
});

test('third-party requests: cookies and page address counted, values never stored', () => {
  const page = startPage({ url: 'https://www.example.co.uk/?utm_campaign=x', now: 0, requestId: 'nav' }, ctx);
  onRequest(page, { requestId: 'nav', url: 'https://www.example.co.uk/?utm_campaign=x', type: 'main_frame', now: 0 }, ctx);
  onSent(page, { requestId: 'nav', headers: [{ name: 'User-Agent', value: 'UA' }] }, ctx);
  onRequest(page, { requestId: '1', url: 'https://ad.doubleclick.net/p?gclid=SECRET', type: 'image', now: 1 }, ctx);
  onSent(page, { requestId: '1', headers: [{ name: 'Cookie', value: 'IDE=SECRETVALUE; DSID=x' },
    { name: 'Referer', value: 'https://www.example.co.uk/' }] }, ctx);
  onRequest(page, { requestId: '2', url: 'https://cdn.other.com/x.js', type: 'script', now: 1 }, ctx);
  onSent(page, { requestId: '2', headers: [{ name: 'Referer', value: 'https://www.example.co.uk/' }] }, ctx);
  const told = page.told;
  assert.equal(told.self.userAgent, 'UA');
  assert.equal(told.thirdWithCookies, 1);
  assert.deepEqual(told.cookieServices, { 'doubleclick.net': 2 });
  assert.equal(told.thirdWithReferer, 2);
  assert.deepEqual(told.params, { utm_campaign: 1, gclid: 1 });
  assert.ok(!JSON.stringify(page).includes('SECRET'));
});

test('recognised only when the first request to a service already carries cookies', () => {
  const page = startPage({ url: 'https://www.example.co.uk/', now: 0, requestId: 'nav' }, ctx);
  onRequest(page, { requestId: '1', url: 'https://ad.doubleclick.net/a', type: 'script', now: 1 }, ctx);
  onSent(page, { requestId: '1', headers: [] }, ctx); // no cookie yet: the response may set one
  onRequest(page, { requestId: '2', url: 'https://ad.doubleclick.net/b', type: 'image', now: 2 }, ctx);
  onSent(page, { requestId: '2', headers: [{ name: 'Cookie', value: 'IDE=x' }] }, ctx);
  assert.deepEqual(page.told.recognised, {});
  assert.equal(page.told.thirdWithCookies, 1);
});
