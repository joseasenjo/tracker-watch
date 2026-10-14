import test from 'node:test';
import assert from 'node:assert/strict';
import { SUMMARY_NOTE, coherence, countryOfZone, summarise } from '../../site_src/assets/browser-check/summary.js';
import { combine, preloadProbe, rowText } from '../../site_src/assets/browser-check/tracker-test.js';
import { parseReport, reportText } from '../../site_src/assets/browser-check/util.js';

const READ = {
  fonts: { value: '30 of 59 tested: Arial' }, canvas: { value: 'abc…', raw: 'abc', randomised: false },
  'webgl-renderer': { value: 'Intel' }, webgl: { value: 'x…' }, 'privacy-signals': { value: 'off', on: false },
  'ad-filter': { value: 'Not hidden', hidden: false }, timezone: { value: 'Europe/Madrid', zone: 'Europe/Madrid' },
};

test('combine says stopped if either method was stopped, answered if either was answered', () => {
  assert.equal(combine('blocked', 'reachable'), 'blocked');
  assert.equal(combine('reachable', 'blocked'), 'blocked');
  assert.equal(combine('reachable', 'reachable'), 'reachable');
  assert.equal(combine('reachable', 'timeout'), 'reachable');
  assert.equal(combine('timeout', 'timeout'), 'timeout');
});

test('rowText explains when only one of the two methods was stopped', () => {
  assert.equal(rowText({ state: 'reachable' }), 'got an answer');
  assert.equal(rowText({ state: 'blocked', byFetch: 'blocked', byScript: 'blocked' }), 'stopped');
  assert.equal(rowText({ state: 'blocked', byFetch: 'blocked', byScript: 'timeout' }), 'stopped');
  assert.match(rowText({ state: 'blocked', byFetch: 'blocked', byScript: 'reachable' }), /not as a script/);
  assert.match(rowText({ state: 'blocked', byFetch: 'reachable', byScript: 'blocked' }), /not as a background request/);
});

function fakeDocument(outcome) {
  const head = {
    appendChild(link) {
      setTimeout(() => (outcome === 'load' ? link.onload() : outcome === 'error' ? link.onerror() : null), 0);
    },
  };
  return { head, createElement: () => ({ remove() {} }) };
}

test('preloadProbe reads the load and error events and times out when neither comes', async () => {
  assert.equal(await preloadProbe('https://a.test/x.js', fakeDocument('load')), 'reachable');
  assert.equal(await preloadProbe('https://a.test/x.js', fakeDocument('error')), 'blocked');
  assert.equal(await preloadProbe('https://a.test/x.js', fakeDocument('none'), 30), 'timeout');
});

test('the second method is a script preload without referrer, never a script element', async () => {
  let link;
  const doc = { head: { appendChild(l) { link = l; setTimeout(() => l.onload(), 0); } }, createElement: () => ({ remove() {} }) };
  await preloadProbe('https://a.test/x.js', doc);
  assert.equal(link.rel, 'preload');
  assert.equal(link.as, 'script');
  assert.equal(link.referrerPolicy, 'no-referrer');
});

test('the summary lists observations and says what was not checked', () => {
  const s = summarise({ by: READ, language: 'es-ES' });
  const all = [...s.observed, ...s.worth, ...s.change.map((c) => c.text)].join(' ');
  assert.match(all, /tracker test has not been run/);
  assert.match(all, /connection check has not been run/);
  assert.doesNotMatch(all, /\b(safe|secure|dangerous|at risk|vulnerable)\b/i);
  assert.ok(s.change.length >= 1 && s.change.length <= 3);
  assert.match(SUMMARY_NOTE, /not a security audit and not a verdict/);
});

