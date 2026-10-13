import test from 'node:test';
import assert from 'node:assert/strict';
import { CATALOG, GROUPS, WEIGHTS } from '../../site_src/assets/browser-check/catalog.js';
import { combinedCode, detectFonts, hashText, plural, reportText, shorten } from '../../site_src/assets/browser-check/util.js';

test('hashText is repeatable and sensitive to every character', async () => {
  assert.equal(await hashText('abc'), await hashText('abc'));
  assert.notEqual(await hashText('abc'), await hashText('abd'));
  assert.equal((await hashText('abc')).length, 64);
});

test('hashText falls back to a simple hash without crypto.subtle', async () => {
  const code = await hashText('abc', null);
  assert.match(code, /^[0-9a-f]{8}$/);
  assert.equal(code, await hashText('abc', null));
});

test('detectFonts keeps a font only when its width differs from the base', () => {
  const widths = { monospace: 100, 'sans-serif': 90, serif: 80 };
  const installed = new Set(['Verdana']);
  const measure = (font, base) => {
    const match = /^"(.+)", /.exec(font);
    return match && installed.has(match[1]) ? widths[base] + 7 : widths[base];
  };
  assert.deepEqual(detectFonts(['Verdana', 'Nope'], ['monospace', 'sans-serif', 'serif'], measure), ['Verdana']);
});

test('combinedCode ignores values marked as unstable and changes with the stable ones', async () => {
  const a = [{ id: 'x', value: '1' }, { id: 'noise', value: 'r1', stable: false }];
  const b = [{ id: 'x', value: '1' }, { id: 'noise', value: 'r2', stable: false }];
  const c = [{ id: 'x', value: '2' }];
  assert.equal(await combinedCode(a), await combinedCode(b));
  assert.notEqual(await combinedCode(a), await combinedCode(c));
});

test('reportText has one line per detail and says nothing was sent', () => {
  const text = reportText([{ title: 'Platform', value: 'Win32' }], '2026-10-13');
  assert.match(text, /nothing was sent anywhere/);
  assert.match(text, /Platform: Win32/);
});

test('shorten and plural', () => {
  assert.equal(shorten('abcdef', 3), 'abc');
  assert.equal(plural(1, 'detail', 'details'), '1 detail');
  assert.equal(plural(2, 'detail', 'details'), '2 details');
});

test('every catalog entry is complete and belongs to a known group and weight', () => {
  const groups = new Set(GROUPS.map((g) => g.id));
  const ids = new Set();
  for (const entry of CATALOG) {
    assert.ok(groups.has(entry.group), entry.id);
    assert.ok(WEIGHTS[entry.weight], entry.id);
    assert.ok(entry.what.length > 30 && entry.how.length > 30, entry.id);
    assert.ok(!ids.has(entry.id), `duplicate ${entry.id}`);
    ids.add(entry.id);
  }
});
