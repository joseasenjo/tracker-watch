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

import { ACTIONS } from '../../site_src/assets/browser-check/actions.js';
import { compareAddresses, parseCandidate, parseTrace, runConnectionTest } from '../../site_src/assets/browser-check/connection-test.js';
import { compareReports, parseReport } from '../../site_src/assets/browser-check/util.js';

test('every catalog entry has advice, and no advice is left without an entry', () => {
  for (const entry of CATALOG) assert.ok(ACTIONS[entry.id] && ACTIONS[entry.id].length > 20, entry.id);
  const ids = new Set(CATALOG.map((e) => e.id));
  for (const key of Object.keys(ACTIONS)) assert.ok(ids.has(key), key);
});

test('parseTrace reads key=value lines', () => {
  const t = parseTrace('fl=1\nip=203.0.113.7\nloc=ES\ntls=TLSv1.3\nhttp=http/2\n');
  assert.deepEqual([t.ip, t.loc, t.tls, t.http], ['203.0.113.7', 'ES', 'TLSv1.3', 'http/2']);
});

test('parseCandidate takes the type and the address, and ignores junk', () => {
  assert.deepEqual(parseCandidate('candidate:84 1 udp 16777 203.0.113.7 54400 typ srflx raddr 0.0.0.0 rport 0'),
    { type: 'srflx', address: '203.0.113.7' });
  assert.deepEqual(parseCandidate('candidate:1 1 udp 2113937151 abcd-1234.local 5000 typ host generation 0'),
    { type: 'host', address: 'abcd-1234.local' });
  assert.equal(parseCandidate('nonsense'), null);
});

test('compareAddresses tells same, different and none, and hides mDNS names from the exposed list', () => {
  const host = { type: 'host', address: 'abcd.local' };
  assert.equal(compareAddresses('1.1.1.1', [host, { type: 'srflx', address: '1.1.1.1' }]).relation, 'same');
  assert.equal(compareAddresses('1.1.1.1', [{ type: 'srflx', address: '2.2.2.2' }]).relation, 'different');
  const none = compareAddresses('1.1.1.1', [host]);
  assert.equal(none.relation, 'none');
  assert.equal(none.hiddenLocal, 1);
  assert.deepEqual(none.exposedLocal, []);
  assert.deepEqual(compareAddresses('1.1.1.1', [{ type: 'host', address: '192.168.1.5' }]).exposedLocal, ['192.168.1.5']);
});

test('the connection test still reports WebRTC when the trace page fails, and works without WebRTC', async () => {
  const failing = async () => { throw new TypeError('offline'); };
  const result = await runConnectionTest({ fetchFn: failing, RTC: undefined });
  assert.equal(result.trace, null);
  assert.equal(result.rtcAvailable, false);
  const ok = await runConnectionTest({ fetchFn: async () => ({ text: async () => 'ip=1.2.3.4\nloc=ES\n' }), RTC: undefined });
  assert.equal(ok.trace.ip, '1.2.3.4');
});

test('a report can be read back and compared, with multi-line values kept on one line', () => {
  const results = [{ title: 'Platform', value: 'Win32' }, { title: 'User agent', value: 'Mozilla\nClient hints: x' }];
  const text = reportText(results, '2026-10-13', 'abc123');
  assert.match(text, /Combined code: abc123/);
  assert.equal(parseReport(text).get('User agent'), 'Mozilla / Client hints: x');
  const other = reportText([{ title: 'Platform', value: 'Linux' }, { title: 'User agent', value: 'Mozilla\nClient hints: x' }], '2026-10-13', 'zzz999');
  const diff = compareReports(text, other);
  assert.equal(diff.same, 1);
  assert.deepEqual(diff.different.map((r) => r.title).sort(), ['Combined code', 'Platform']);
});

import { timezoneExample } from '../../site_src/assets/browser-check/util.js';

test('timezoneExample puts the local clock next to universal time and states the difference', () => {
  const text = timezoneExample(new Date(Date.UTC(2026, 0, 15, 12, 0)), 'UTC');
  assert.match(text, /Your clock now: 12:00\. Universal time: 12:00\./);
  assert.match(text, /Difference: [+-]\d+(:\d\d)? hours\./);
});

test('extra report lines with a comma in the title are read back and compared', () => {
  const a = reportText([{ title: 'Tracker test, servers stopped', value: '7 of 7' }], 'd', 'x');
  const b = reportText([{ title: 'Tracker test, servers stopped', value: '0 of 7' }], 'd', 'x');
  const diff = compareReports(a, b);
  assert.deepEqual(diff.different.map((r) => [r.title, r.a, r.b]), [['Tracker test, servers stopped', '7 of 7', '0 of 7']]);
});
