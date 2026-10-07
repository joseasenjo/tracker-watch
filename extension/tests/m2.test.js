// M2: consent banner labels, "why your number can differ", search engine view (F13 core).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildSuffixTrie } from '../src/core/psl.js';
import { createTrackerList } from '../src/core/classify.js';
import { markInteraction, noteBanner, notePing, noteSerp, onRequest, onSent, startPage } from '../src/core/page.js';
import { summarizePage } from '../src/core/report.js';
import { differenceReasons } from '../src/core/differ.js';
import { classifyEngineUrl, compileEngines, engineCookies, engineForHost, searchParams, signedIn } from '../src/core/search.js';

const load = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const glossary = load('../data/glossary.json');
const engines = compileEngines(load('../profiles/search_engines.json'));
const trie = buildSuffixTrie(load('../data/psl.json').rules);
const ctx = { trie, list: createTrackerList({
  tracking_categories: ['advertising'],
  domains: {
    'doubleclick.net': { entity: 'Google', category: 'advertising', verified: true },
    'cookielaw.org': { entity: 'OneTrust', category: 'consent_management', verified: true },
    'googletagmanager.com': { entity: 'Google', category: 'tag_manager', verified: true },
  },
}) };

function visit(urls, extra = {}) {
  const page = startPage({ url: 'https://www.example.com/', now: 0, requestId: 'nav', ...extra }, ctx);
  urls.forEach((u, i) => {
    onRequest(page, { requestId: String(i), url: u, type: 'script', now: 1 }, ctx);
    onSent(page, { requestId: String(i), headers: u.includes('doubleclick') ? [{ name: 'Cookie', value: 'IDE=x' }] : [] }, ctx);
  });
  return page;
}

test('the consent table travels with the data and has the engine tools', () => {
  const names = glossary.consent_tools.map((c) => c.name);
  assert.ok(names.includes('OneTrust') && names.includes('Didomi'));
  assert.ok(glossary.consent_tools.every((c) => c.banner && c.reject && c.accept));
});

test('banner and click on it are recorded once', () => {
  const page = visit([]);
  noteBanner(page, 'OneTrust');
  noteBanner(page, 'OneTrust');
  markInteraction(page, { now: 5, kind: 'click', on: { tool: 'OneTrust', choice: 'reject' } });
  markInteraction(page, { now: 6, kind: 'click', on: { tool: 'Didomi', choice: 'accept' } });
  assert.deepEqual(page.consent, { banners: ['OneTrust'], click: { tool: 'OneTrust', choice: 'reject' } });
});

test('reasons: facts of this visit, most specific first, auctions always last', () => {
  const page = visit(['https://cdn.cookielaw.org/x.js', 'https://ad.doubleclick.net/x', 'https://www.googletagmanager.com/gtm.js']);
  page.told.self = { language: 'es-ES,es;q=0.9' };
  page.stopped.client = 3;
  const s = summarizePage(page, glossary);
  const baseline = { vantage: 'github-actions-us', date: '2026-10-04' };
  const ids = differenceReasons(s, page, baseline, { browser: 'firefox', nowMs: Date.UTC(2026, 9, 20) });
  const by = Object.fromEntries(ids.map((r) => [r.id, r.args]));
  assert.deepEqual(by.whyExtension, [3]);
  assert.deepEqual(by.whyOtherBrowser, ['Firefox']);
  assert.deepEqual(by.whyAnsweredBefore, ['OneTrust']); // contacted, no banner on screen, no click
  assert.deepEqual(by.whyRecognised, [1]); // the first doubleclick request already carried a cookie
  assert.deepEqual(by.whyTagManagers, ['googletagmanager.com']);
  assert.deepEqual(by.whyPlace, ['github-actions-us', 'es-ES']);
  assert.deepEqual(by.whyAge, [16]);
  assert.equal(ids.at(-1).id, 'whyAuctions');
});

test('reasons: a banner still on screen, or a click, replace the "answered before" guess', () => {
  const page = visit(['https://cdn.cookielaw.org/x.js']);
  noteBanner(page, 'OneTrust');
  let ids = differenceReasons(summarizePage(page, glossary), page, null, { browser: 'chromium', nowMs: 0 }).map((r) => r.id);
  assert.ok(ids.includes('whyBannerWaiting') && !ids.includes('whyAnsweredBefore'));
  markInteraction(page, { now: 9, kind: 'click', on: null });
  ids = differenceReasons(summarizePage(page, glossary), page, null, { browser: 'chromium', nowMs: 0 }).map((r) => r.id);
  assert.ok(ids.includes('whyClicked') && !ids.includes('whyBannerWaiting'));
  assert.ok(!ids.includes('whyPlace')); // no baseline: nothing to compare
});

