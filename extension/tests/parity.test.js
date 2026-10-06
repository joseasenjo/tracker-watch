// Parity with the Python engine: every case in fixtures/parity.json was answered by traceguard
// (tools/build_data.py); the extension's core must give exactly the same answers.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildSuffixTrie, registrableDomain } from '../src/core/psl.js';
import { createTrackerList, lookup } from '../src/core/classify.js';
import { bandFor } from '../src/core/bands.js';

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