test('the summary reports the tracker test and the connection check with their limits', () => {
  const tracker = { ok: true, rows: [{ state: 'blocked' }, { state: 'reachable' }, { state: 'blocked' }] };
  const connection = {
    trace: { tls: 'TLSv1.3', http: 'http/2', loc: 'ES', ip: '1.2.3.4' },
    rtc: { relation: 'different', exposedLocal: [] }, rtcAvailable: true,
  };
  const s = summarise({ by: READ, tracker, connection, language: 'es-ES', zone: 'Europe/Madrid' });
  const text = [...s.observed, ...s.worth].join(' ');
  assert.match(text, /requests to 2 of 3 advertising and analytics servers were stopped and 1 got an answer/);
  assert.match(text, /TLSv1\.3 over http\/2/);
  assert.match(text, /The site still sees your address/);
  assert.match(text, /agree \(ES\)/);
  assert.match(text, /WebRTC revealed an address other than the one websites see/);
  assert.ok(s.change.some((c) => /WebRTC/.test(c.text)));
});

test('coherence only speaks when it knows, and says different without accusing', () => {
  assert.equal(countryOfZone('Europe/Madrid'), 'ES');
  assert.equal(countryOfZone('America/New_York'), null);
  assert.equal(coherence({ zone: 'Europe/Madrid', addressCountry: 'ES', language: 'es-ES' }).state, 'consistent');
  assert.equal(coherence({ zone: 'Europe/Madrid', addressCountry: 'DE', language: 'es-ES' }).state, 'different');
  assert.equal(coherence({ zone: 'America/New_York', addressCountry: 'US', language: 'en' }).state, 'unknown');
  assert.equal(coherence({ zone: 'Europe/Madrid', addressCountry: null, language: 'es-ES' }).state, 'unknown');
  const connection = { trace: { loc: 'DE', tls: 'TLSv1.3', http: 'http/2' }, rtc: { relation: 'same', exposedLocal: [] } };
  const s = summarise({ by: READ, connection, language: 'es-ES', zone: 'Europe/Madrid' });
  assert.match(s.worth.join(' '), /VPN or travel/);
});

test('a copied report carries the summary but a comparison ignores it', () => {
  const summary = summarise({ by: READ, language: 'es-ES' });
  const text = reportText([{ title: 'Platform', value: 'Win32' }], '2026-10-14', 'abc', summary);
  assert.match(text, /Summary \(observations, not a verdict\):/);
  assert.match(text, /- Observed: /);
  assert.deepEqual([...parseReport(text).keys()].sort(), ['Combined code', 'Platform']);
});

test('the change suggestions make sense on their own and the tracker advice is short', () => {
  const tracker = { ok: true, rows: [{ state: 'reachable' }, { state: 'reachable' }] };
  const s = summarise({ by: READ, tracker, language: 'es-ES' });
  const texts = s.change.map((c) => c.text);
  assert.ok(texts.every((t) => !/^\w+ \w+ small random noise to it/.test(t)), texts.join(' | '));
  assert.ok(texts.some((t) => /^Pages can read a drawing signature/.test(t)));
  assert.ok(texts.some((t) => /^Try a blocker or a browser with tracking protection/.test(t)));
  const worth = s.worth.join(' ');
  assert.match(worth, /Some of those servers got an answer/);
  assert.doesNotMatch(texts.join(' '), /nothing stopped/i);
});

test('the summary tells apart changing noise from altered pixels, and never claims a guarantee', () => {
  const varies = summarise({ by: { ...READ, canvas: { value: 'x', randomised: true, varies: true, integrity: 'exact' } }, language: 'en' });
  assert.match(varies.worth.join(' '), /adds noise to the canvas drawing/);
  const altered = summarise({ by: { ...READ, canvas: { value: 'x', randomised: true, varies: false, integrity: 'altered' } }, language: 'en' });
  assert.match(altered.worth.join(' '), /known colour drawn on a canvas came back different/);
  const none = summarise({ by: READ, language: 'en' });
  assert.match(none.worth.join(' '), /None of the signs we check were seen/);
  assert.match(none.worth.join(' '), /not a guarantee/);
});
