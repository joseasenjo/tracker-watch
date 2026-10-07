// F13 core: what a search engine collects in your browser before you reach a site.
// Only what happens in the browser is shown (cookies it keeps, parameters in the search address, how a
// click on a result is recorded). What the engine stores on its servers cannot be seen from here; the
// panel points to the engine's own account tools instead. Cookie purposes come from each engine's public
// cookie page (profiles/search_engines.json, with source and date); behaviour is described as observed.

/**
 * @typedef {{ id: string, name: string, hosts: string, results_paths: string[], redirect_paths: string[],
 *   query_param: string, cookie_source: string | null, cookies: Record<string, string>,
 *   server: ServerGuide }} EngineProfile
 * @typedef {{ sources: string[], signed_in_cookie: string | null, signed_out: string, signed_in: string | null,
 *   steps: Array<{ label: string, url: string, what: string }> }} ServerGuide
 *   What the engine says it keeps on its servers, paraphrased from its own pages (Lens cannot check it), and
 *   where to see, delete or request it.
 * @typedef {EngineProfile & { re: RegExp }} Engine
 */

/**
 * @param {{ engines: EngineProfile[] }} profiles
 * @returns {Engine[]}
 */
export function compileEngines(profiles) {
  return profiles.engines.map((e) => ({ ...e, re: new RegExp(e.hosts) }));
}

/**
 * @param {Engine[]} engines
 * @param {string} host
 * @returns {Engine | null}
 */
export function engineForHost(engines, host) {
  const h = String(host || '').toLowerCase();
  return engines.find((e) => e.re.test(h)) ?? null;
}

/**
 * Is this URL a results page or a click redirect of a known engine?
 * @param {Engine[]} engines
 * @param {string} url
 * @returns {{ engine: Engine, kind: 'results' | 'redirect' } | null}
 */
export function classifyEngineUrl(engines, url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  const engine = engineForHost(engines, u.hostname);
  if (!engine) return null;
  if (engine.redirect_paths.some((p) => u.pathname === p || (p.endsWith('/') && u.pathname.startsWith(p)))) {
    return { engine, kind: 'redirect' };
  }
  if (engine.results_paths.includes(u.pathname) && u.searchParams.has(engine.query_param)) return { engine, kind: 'results' };
  return null;
}

/**
 * Names (never values) of the parameters in a search address, with the search terms marked.
 * @param {Engine} engine
 * @param {string} url
 * @returns {Array<{ name: string, isQuery: boolean }>}
 */
export function searchParams(engine, url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return [];
  }
  const names = [...new Set(u.searchParams.keys())].slice(0, 40);
  return names.map((name) => ({ name: name.slice(0, 40), isQuery: name === engine.query_param }));
}

/**
 * Signed in to the engine's account in this browser? Only when the profile names the sign-in cookie that
 * the engine documents (Google: SID, "signing in and security"); otherwise unknown (null).
 * @param {Engine} engine
 * @param {Array<{ name: string }>} cookies  from engineCookies
 * @returns {boolean | null}
 */
export function signedIn(engine, cookies) {
  const name = engine.server && engine.server.signed_in_cookie;
  if (!name) return null;
  return cookies.some((c) => c.name === name);
}

/**
 * The engine's cookies in this browser, with the purpose its cookie page gives (or null when the page
 * does not describe that name). Values are never read.
 * @param {Engine} engine
 * @param {Array<{ name: string, domain: string, session: boolean, expirationDate?: number }>} cookies
 * @param {number} nowMs
 */
export function engineCookies(engine, cookies, nowMs) {
  const out = [];
  const seen = new Set();
  for (const c of cookies) {
    const domain = String(c.domain || '').replace(/^\./, '');
    if (!engine.re.test(domain) && !engine.re.test('www.' + domain)) continue;
    if (seen.has(c.name)) continue;
    seen.add(c.name);
    const days = !c.session && c.expirationDate ? Math.max(0, Math.round((c.expirationDate * 1000 - nowMs) / 86400000)) : null;
    const purpose = Object.prototype.hasOwnProperty.call(engine.cookies, c.name) ? engine.cookies[c.name] : null;
    out.push({ name: c.name, days, session: Boolean(c.session), purpose });
  }
  return out.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}
