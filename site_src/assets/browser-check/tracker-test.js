// The optional tracker-blocking test. It runs only when the visitor presses its button, because it is the one thing on
// this page that contacts other servers: it asks for a well-known script from a few advertising and analytics
// companies and sees whether the browser lets the request through. Nothing is sent from this page except the request
// itself (no cookies, no referrer), and the answer is never stored or reported anywhere.
export const TARGETS = [
  { id: 'google-ads', kind: 'ads', owner: 'Google (advertising)', host: 'pagead2.googlesyndication.com',
    url: 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js' },
  { id: 'google-publisher', kind: 'ads', owner: 'Google (ad serving)', host: 'securepubads.g.doubleclick.net',
    url: 'https://securepubads.g.doubleclick.net/tag/js/gpt.js' },
  { id: 'x-ads', kind: 'ads', owner: 'X (advertising)', host: 'static.ads-twitter.com',
    url: 'https://static.ads-twitter.com/uwt.js' },
  { id: 'microsoft-ads', kind: 'ads', owner: 'Microsoft (advertising)', host: 'bat.bing.com',
    url: 'https://bat.bing.com/bat.js' },
  { id: 'google-analytics', kind: 'analytics', owner: 'Google (analytics)', host: 'www.google-analytics.com',
    url: 'https://www.google-analytics.com/analytics.js' },
  { id: 'meta-pixel', kind: 'analytics', owner: 'Meta (tracking pixel)', host: 'connect.facebook.net',
    url: 'https://connect.facebook.net/en_US/fbevents.js' },
  { id: 'comscore', kind: 'analytics', owner: 'Comscore (audience measurement)', host: 'sb.scorecardresearch.com',
    url: 'https://sb.scorecardresearch.com/beacon.js' },
];

// 'reachable': the request got an answer (any answer, even an error page). 'blocked': the browser or the network
// refused it before it left. 'timeout': no answer in time, which proves nothing either way.
export async function probe(url, fetchFn, timeoutMs = 5000) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const timer = setTimeout(() => controller && controller.abort(), timeoutMs);
  try {
    await fetchFn(url, { mode: 'no-cors', cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer',
      signal: controller ? controller.signal : undefined });
    return 'reachable';
  } catch (error) {
    return error && error.name === 'AbortError' ? 'timeout' : 'blocked';
  } finally {
    clearTimeout(timer);
  }
}

// yes: every answered request was blocked; no: none was; partly: some. none: nothing could be judged.
export function verdict(rows) {
  const judged = rows.filter((r) => r.state !== 'timeout');
  if (!judged.length) return 'none';
  const blocked = judged.filter((r) => r.state === 'blocked').length;
  return blocked === judged.length ? 'yes' : blocked === 0 ? 'no' : 'partly';
}

// controlUrl is a page of this same site: if even that fails, the connection is down and nothing can be concluded.
export async function runTrackerTest({ fetchFn, controlUrl, targets = TARGETS, timeoutMs = 5000 }) {
  const control = await probe(controlUrl, fetchFn, timeoutMs);
  if (control !== 'reachable') return { ok: false, rows: [], ads: 'none', analytics: 'none' };
  const rows = await Promise.all(targets.map(async (t) => ({ ...t, state: await probe(t.url, fetchFn, timeoutMs) })));
  return { ok: true, rows, ads: verdict(rows.filter((r) => r.kind === 'ads')),
    analytics: verdict(rows.filter((r) => r.kind === 'analytics')) };
}

export const VERDICT_TEXT = { yes: 'Yes, all blocked', partly: 'Partly', no: 'No, none blocked', none: 'Could not tell' };
