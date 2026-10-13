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

import { TARGETS, probe, runTrackerTest, verdict } from '../../site_src/assets/browser-check/tracker-test.js';

const blockingFetch = (blocked) => async (url) => {
  if ([...blocked].some((host) => url.includes(host))) throw new TypeError('Failed to fetch');
  return {};
};

test('probe says reachable, blocked or timeout and always uses no cookies and no referrer', async () => {
  let seen;
  assert.equal(await probe('https://a.test/x', async (url, init) => { seen = init; return {}; }), 'reachable');
  assert.equal(seen.credentials, 'omit');
  assert.equal(seen.referrerPolicy, 'no-referrer');
  assert.equal(await probe('https://a.test/x', async () => { throw new TypeError('x'); }), 'blocked');
  const abort = Object.assign(new Error('x'), { name: 'AbortError' });
  assert.equal(await probe('https://a.test/x', async () => { throw abort; }), 'timeout');
});

test('verdict is yes only when everything that answered was blocked, and ignores timeouts', () => {
  assert.equal(verdict([{ state: 'blocked' }, { state: 'blocked' }]), 'yes');
  assert.equal(verdict([{ state: 'reachable' }, { state: 'reachable' }]), 'no');
  assert.equal(verdict([{ state: 'blocked' }, { state: 'reachable' }]), 'partly');
  assert.equal(verdict([{ state: 'blocked' }, { state: 'timeout' }]), 'yes');
  assert.equal(verdict([{ state: 'timeout' }]), 'none');
});

test('the test concludes nothing when the visitor cannot even reach this site', async () => {
  const result = await runTrackerTest({ fetchFn: async () => { throw new TypeError('offline'); }, controlUrl: 'https://self.test/' });
  assert.equal(result.ok, false);
});

test('the test reports ads and analytics separately', async () => {
  const adHosts = TARGETS.filter((t) => t.kind === 'ads').map((t) => t.host);
  const result = await runTrackerTest({ fetchFn: blockingFetch(adHosts), controlUrl: 'https://self.test/' });
  assert.equal(result.ok, true);
  assert.equal(result.ads, 'yes');
  assert.equal(result.analytics, 'no');
});

test('every target is an https address on the host it names, with a known kind', () => {
  assert.equal(TARGETS.length, 7);
  for (const t of TARGETS) {
    assert.ok(t.url.startsWith(`https://${t.host}/`), t.id);
    assert.ok(['ads', 'analytics'].includes(t.kind), t.id);
  }
});
