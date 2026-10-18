// TEMPORARY visit counter: a Cloudflare Worker with a D1 database. It receives the events sent by
// site_src/assets/analytics.js and returns totals to the local developer dashboard.
//
// What it keeps per event: the day, the hour (UTC), the kind, the section, the page, the website the visitor came from
// (host only), the country that Cloudflare derives from the connection, and either the seconds on the page or the name
// of the clicked button. It does NOT keep the IP address, the user agent, any identifier or any cookie, and it does
// not read or set cookies. Events older than RETENTION_DAYS are deleted.
//
//   POST /e                       an event (text/plain JSON); answers 204
//   GET  /stats?days=14           totals; needs the header  Authorization: Bearer <READ_TOKEN>

export const SECTIONS = ['web', 'addon', 'check'];
export const KINDS = ['view', 'time', 'click', 'act'];
export const RETENTION_DAYS = 90;
const BOT = /bot|crawl|spider|slurp|headless|preview|monitor|lighthouse|facebookexternalhit/i;
const PATH_OK = /^[A-Za-z0-9_\-./]{1,120}$/;
const HOST_OK = /^[a-z0-9.\-]{1,100}$/;
const WORD_OK = /^[a-z0-9\-]{1,30}$/;

// The event the client sent, or null if anything about it is wrong. `section` is checked against the page, so the
// client cannot file a page under another section.
export function parseEvent(raw) {
  let o;
  try {
    o = typeof raw === 'string' ? JSON.parse(raw) : raw;
  } catch (error) {
    return null;
  }
  if (!o || typeof o !== 'object') return null;
  if (!KINDS.includes(o.k) || !SECTIONS.includes(o.s) || typeof o.p !== 'string' || !PATH_OK.test(o.p)) return null;
  if (sectionOfPath(o.p) !== o.s) return null;
  const event = { kind: o.k, section: o.s, path: o.p, ref: '', value: 0, action: '' };
  if (o.k === 'view') {
    const ref = typeof o.r === 'string' ? o.r : 'direct';
    event.ref = ref === 'direct' || ref === 'internal' || HOST_OK.test(ref) ? ref : 'other';
  } else if (o.k === 'time') {
    const d = Number(o.d);
    if (!Number.isFinite(d) || d < 0) return null;
    event.value = Math.min(Math.round(d), 1800);
  } else {
    if (typeof o.x !== 'string' || !WORD_OK.test(o.x)) return null;
    event.action = o.x;
  }
  return event;
}

export function sectionOfPath(path) {
  if (path === 'extension.html' || path === 'extension-privacy.html') return 'addon';
  if (path === 'browser-check.html') return 'check';
  return 'web';
}

export function isBot(userAgent) {
  return !userAgent || BOT.test(userAgent);
}

export function constantTimeEqual(a, b) {
  const x = String(a);
  const y = String(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x.charCodeAt(i) || 0) ^ (y.charCodeAt(i) || 0);
  return diff === 0;
}

const top = (map, limit = 8) => [...map.entries()].map(([name, n]) => ({ name, n })).sort((a, b) => b.n - a.n || a.name.localeCompare(b.name)).slice(0, limit);
const bump = (map, key, n = 1) => map.set(key, (map.get(key) || 0) + n);

// `rows` are the grouped rows of one SQL query: {section, kind, day, path, ref, country, action, n, v}.
// Totals per section: views, how many times the time was reported and the seconds, a count per day, the most
// visited pages, sources, countries and clicked buttons.
export function buildStats(rows, days, today) {
  const out = {};
  for (const section of SECTIONS) {
    out[section] = { views: 0, timed: 0, seconds: 0, by_day: new Map(), pages: new Map(), refs: new Map(), countries: new Map(), clicks: new Map(), acts: new Map() };
  }
  for (const r of rows) {
    const s = out[r.section];
    if (!s) continue;
    if (r.kind === 'view') {
      s.views += r.n;
      bump(s.by_day, r.day, r.n);
      bump(s.pages, r.path, r.n);
      bump(s.refs, r.ref || 'direct', r.n);
      bump(s.countries, r.country || 'XX', r.n);
    } else if (r.kind === 'time') {
      s.timed += r.n;
      s.seconds += r.v || 0;
    } else if (r.kind === 'click') {
      bump(s.clicks, r.action, r.n);
    } else if (r.kind === 'act') {
      bump(s.acts, r.action, r.n);
    }
  }
  const dayList = [];
  const base = new Date(`${today}T00:00:00Z`);
  for (let i = days - 1; i >= 0; i--) dayList.push(new Date(base.getTime() - i * 86400000).toISOString().slice(0, 10));
  const result = {};
  for (const section of SECTIONS) {
    const s = out[section];
    result[section] = {
      views: s.views, timed: s.timed, avg_seconds: s.timed ? Math.round(s.seconds / s.timed) : null,
      by_day: dayList.map((day) => ({ day, n: s.by_day.get(day) || 0 })),
      pages: top(s.pages), referrers: top(s.refs), countries: top(s.countries), clicks: top(s.clicks), actions: top(s.acts),
    };
  }
  return { days, today, sections: result };
}

const cors = (origin) => ({ 'Access-Control-Allow-Origin': origin, 'Vary': 'Origin' });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = env.ALLOWED_ORIGIN || '';

    if (url.pathname === '/e') {
      if (request.method === 'OPTIONS') {
        return new Response(null, { status: 204, headers: { ...cors(origin), 'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': 'content-type' } });
      }
      if (request.method !== 'POST') return new Response(null, { status: 405 });
      if (!origin || request.headers.get('Origin') !== origin) return new Response(null, { status: 403 });
      if (isBot(request.headers.get('User-Agent'))) return new Response(null, { status: 204, headers: cors(origin) });
      const text = await request.text();
      if (text.length > 1000) return new Response(null, { status: 413 });
      const event = parseEvent(text);
      if (!event) return new Response(null, { status: 400 });
      const now = new Date();
      const country = /^[A-Z]{2}$/.test((request.cf && request.cf.country) || '') ? request.cf.country : 'XX';
      await env.DB.prepare('INSERT INTO events (day, hour, kind, section, path, ref, country, value, action) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')
        .bind(now.toISOString().slice(0, 10), now.getUTCHours(), event.kind, event.section, event.path, event.ref, country, event.value, event.action)
        .run();
      if (Math.random() < 0.01) {
        await env.DB.prepare(`DELETE FROM events WHERE day < date('now', ?)`).bind(`-${RETENTION_DAYS} day`).run();
      }
      return new Response(null, { status: 204, headers: cors(origin) });
    }

    if (url.pathname === '/stats' && request.method === 'GET') {
      const auth = request.headers.get('Authorization') || '';
      if (!env.READ_TOKEN || !constantTimeEqual(auth, `Bearer ${env.READ_TOKEN}`)) return new Response('unauthorized', { status: 401 });
      const days = Math.max(1, Math.min(90, Number(url.searchParams.get('days')) || 14));
      const today = new Date().toISOString().slice(0, 10);
      const { results } = await env.DB.prepare(
        `SELECT section, kind, day, path, ref, country, action, COUNT(*) AS n, SUM(value) AS v FROM events
         WHERE day >= date('now', ?) GROUP BY section, kind, day, path, ref, country, action`).bind(`-${days - 1} day`).all();
      return new Response(JSON.stringify({ ...buildStats(results, days, today), generated: new Date().toISOString() }), {
        status: 200, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
    }

    return new Response('not found', { status: 404 });
  },
};
