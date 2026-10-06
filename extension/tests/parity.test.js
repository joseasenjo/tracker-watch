// Parity with the Python engine: every case in fixtures/parity.json was answered by traceguard
// (tools/build_data.py); the extension's core must give exactly the same answers.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildSuffixTrie, registrableDomain } from '../src/core/psl.js';
import { createTrackerList, lookup } from '../src/core/classify.js';
import { bandFor } from '../src/core/bands.js';
import { phrases } from '../src/core/activity.js';
import { onRequest, onSent, redirectPage, startPage } from '../src/core/page.js';
import { summarizePage } from '../src/core/report.js';

const load = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const parity = load('./fixtures/parity.json');
const trie = buildSuffixTrie(load('../data/psl.json').rules);
const list = createTrackerList(load('../data/trackers.json'));
const glossary = load('../data/glossary.json');

test('fixture is not empty', () => {
  assert.ok(parity.cases.length > 1000);
});

test('registrable domain matches the engine for every host', () => {
  const wrong = parity.cases.filter((c) => registrableDomain(trie, c.host) !== c.registrable)
    .map((c) => `${c.host}: js=${registrableDomain(trie, c.host)} py=${c.registrable}`);
  assert.deepEqual(wrong, []);
});

test('tracker lookup matches the engine for every host', () => {
  const wrong = [];
  for (const c of parity.cases) {
    const m = lookup(list, c.host);
    const got = m && { service: m.service, entity: m.entity, category: m.category };
    if (JSON.stringify(got) !== JSON.stringify(c.lookup && { service: c.lookup.service, entity: c.lookup.entity,
      category: c.lookup.category })) wrong.push(`${c.host}: js=${JSON.stringify(got)} py=${JSON.stringify(c.lookup)}`);
  }
  assert.deepEqual(wrong, []);
});

test('bands match the engine', () => {
  for (const c of parity.bands) assert.equal(bandFor(c.count, glossary.bands), c.band, `count ${c.count}`);
});

test('behaviour phrases match the engine word for word', () => {
  for (const c of parity.phrases) assert.deepEqual(phrases(c.activity, glossary), c.phrases);
});

test('replaying every measured pass gives the engine counts', () => {
  const ctx = { trie, list };
  assert.ok(parity.replays.length >= 20);
  for (const r of parity.replays) {
    let page = startPage({ url: r.url, now: 0, declared: r.first_party_domains }, ctx);
    if (r.final_url) page = redirectPage(page, r.final_url, ctx);
    r.requests.forEach(([host, type], i) => {
      onRequest(page, { requestId: String(i), url: `https://${host}/`, type, now: 1 }, ctx);
      onSent(page, { requestId: String(i) });
    });
    const s = summarizePage(page, glossary);
    const services = s.operators.flatMap((o) => o.services).filter((x) => x.tracking && x.before > 0)
      .map((x) => x.service).sort();
    assert.deepEqual(services, r.expected.tracking_services, r.site);
    assert.equal(s.trackingBefore, r.expected.tracking_services.length, r.site);
    assert.equal(s.thirdPartyRequests, r.expected.third_party_requests, r.site);
    assert.equal(s.thirdPartyDomains, r.expected.third_party_domains, r.site);
    for (const [service, kinds] of Object.entries(r.expected.kinds)) {
      for (const [kind, n] of Object.entries(kinds)) assert.equal(page.services[service].before[kind], n, `${r.site} ${service} ${kind}`);
    }
  }
});
