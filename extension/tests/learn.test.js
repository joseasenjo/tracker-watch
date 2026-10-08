// Blocker step 4: learning trackers that are on no list from what they do.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { THRESHOLD, idLikeCookie, learnView, learnedDomains, normalizeLearn, noteSignal, siteHash } from '../src/core/learn.js';

const fresh = (enabled = true) => normalizeLearn({ enabled }, () => '0123456789abcdef0123456789abcdef');

test('identifier-like cookies, not settings or words', () => {
  assert.equal(idLikeCookie('uid=8f3a9c2e1b7d4a60'), true);
  assert.equal(idLikeCookie('lang=en; consent=true; theme=dark-mode'), false);
  assert.equal(idLikeCookie('visitor=1696712345123456'), true);
  assert.equal(idLikeCookie('pref=accepted_all_cookies'), false);
  assert.equal(idLikeCookie('lastvisit=1696712345123'), false); // a date in milliseconds
  assert.equal(idLikeCookie(undefined), false);
});

test('learned after the same behaviour on three different sites, never by name', () => {
  const s = fresh();
  assert.equal(noteSignal(s, 'tracky.io', 'news-a.com', 'cookie', 1), false);
  assert.equal(noteSignal(s, 'tracky.io', 'news-a.com', 'cookie', 2), false); // same site again
  assert.equal(noteSignal(s, 'tracky.io', 'news-b.com', 'canvas', 3), false);
  assert.ok(!JSON.stringify(s).includes('news-'));
  assert.deepEqual(s.domains['tracky.io'].sites, [siteHash(s.salt, 'news-a.com'), siteHash(s.salt, 'news-b.com')]);
  assert.equal(noteSignal(s, 'tracky.io', 'news-c.com', 'cookie', 4), true);
  assert.deepEqual(learnedDomains(s), ['tracky.io']);
  assert.deepEqual(s.domains['tracky.io'].sites, []);
  assert.deepEqual(s.domains['tracky.io'].signals, { cookie: 3, canvas: 1 });
  assert.equal(noteSignal(s, 'tracky.io', 'news-d.com', 'cookie', 5), false); // already learned
  assert.equal(THRESHOLD, 3);
});

test('off, its own site and needed services are never learned', () => {
  const off = fresh(false);
  for (const site of ['a.com', 'b.com', 'c.com']) noteSignal(off, 'tracky.io', site, 'cookie', 1);
  assert.deepEqual(learnedDomains(off), []);
  const s = fresh();
  for (const site of ['a.com', 'b.com', 'c.com']) {
    noteSignal(s, 'jsdelivr.net', site, 'cookie', 1);
    noteSignal(s, site, site, 'cookie', 1);
  }
  assert.deepEqual(Object.keys(s.domains), []);
});

test('stored data is repaired; the view counts learned and watched', () => {
  const s = normalizeLearn({ enabled: true, salt: 'x'.repeat(20), domains: {
    'ok.io': { sites: [], learned: true, signals: { cookie: 4, bogus: 2 }, first: 1, last: 9 },
    'watch.io': { sites: ['0000abcd', 'not-a-hash'], learned: false, signals: {}, first: 1, last: 2 },
    'BAD DOMAIN': { learned: true },
  } });
  assert.deepEqual(Object.keys(s.domains).sort(), ['ok.io', 'watch.io']);
  assert.deepEqual(s.domains['watch.io'].sites, ['0000abcd']);
  const v = learnView(s);
  assert.deepEqual(v.learned, [{ domain: 'ok.io', signals: { cookie: 4 }, first: 1, last: 9 }]);
  assert.equal(v.watching, 1);
});
