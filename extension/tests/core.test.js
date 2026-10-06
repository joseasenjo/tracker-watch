// Unit tests of the pure core (no browser APIs).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildSuffixTrie, registrableDomain } from '../src/core/psl.js';
import { createTrackerList, firstPartySet, isTracking, lookup, party } from '../src/core/classify.js';
import { bandFor } from '../src/core/bands.js';

const trie = buildSuffixTrie(['com', 'uk', 'co.uk', 'jp', 'kawasaki.jp', '*.kawasaki.jp', '!city.kawasaki.jp', '*.ck', '!www.ck']);
const list = createTrackerList({
  tracking_categories: ['advertising', 'analytics'],
  domains: {
    'doubleclick.net': { entity: 'Google', category: 'advertising', verified: true },
    'g.doubleclick.net': { entity: 'Google', category: 'analytics', verified: false },
    'googletagmanager.com': { entity: 'Google', category: 'tag_manager', verified: true },
  },
});

test('registrable domain: plain, multi-label suffix, case and dots', () => {
  assert.equal(registrableDomain(trie, 'www.example.com'), 'example.com');
  assert.equal(registrableDomain(trie, 'news.bbc.co.uk'), 'bbc.co.uk');
  assert.equal(registrableDomain(trie, 'WWW.BBC.CO.UK.'), 'bbc.co.uk');
});

test('registrable domain: wildcard and exception rules', () => {
  assert.equal(registrableDomain(trie, 'a.b.foo.ck'), 'b.foo.ck');
  assert.equal(registrableDomain(trie, 'foo.ck'), 'foo.ck'); // a public suffix itself: unchanged
  assert.equal(registrableDomain(trie, 'a.www.ck'), 'www.ck');
  assert.equal(registrableDomain(trie, 'x.city.kawasaki.jp'), 'city.kawasaki.jp');
});

test('registrable domain: no known suffix, IPs and empty input are returned unchanged', () => {
  assert.equal(registrableDomain(trie, 'localhost'), 'localhost');
  assert.equal(registrableDomain(trie, 'a.b.site.test'), 'a.b.site.test');
  assert.equal(registrableDomain(trie, '127.0.0.1'), '127.0.0.1');
  assert.equal(registrableDomain(trie, '[::1]'), '[::1]');
  assert.equal(registrableDomain(trie, ''), '');
});

test('lookup: longest suffix wins, no partial-label matches', () => {
  assert.equal(lookup(list, 'stats.g.doubleclick.net').service, 'g.doubleclick.net');
  assert.equal(lookup(list, 'ad.doubleclick.net').service, 'doubleclick.net');
  assert.equal(lookup(list, 'doubleclick.net').category, 'advertising');
  assert.equal(lookup(list, 'notdoubleclick.net'), null);
  assert.equal(lookup(list, 'doubleclick.net.evil.example'), null);
  assert.equal(lookup(list, 'net'), null);
});

test('lookup keeps the verified flag', () => {
  assert.equal(lookup(list, 'stats.g.doubleclick.net').verified, false);
});

test('tag managers are listed but not tracking', () => {
  assert.equal(isTracking(list, lookup(list, 'www.googletagmanager.com').category), false);
  assert.equal(isTracking(list, 'advertising'), true);
});

test('party: page host and declared first-party domains', () => {
  const fp = firstPartySet(trie, 'www.bbc.co.uk', ['bbci.co.uk']);
  assert.equal(party(trie, fp, 'static.bbci.co.uk'), 'first');
  assert.equal(party(trie, fp, 'news.bbc.co.uk'), 'first');
  assert.equal(party(trie, fp, 'ad.doubleclick.net'), 'third');
});

test('bands: ranges, open end and invalid counts', () => {
  const bands = [{ label: 'A', low: 0, high: 2 }, { label: 'B', low: 3, high: 9 }, { label: 'E', low: 50, high: null }];
  assert.equal(bandFor(0, bands), 'A');
  assert.equal(bandFor(3, bands), 'B');
  assert.equal(bandFor(500, bands), 'E');
  assert.equal(bandFor(20, bands), null);
  assert.equal(bandFor(null, bands), null);
  assert.equal(bandFor(-1, bands), null);
  assert.equal(bandFor(2.5, bands), null);
});
