// The optional tracker-blocking test. It runs only when the visitor presses its button, because it is the one thing on
// this page that contacts other servers: it asks for a well-known script from a few advertising and analytics
// companies, in two different ways, and sees whether anything stops the request. The script is only requested, never
// run. Nothing is sent from this page except the requests themselves (no cookies, no referrer), and the answer is
// never stored or reported anywhere.
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

// 'reachable': the request got an answer (any answer, even an error page, even a stand-in file from a blocker).
// 'blocked': the browser or the network refused it before it left. 'timeout': no answer in time, which proves nothing.
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

// The second way: ask the browser to preload the address as a script. It is requested like a script (which some
// blockers treat differently from a background request) but never run. The load and error events tell the result.
export function preloadProbe(url, doc, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const link = doc.createElement('link');
    let done = false;
    const finish = (state) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (link.remove) link.remove();
      resolve(state);
    };
    const timer = setTimeout(() => finish('timeout'), timeoutMs);
    link.rel = 'preload';
    link.as = 'script';
    link.referrerPolicy = 'no-referrer';
    link.onload = () => finish('reachable');
    link.onerror = () => finish('blocked');
    link.href = url;
    doc.head.appendChild(link);
  });
}

// One state from the two methods: stopped if either method was stopped, answered if either got an answer.
export function combine(byFetch, byScript) {
  const states = [byFetch, byScript];
  if (states.includes('blocked')) return 'blocked';
  if (states.includes('reachable')) return 'reachable';
  return 'timeout';
}

// yes: every request that was judged was stopped; no: none was; partly: some. none: nothing could be judged.
export function verdict(rows) {
  const judged = rows.filter((r) => r.state !== 'timeout');
  if (!judged.length) return 'none';
  const blocked = judged.filter((r) => r.state === 'blocked').length;
  return blocked === judged.length ? 'yes' : blocked === 0 ? 'no' : 'partly';
}

// controlUrl is a page of this same site: if even that fails, the connection is down and nothing can be concluded.
export async function runTrackerTest({ fetchFn, preloadFn, controlUrl, targets = TARGETS, timeoutMs = 5000 }) {
  const control = await probe(controlUrl, fetchFn, timeoutMs);
  if (control !== 'reachable') return { ok: false, rows: [], ads: 'none', analytics: 'none' };
  const second = preloadFn || (async () => 'timeout');
  const rows = await Promise.all(targets.map(async (t) => {
    const [byFetch, byScript] = await Promise.all([probe(t.url, fetchFn, timeoutMs), second(t.url, timeoutMs)]);
    return { ...t, byFetch, byScript, state: combine(byFetch, byScript) };
  }));
  return { ok: true, rows, ads: verdict(rows.filter((r) => r.kind === 'ads')),
    analytics: verdict(rows.filter((r) => r.kind === 'analytics')) };
}

export const VERDICT_TEXT = {
  yes: 'Yes: every request was stopped',
  partly: 'Partly: some requests were stopped',
  no: 'No request was stopped',
  none: 'Could not tell',
};

// What to show for one row, including the case where only one of the two methods was stopped.
export function rowText(row) {
  if (row.state === 'reachable') return 'got an answer';
  if (row.state === 'timeout') return 'no answer in time';
  if (row.byFetch === 'blocked' && row.byScript !== 'reachable') return 'stopped';
  if (row.byScript === 'blocked' && row.byFetch !== 'reachable') return 'stopped';
  return row.byFetch === 'blocked' ? 'stopped as a background request, not as a script' : 'stopped as a script, not as a background request';
}
