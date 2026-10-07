// Script behaviours (spec B): attribution to listed services, other third parties apart, first party ignored.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildSuffixTrie } from '../src/core/psl.js';
import { createTrackerList } from '../src/core/classify.js';
import { noteBehaviour, onRequest, onSent, startPage } from '../src/core/page.js';
import { summarizePage } from '../src/core/report.js';

const load = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const glossary = load('../data/glossary.json');
const ctx = { trie: buildSuffixTrie(load('../data/psl.json').rules), list: createTrackerList({
  tracking_categories: ['advertising'],
  domains: { 'adnxs.com': { entity: 'Microsoft (Xandr)', category: 'advertising', verified: true } },
}) };

test('behaviours go to the listed service, other third parties apart, first party ignored', () => {
  const page = startPage({ url: 'https://www.news.es/', now: 0, requestId: '1', declared: [], arrival: null }, ctx);
  onRequest(page, { requestId: '2', url: 'https://ib.adnxs.com/ut.js', type: 'script', now: 1 }, ctx);
  onSent(page, { requestId: '2', headers: [] }, ctx);
  noteBehaviour(page, { kind: 'canvas_read', host: 'ib.adnxs.com' }, ctx);
  noteBehaviour(page, { kind: 'canvas_read', host: 'ib.adnxs.com' }, ctx);
  noteBehaviour(page, { kind: 'webrtc_connection', host: 'cdn.other.io' }, ctx);
  noteBehaviour(page, { kind: 'canvas_read', host: 'static.news.es' }, ctx);
  noteBehaviour(page, { kind: 'key_listener', host: 'cdn.other.io' }, ctx);
  noteBehaviour(page, { kind: 'canvas_read', host: '' }, ctx);
  noteBehaviour(page, { kind: 'canvas_read', host: '__proto__' }, ctx);
  assert.deepEqual(page.behaviours, { 'adnxs.com': ['canvas_read'] });
  assert.deepEqual(page.behavioursOther, { 'other.io': ['webrtc_connection'] });
  const s = summarizePage(page, glossary, { behaviours: page.behaviours, behavioursOther: page.behavioursOther });
  const svc = s.operators[0].services[0];
  assert.ok(svc.phrases.includes('a script from this domain read image data from a canvas element'));
  assert.equal(s.behaviourServices, 1);
  assert.deepEqual(s.behavioursOther, [{ domain: 'other.io', kinds: ['webrtc_connection'] }]);
});