test('reasons: an answer just before a reload is named, not guessed', () => {
  const page = visit(['https://cdn.cookielaw.org/x.js']);
  page.consent.previous = { tool: 'Didomi', choice: 'accept' };
  const ids = differenceReasons(summarizePage(page, glossary), page, null, { browser: 'chromium', nowMs: 0 }).map((r) => r.id);
  assert.ok(ids.includes('whyAnsweredJustBefore') && !ids.includes('whyAnsweredBefore'));
});

test('search engines: hosts, results pages and click redirects', () => {
  assert.equal(engineForHost(engines, 'www.google.es').id, 'google');
  assert.equal(engineForHost(engines, 'www.google.co.uk').id, 'google');
  assert.equal(engineForHost(engines, 'google.evil.example'), null);
  assert.equal(engineForHost(engines, 'notgoogle.com'), null);
  assert.equal(classifyEngineUrl(engines, 'https://www.google.es/search?q=x&ei=1').kind, 'results');
  assert.equal(classifyEngineUrl(engines, 'https://www.google.es/search'), null); // no search terms
  assert.equal(classifyEngineUrl(engines, 'https://www.google.com/url?q=https://x.example/').kind, 'redirect');
  assert.equal(classifyEngineUrl(engines, 'https://duckduckgo.com/l/?uddg=x').kind, 'redirect');
  assert.equal(classifyEngineUrl(engines, 'https://www.bing.com/search?q=x').engine.id, 'bing');
});

test('search parameters: names only, the query marked', () => {
  const g = engines.find((e) => e.id === 'google');
  assert.deepEqual(searchParams(g, 'https://www.google.com/search?q=private+words&ei=SECRET&ved=1'),
    [{ name: 'q', isQuery: true }, { name: 'ei', isQuery: false }, { name: 'ved', isQuery: false }]);
});

test('engine cookies: documented purposes or "not described", lifetime, no values', () => {
  const g = engines.find((e) => e.id === 'google');
  const now = Date.UTC(2026, 9, 6);
  const list = engineCookies(g, [
    { name: 'NID', domain: '.google.es', session: false, expirationDate: now / 1000 + 180 * 86400, value: 'SECRET' },
    { name: '__Secure-3PSID', domain: '.google.es', session: false, expirationDate: now / 1000 + 400 * 86400 },
    { name: 'IDE', domain: '.doubleclick.net', session: false },
  ], now);
  assert.equal(list.length, 2);
  assert.equal(list.find((c) => c.name === 'NID').days, 180);
  assert.match(list.find((c) => c.name === 'NID').purpose, /preferences/);
  assert.equal(list.find((c) => c.name === '__Secure-3PSID').purpose, null);
  assert.ok(!JSON.stringify(list).includes('SECRET'));
});

test('results page: link structure and pings are recorded', () => {
  const page = startPage({ url: 'https://www.google.com/search?q=x', now: 0, requestId: 'n' }, ctx);
  noteSerp(page, { engine: 'google', params: [{ name: 'q', isQuery: true }], links: { total: 0, ping: 0, redirect: 0, mousedown: 0 } });
  noteSerp(page, { engine: 'google', params: page.search.params, links: { total: 12, ping: 10, redirect: 2, mousedown: -1 } });
  notePing(page, 50);
  assert.deepEqual(page.search.links, { total: 12, ping: 10, redirect: 2, mousedown: 0 });
  assert.equal(page.search.pings, 1);
  assert.equal(page.search.lastPingAt, 50);
});

test('profiles only describe engines from their own documentation, with a source', () => {
  for (const e of engines) {
    if (Object.keys(e.cookies).length) assert.match(e.cookie_source, /^https:\/\//, e.id);
    assert.ok(e.server.sources.length && e.server.sources.every((u) => /^https:\/\//.test(u)), e.id);
    assert.ok(e.server.steps.length && e.server.steps.every((s) => /^https:\/\//.test(s.url) && s.what), e.id);
    // statements are attributed to the company, never asserted by Lens
    assert.match(e.server.signed_out, /says/, e.id);
    if (e.server.signed_in) assert.match(e.server.signed_in, /says/, e.id);
  }
});

test('signed in only from the sign-in cookie the engine documents; unknown otherwise', () => {
  const google = engines.find((e) => e.id === 'google');
  const bing = engines.find((e) => e.id === 'bing');
  assert.equal(signedIn(google, [{ name: 'NID' }]), false);
  assert.equal(signedIn(google, [{ name: 'NID' }, { name: 'SID' }]), true);
  assert.equal(signedIn(bing, [{ name: 'MUID' }]), null);
});
