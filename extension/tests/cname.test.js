// Disguised trackers (CNAME cloaking): matching, which hosts are checked, what is kept.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildSuffixTrie } from '../src/core/psl.js';
import { createTrackerList } from '../src/core/classify.js';
import { startPage } from '../src/core/page.js';
import { cnameMatch, noteCloaked, worthResolving } from '../src/core/cname.js';

const load = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const ctx = { trie: buildSuffixTrie(load('../data/psl.json').rules), list: createTrackerList({ tracking_categories: [], domains: {} }) };
const targets = { '2o7.net': 'Adobe Experience Cloud (formerly Omniture)', 'eulerian.net': 'Eulerian' };

test('a canonical name matches its listed target or a parent of it', () => {
  assert.deepEqual(cnameMatch(targets, 'news.d1.sc.omtrdc.2o7.net.'), { target: '2o7.net', company: 'Adobe Experience Cloud (formerly Omniture)' });
  assert.equal(cnameMatch(targets, 'cdn.example.net'), null);
  assert.equal(cnameMatch(targets, 'x2o7.net'), null);
  assert.equal(cnameMatch(targets, ''), null);
});

test('only other hosts of the site itself are checked; the page keeps host and company', () => {
  const page = startPage({ url: 'https://www.news.es/', now: 0, requestId: 'nav', declared: [], arrival: null }, ctx);
  assert.equal(worthResolving(page, 'metrics.news.es', 'news.es'), true);
  assert.equal(worthResolving(page, 'www.news.es', 'news.es'), false);
  assert.equal(worthResolving(page, 'ad.doubleclick.net', 'doubleclick.net'), false);
  noteCloaked(page, 'metrics.news.es', { target: '2o7.net', company: 'Adobe' });
  noteCloaked(page, 'metrics.news.es', { target: 'other', company: 'Other' });
  assert.deepEqual(page.cloaked, { 'metrics.news.es': { target: '2o7.net', company: 'Adobe' } });
});
