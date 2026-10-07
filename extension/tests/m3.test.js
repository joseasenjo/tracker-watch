// M3: blocking simulation (F6), journey rows (F5), page address per service (F12).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildSuffixTrie } from '../src/core/psl.js';
import { createTrackerList } from '../src/core/classify.js';
import { onCompleted, onRequest, onSent, startPage } from '../src/core/page.js';
import { commonOperators, journeyEntry, protectionOf, summarizePage } from '../src/core/report.js';

const load = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const glossary = load('../data/glossary.json');
const ctx = { trie: buildSuffixTrie(load('../data/psl.json').rules), list: createTrackerList({
  tracking_categories: ['advertising'],
  domains: {
    'doubleclick.net': { entity: 'Google', category: 'advertising', verified: true },
    'adnxs.com': { entity: 'Microsoft (Xandr)', category: 'advertising', verified: false },
    'googletagmanager.com': { entity: 'Google', category: 'tag_manager', verified: true },
  },
}) };

function visit(urls) {
  const page = startPage({ url: 'https://www.news.es/', now: 0, requestId: 'nav', declared: [], arrival: null }, ctx);
  urls.forEach((url, i) => {
    const id = String(i);
    onRequest(page, { requestId: id, url, type: 'image', now: 1 }, ctx);
    onSent(page, { requestId: id, headers: [{ name: 'Referer', value: 'https://www.news.es/' }] }, ctx);
    onCompleted(page, { requestId: id, fromCache: false, size: 100, sizeExact: true });
  });
  return page;
}

test('blocking simulation: verified and full lists, direct requests and bytes; tag managers never', () => {
  const page = visit(['https://ad.doubleclick.net/a', 'https://ad.doubleclick.net/b', 'https://ib.adnxs.com/c',
    'https://www.googletagmanager.com/gtm.js', 'https://cdn.other.io/x']);
  const b = summarizePage(page, glossary).blocking;
  assert.deepEqual(b.verified, { services: 1, requests: 2, bytes: 200 });
  assert.deepEqual(b.full, { services: 2, requests: 3, bytes: 300 });
  assert.equal(b.thirdRequests, 5);
  assert.equal(b.thirdBytes, 500);
  assert.deepEqual(Object.keys(page.told.refererServices).sort(), ['adnxs.com', 'doubleclick.net', 'googletagmanager.com']);
});

test('journey rows keep only the site, the count and the operators; common operators', () => {
  const a = journeyEntry(summarizePage(visit(['https://ad.doubleclick.net/a', 'https://ib.adnxs.com/c']), glossary));
  assert.deepEqual(a, { site: 'news.es', tracking: 2, operators: ['Google', 'Microsoft (Xandr)'] });
  const b = journeyEntry(summarizePage(visit(['https://ad.doubleclick.net/a', 'https://www.googletagmanager.com/x']), glossary));
  assert.deepEqual(b.operators, ['Google']);
  assert.deepEqual(commonOperators(a, b), ['Google']);
  const late = visit(['https://ad.doubleclick.net/a']);
  late.interactionAt = 0.5; // requests after the first click do not enter the journey row
  onRequest(late, { requestId: 'z', url: 'https://ib.adnxs.com/late', type: 'image', now: 2 }, ctx);
  onSent(late, { requestId: 'z', headers: [] }, ctx);
  assert.deepEqual(journeyEntry(summarizePage(late, glossary)).operators, ['Google']); // adnxs came after
});

test('cookies read from a real browser are "held", not "set" (they may come from earlier visits)', async () => {
  const { phrases } = await import('../src/core/activity.js');
  const activity = { requests: {}, cookies: [{ name: 'IDE', persistent: true, days: 180 }], behaviours: [] };
  assert.equal(phrases(activity, glossary)[0].startsWith('set 1 cookie'), true); // engine wording (parity)
  assert.equal(phrases(activity, glossary, { live: true })[0],
    'holds 1 cookie in your browser, set on this visit or earlier (1 that stays after you close the browser, the longest lasting about 180 days)');
});

test('requests are attributed to the outermost third-party frame; the frame document itself to its parent', () => {
  const page = startPage({ url: 'https://www.news.es/', now: 0, requestId: 'nav', declared: [], arrival: null }, ctx);
  const req = (id, url, type, frameId, parentFrameId) => {
    onRequest(page, { requestId: id, url, type, now: 1, frameId, parentFrameId }, ctx);
    onSent(page, { requestId: id, headers: [] }, ctx);
  };
  req('1', 'https://ad.doubleclick.net/page', 'image', 0, -1); // the page itself
  req('2', 'https://www.youtube.com/embed/x', 'sub_frame', 5, 0); // video frame
  req('3', 'https://googleads.g.doubleclick.net/ads', 'script', 5, 0); // inside the video
  req('4', 'https://ad.doubleclick.net/inner', 'sub_frame', 9, 5); // a frame inside the video
  req('5', 'https://ib.adnxs.com/px', 'image', 9, 5); // inside that inner frame: still the video
  req('6', 'https://static.news.es/frame', 'sub_frame', 11, 0); // a first-party frame
  req('7', 'https://ib.adnxs.com/px2', 'image', 11, 0); // counts as the page
  const s = summarizePage(page, glossary);
  assert.deepEqual(s.frames, [{ site: 'youtube.com', services: ['adnxs.com', 'doubleclick.net'], requests: 3 }]);
  assert.deepEqual(s.onlyFromFrames, []);
  const dc = s.operators.flatMap((o) => o.services).find((x) => x.service === 'doubleclick.net');
  assert.deepEqual(dc.via, [{ site: 'youtube.com', requests: 2 }]);
});

test('protection: services stopped entirely, partly, and those that got through', () => {
  const st = (client, browser = 0) => ({ client, browser, cancelled: 0, failed: 0 });
  const p = protectionOf([
    { service: 'a', tracking: true, contacted: false, stopped: st(3) },
    { service: 'b', tracking: true, contacted: true, stopped: st(1) },
    { service: 'c', tracking: true, contacted: true, stopped: st(0) },
    { service: 'd', tracking: true, contacted: false, stopped: st(0, 2) },
    { service: 'gtm', tracking: false, contacted: false, stopped: st(5) },
  ]);
  assert.deepEqual(p, { met: 4, stopped: 2, partly: 1, through: 2, requestsStopped: 6, stoppedServices: ['a', 'd'] });
});
