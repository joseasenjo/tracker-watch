// "Your own banner test": runs, clean checks, ranges, retention.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { carryTest, currentRun, emptyStore, exportStore, normalizeStore, purge, saveRun, siteView,
  startTest } from '../src/core/mytests.js';

const DAY = 86400000;
const svc = (service, before, after, tracking = true) => ({ service, before, after, tracking, contacted: before + after > 0 });
const summary = (services, client = 0) => ({ operators: [{ entity: 'X', services }], stopped: { client } });
const page = (recognised = {}) => ({ told: { recognised } });

test('a test starts only on reject or accept, with the services seen before', () => {
  const s = summary([svc('A', 1, 0), svc('B', 0, 0), svc('CMP', 2, 0, false)]);
  assert.equal(startTest(s, null, { nowMs: 1 }), null);
  assert.equal(startTest(s, { choice: 'other', tool: null }, { nowMs: 1 }), null);
  const p = startTest(s, { choice: 'reject', tool: 'OneTrust' }, { nowMs: 1000, cleared: true });
  assert.deepEqual(p.beforeServices, ['A']);
  assert.equal(p.cleared, true);
});

test('a run lists services after the answer, the new ones and their cookie names (never values)', () => {
  const p = startTest(summary([svc('A', 1, 0)]), { choice: 'reject', tool: 'OneTrust' }, { nowMs: Date.UTC(2026, 9, 7) });
  const s = summary([svc('A', 1, 2), svc('B', 0, 1), svc('C', 3, 0)]);
  const run = currentRun(p, s, page(), { B: [{ name: '_b', persistent: true, days: 90 }], C: [{ name: '_c' }] }, false);
  assert.deepEqual(run.after, ['A', 'B']);
  assert.deepEqual(run.newAfter, ['B']);
  assert.deepEqual(run.cookies, { B: ['_b'] });
  assert.equal(run.clean, true);
  assert.equal(run.date, '2026-10-07');
  assert.ok(!JSON.stringify(run).includes('90'));
});

test('a blocker makes the run not clean; recognised services are only a note', () => {
  const p = startTest(summary([]), { choice: 'accept', tool: null }, { nowMs: 5 });
  let run = currentRun(p, summary([svc('A', 1, 1)]), page({ A: 1, Z: 1 }), {}, false);
  assert.equal(run.clean, true);
  assert.deepEqual(run.notes, ['A']);
  run = currentRun(p, summary([svc('A', 1, 1)], 2), page(), {}, false);
  assert.equal(run.clean, false);
  assert.deepEqual(run.reasons, ['blocker']);
});

test('after a reload that follows the answer, the whole new page counts as after', () => {
  const p = startTest(summary([svc('A', 1, 0)]), { choice: 'reject', tool: null }, { nowMs: 5 });
  const first = currentRun(p, summary([svc('A', 1, 0), svc('B', 0, 1)]), page(), {}, false);
  const carried = carryTest(p, first);
  const run = currentRun(carried, summary([svc('A', 2, 0), svc('C', 1, 0)]), page(), {}, true);
  assert.deepEqual(run.after, ['A', 'B', 'C']);
  assert.deepEqual(run.newAfter, ['B', 'C']);
  assert.equal(run.id, p.id);
});

test('store keeps the latest three runs per answer, ranges and next step', () => {
  const store = emptyStore();
  const run = (id, at, after, clean = true) => ({ id, at, date: '', tool: null, before: 0, beforeServices: [],
    after, newAfter: after, cookies: {}, clean, reasons: clean ? [] : ['blocker'], notes: [], cleared: false });
  assert.equal(siteView(store, 'x.es').next, 'reject');
  for (let i = 1; i <= 4; i += 1) saveRun(store, 'x.es', 'reject', run('r' + i, i, ['A', 'B'].slice(0, i % 2 + 1)));
  saveRun(store, 'x.es', 'reject', run('r4', 4, ['A', 'B', 'C'])); // same id updates
  assert.deepEqual(store.sites['x.es'].reject.map((r) => r.id), ['r2', 'r3', 'r4']);
  let v = siteView(store, 'x.es');
  assert.deepEqual(v.reject.range, [1, 3]);
  assert.deepEqual(v.reject.inEveryCleanRun, ['A']);
  assert.equal(v.next, 'accept');
  saveRun(store, 'x.es', 'accept', run('a1', 9, ['A', 'D'], false));
  assert.equal(siteView(store, 'x.es').next, 'accept');
  saveRun(store, 'x.es', 'accept', run('a2', 10, ['A', 'C', 'D', 'E']));
  v = siteView(store, 'x.es');
  assert.equal(v.next, 'repeat');
  assert.deepEqual(v.comparison, { reject: 3, accept: 4, both: 2, onlyAccept: 2 });
  saveRun(store, '__proto__', 'reject', run('z', 1, []));
  assert.equal(Object.keys(store.sites).length, 1);
});

test('retention removes old runs and empty sites; settings are repaired', () => {
  const store = normalizeStore({ v: 1, settings: { enabled: true, days: 7 }, sites: { 'x.es': { reject: [{ id: 'a', at: 0, after: [] }], accept: [] } } });
  purge(store, 8 * DAY);
  assert.deepEqual(store.sites, {});
  assert.deepEqual(normalizeStore({ v: 1, settings: { days: 5 } }).settings, { enabled: false, days: 30 });
  assert.deepEqual(normalizeStore(null), emptyStore());
  assert.equal(exportStore(store, 0).format, 'tracker-watch-lens/my-banner-tests');
});
