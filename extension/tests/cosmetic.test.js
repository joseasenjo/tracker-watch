// Ads: which EasyList element hiding selectors apply on a host, and the style sheet built from them.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { hostPlan, selectorsFor, styleSheet } from '../src/core/cosmetic.js';

const data = {
  generic: ['.ad-banner', '#sponsored'],
  sites: { 'example.com': ['.promo-slot'], 'news.example.com': ['.news-ad'] },
  exceptions: { 'shop.example.com': ['.ad-banner', '.promo-slot'] },
  generichide: ['quiet.org'],
  elemhide: ['clean.net'],
};

test('generic selectors, plus those of the host and its parent domains', () => {
  assert.deepEqual(selectorsFor(data, 'www.other.com'), ['.ad-banner', '#sponsored']);
  assert.deepEqual(selectorsFor(data, 'news.example.com'), ['.ad-banner', '#sponsored', '.news-ad', '.promo-slot']);
});

test('exceptions, $generichide and $elemhide', () => {
  assert.deepEqual(selectorsFor(data, 'shop.example.com'), ['#sponsored']);
  assert.deepEqual(selectorsFor(data, 'www.quiet.org'), []);
  assert.deepEqual(selectorsFor(data, 'clean.net'), []);
});

test('plan: the shared generic sheet plus the own selectors, or everything for hosts with exceptions', () => {
  assert.deepEqual(hostPlan(data, 'news.example.com'), { generic: true, own: ['.news-ad', '.promo-slot'] });
  assert.deepEqual(hostPlan(data, 'other.com'), { generic: true, own: [] });
  assert.deepEqual(hostPlan(data, 'shop.example.com'), { generic: false, own: ['#sponsored'] });
  assert.deepEqual(hostPlan(data, 'clean.net'), { generic: false, own: [] });
});

test('style sheet: forgiving :is() groups that hide', () => {
  assert.equal(styleSheet(['.a', '#b']), ':is(.a,\n#b){display:none!important}');
  assert.equal(styleSheet([]), '');
  const many = Array.from({ length: 450 }, (_, i) => `.x${i}`);
  assert.equal(styleSheet(many).split('{display:none!important}').length - 1, 3);
});
