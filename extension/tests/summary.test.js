// F8 "Your week": daily aggregates by company, never which pages.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { addPage, emptySummary, normalizeSummary, periodView, purgeSummary } from '../src/core/summary.js';

const DAY = 86400000;
const T = Date.UTC(2026, 9, 7, 12);

test('pages and companies per day; a company counts once per page', () => {
  const s = emptySummary();
  addPage(s, ['Google', 'Criteo', 'Google'], T);
  addPage(s, ['Google'], T);
  addPage(s, [], T);
  addPage(s, ['Meta'], T - DAY);
  assert.deepEqual(s.days['2026-10-07'], { pages: 3, operators: { Google: 2, Criteo: 1 } });
  const v = periodView(s, T, 7);
  assert.equal(v.pages, 4);
  assert.deepEqual(v.operators, [{ name: 'Google', pages: 2 }, { name: 'Criteo', pages: 1 }, { name: 'Meta', pages: 1 }]);
  assert.ok(!JSON.stringify(s).includes('.com'));
});

test('old days are dropped; stored values are repaired', () => {
  const s = emptySummary();
  addPage(s, ['Google'], T - 40 * DAY);
  addPage(s, ['Google'], T);
  purgeSummary(s, T);
  assert.deepEqual(Object.keys(s.days), ['2026-10-07']);
  const bad = normalizeSummary({ v: 1, enabled: 'yes', days: { x: {}, '2026-10-07': { pages: -1, operators: { A: 2, B: 'x', ['__proto__']: 3 } } } });
  assert.equal(bad.enabled, false);
  assert.deepEqual(bad.days, { '2026-10-07': { pages: 0, operators: { A: 2 } } });
  assert.deepEqual(normalizeSummary(null), emptySummary());
});
