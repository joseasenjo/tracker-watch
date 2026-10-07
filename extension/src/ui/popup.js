// The panel. Every text from the page or the data goes through textContent: never innerHTML.
const api = globalThis.browser ?? globalThis.chrome;
const t = (key, ...subs) => api.i18n.getMessage(key, subs.map(String)) || key;
const app = document.getElementById('app');
const status = document.getElementById('status');
const hidden = document.getElementById('announce');
/** Short announcements go to live regions, never the whole panel: the headline only for screen readers,
 *  the result of a button (copied) visible too. */
const announce = (text) => { if (hidden.textContent !== text) hidden.textContent = text; };
const notice = (text) => { status.textContent = text; };

/** @param {string} tag @param {string} [text] @param {string} [cls] */
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
const add = (parent, ...nodes) => { for (const n of nodes) parent.appendChild(n); return parent; };

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const category = (id) => t('cat_' + id) || id;

async function currentTab() {
  const q = new URLSearchParams(location.search);
  if (q.has('tabId')) return { id: Number(q.get('tabId')), url: q.get('url') || '' }; // opened as a page (tests)
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  return tab ? { id: tab.id, url: tab.url || '' } : null;
}

function render(tab, data) {
  app.replaceChildren();
  add(app, el('h1', t('extName')));
  const s = data && data.page;
  if (!s || !s.host) {
    add(app, el('p', t('noData'), 'note'));
    app.appendChild(settingsLink());
    return;
  }
  app.appendChild(el('p', s.host, 'host'));
  const head = add(el('div', undefined, 'headline'), el('span', String(s.trackingBefore), 'big'), el('span', t('headline')));
  const chips = add(el('div', undefined, 'chips'), el('span', t('oneVisit'), 'chip'));
  if (s.band) chips.appendChild(el('span', t('band', s.band), 'chip'));
  add(app, head, chips);
  if (data.loading) {
    add(app, add(el('p', undefined, 'loading'), el('span', undefined, 'spinner'), el('span', t('stillLoading'))));
  }
  announce(data.loading ? t('stillLoading') : t('headlineStatus', s.trackingBefore));
  renderArrival(data.arrival);
  const c = data.consent || { banners: [], toolsContacted: [], click: null };
  if (c.previous) {
    app.appendChild(el('p', c.previous.tool
      ? t('reloadedAfterAnswer', c.previous.tool, t('choice_' + c.previous.choice))
      : t('reloadedAfterAnswerUnknown', t('choice_' + c.previous.choice)), 'note'));
  }
  if (c.banners.length) app.appendChild(el('p', t('bannerShown', c.banners.join(', ')), 'note'));
  else if (c.toolsContacted.length) app.appendChild(el('p', t('consentContacted', c.toolsContacted.join(', ')), 'note'));
  if (c.payOrAccept) app.appendChild(el('p', t('payOrAccept'), 'warn'));
  renderSearch(data.search); // on a results page this is the main content

  const dl = el('dl');
  const row = (k, v) => add(dl, el('dt', k), el('dd', v));
  row(t('thirdDomains'), String(s.thirdPartyDomains));
  row(t('thirdRequests'), String(s.thirdPartyRequests));
  row(t('size'), s.bytes.total === null ? t('sizeNone') : (s.bytes.exact ? t('sizeExact', formatBytes(s.bytes.total)) : t('sizeMin', formatBytes(s.bytes.total))));
  app.appendChild(dl);
  if (s.bytes.unknown) app.appendChild(el('p', t('unknownSizes', s.bytes.unknown), 'note'));

  const stopped = [];
  if (s.stopped.client) stopped.push(t('stoppedClient', s.stopped.client));
  if (s.stopped.browser) stopped.push(t('stoppedBrowser', s.stopped.browser));
  if (s.stopped.cancelled) stopped.push(t('stoppedCancelled', s.stopped.cancelled));
  if (stopped.length) {
    add(app, el('h2', t('stoppedTitle')), add(el('ul'), ...stopped.map((x) => el('li', x))));
  }

  app.appendChild(el('h2', t('afterTitle')));
  if (!s.interaction) {
    const btn = el('button', t('markNow'));
    btn.addEventListener('click', async () => {
      await api.runtime.sendMessage({ type: 'mark', tabId: tab.id });
      load();
    });
    add(app, el('p', t('noInteraction'), 'note'), btn);
  } else {
    const click = c.click;
    let text = t('clickPage');
    if (click && click.tool) {
      text = t({ reject: 'clickReject', accept: 'clickAccept', pay: 'clickPay', other: 'clickOther' }[click.choice], click.tool);
    } else if (click) {
      text = t('clickUnknown', t('choice_' + click.choice));
    }
    app.appendChild(el('p', text, 'note'));
    if (s.trackingNewAfter.length) {
      app.appendChild(el('p', t('newAfter', s.trackingNewAfter.length, s.thirdPartyRequestsAfter)));
      app.appendChild(el('p', s.trackingNewAfter.join(', '), 'svc'));
    } else {
      app.appendChild(el('p', t('noneAfter', s.thirdPartyRequestsAfter)));
    }
  }

  renderBlocking(s.blocking);
  renderJourney(data.journey);
  if (!data.search) renderMyTests(tab, data.mytests, Boolean(c.payOrAccept)); // a results page has no banner to test

  renderFrames(s);
  app.appendChild(el('h2', t('operatorsTitle')));
  if (!s.operators.length) app.appendChild(el('p', t('noneContacted'), 'note'));
  const FIRST = 10;
  let more = null;
  s.operators.forEach((op, i) => {
    if (i === FIRST) {
      more = add(el('details'), el('summary', t('moreCompanies', s.operators.length - FIRST), 'svc'));
      app.appendChild(more);
    }
    const box = el('div', undefined, 'op');
    box.appendChild(el('b', op.entity));
    const ul = el('ul');
    for (const svc of op.services) {
      const li = el('li');
      const labels = [category(svc.category)];
      if (!svc.tracking) labels.push(t('notCounted'));
      if (!svc.verified) labels.push(t('unverified'));
      if (svc.tracking && svc.via.length) {
        labels.push(t(s.onlyFromFrames.includes(svc.service) ? 'viaOnlyLabel' : 'viaLabel', svc.via.map((v) => v.site).join(', ')));
      }
      add(li, el('span', svc.service), el('span', ` · ${labels.join(' · ')}`, 'svc'));
      if (svc.phrases.length) li.appendChild(el('div', svc.phrases.join('; '), 'svc'));
      ul.appendChild(li);
    }
    add(more || app, add(box, ul));
  });

  if (s.behaviourServices || s.behavioursOther.length) {
    app.appendChild(el('p', t('behaviourNote'), 'note'));
    if (s.behavioursOther.length) {
      app.appendChild(el('p', t('behaviourOther', s.behavioursOther.length)));
      app.appendChild(add(el('ul'), ...s.behavioursOther.map((o) => el('li',
        `${o.domain}: ${o.kinds.map((k) => t('beh_' + k)).join('; ')}`, 'svc'))));
    }
  }

  renderTold(data.told);

  app.appendChild(el('h2', t('baselineTitle')));
  const b = data.baseline;
  if (b && b.weekly !== null) {
    app.appendChild(el('p', t('baselineText', b.weekly, b.date, b.vantage || '?')));
    app.appendChild(el('p', t('baselineDiff', b.onlyHere.length, b.onlyWeekly.length), 'note'));
  } else {
    app.appendChild(el('p', t('notMeasured'), 'note'));
  }
  app.appendChild(el('h2', t('whyTitle')));
  app.appendChild(add(el('ul'), ...(data.reasons || []).map((r) => el('li', t(r.id, ...r.args)))));
  app.appendChild(el('p', t('honesty'), 'note'));
  renderShare(tab, data);
  if (data.index) app.appendChild(el('p', t('dataVersion', data.index.data_version, data.index.list_entries), 'note'));
  app.appendChild(settingsLink());
  fetch(api.runtime.getURL('data/build.json')).then((r) => r.json())
    .then((b) => app.appendChild(el('p', `build ${b.built}`, 'note')), () => {});
}

const send = (msg) => api.runtime.sendMessage(msg);

function settingsLink() {
  const a = el('a', t('settingsLink'));
  a.href = '#';
  a.addEventListener('click', (e) => { e.preventDefault(); api.runtime.openOptionsPage(); window.close(); });
  return add(el('p', undefined, 'note'), a);
}

/** Firefox lets people withdraw the "all sites" permission; without it Lens sees nothing. */
async function hostAccess() {
  try { return await api.permissions.contains({ origins: ['<all_urls>'] }); } catch { return true; }
}
function button(label, onClick) {
  const b = el('button', label);
  b.addEventListener('click', async () => { b.disabled = true; await onClick(); load(); });
  return b;
}

/** "Your own banner test": opt-in; only counts and service names, kept in this browser. */
function renderMyTests(tab, m, payHere) {
  if (!m) return;
  app.appendChild(el('h2', t('myTitle')));
  if (!m.settings.enabled) {
    add(app, el('p', t('myOff'), 'note'), button(t('myTurnOn'), () => send({ type: 'mytests:settings', enabled: true })));
    return;
  }
  const v = m.view;
  if (m.running) {
    app.appendChild(el('p', t(m.running.choice === 'reject' ? 'myRecordingReject' : 'myRecordingAccept')
      + (m.running.continued ? ' ' + t('myContinued') : ''), 'note'));
  }
  const pay = payHere || v.payOrAccept;
  if (pay) app.appendChild(el('p', t('myPayOrAccept'), 'note'));
  const steps = el('ol', undefined, 'steps');
  const step = (done, text) => add(steps, el('li', (done ? '\u2713 ' : '') + text, done ? 'done' : ''));
  if (!pay) step(v.reject.runs.length > 0, t('myStep1'));
  const li = el('li', t('myStep2'));
  li.appendChild(el('br'));
  li.appendChild(button(t('myClear'), async () => {
    // Chrome may close the panel while it asks for a permission: clear first (cookies always; other site data
    // once the optional permission is granted), then ask, so the next clearing is complete.
    let granted = false;
    try { granted = await api.permissions.contains({ permissions: ['browsingData'] }); } catch { /* no API */ }
    await send({ type: 'mytests:clearSite', tabId: tab.id });
    if (!granted) { try { await api.permissions.request({ permissions: ['browsingData'] }); } catch { /* cookies only */ } }
  }));
  steps.appendChild(li);
  step(v.accept.runs.length > 0, t('myStep3'));
  app.appendChild(steps);
  if (v.next === 'repeat') app.appendChild(el('p', t('myRepeat'), 'note'));

  for (const choice of ['reject', 'accept']) {
    const part = v[choice];
    if (!part.runs.length) continue;
    const last = part.runs[0];
    const box = el('div', undefined, 'op');
    box.appendChild(el('b', t(choice === 'reject' ? 'myAfterReject' : 'myAfterAccept', last.date)));
    box.appendChild(el('div', t('myBefore', last.before, last.after.length, last.newAfter.length)));
    if (!last.clean) box.appendChild(el('div', t('myNotClean'), 'warn'));
    if (last.cleared) box.appendChild(el('div', t('myCleared'), 'svc'));
    if (last.newAfter.length) box.appendChild(fold(t('myNew', last.newAfter.length), last.newAfter.join(', ')));
    if (last.after.length) box.appendChild(fold(t('myAll', last.after.length), last.after.join(', ')));
    const withCookies = Object.entries(last.cookies);
    if (withCookies.length) {
      box.appendChild(el('div', t('myCookies', withCookies.length), 'svc'));
      box.appendChild(add(el('ul'), ...withCookies.map(([svc, names]) => el('li', `${svc}: ${names.join(', ')}`, 'svc'))));
    }
    if (last.notes.length) box.appendChild(el('div', t('myRecognised', last.notes.length), 'svc'));
    if (part.runs.length > 1 && part.range) {
      box.appendChild(el('div', t('myRange', part.runs.length, part.range[0], part.range[1]), 'svc'));
      if (part.cleanRuns > 1) box.appendChild(el('div', t('myEvery', part.inEveryCleanRun.join(', ') || '-'), 'svc'));
    }
    app.appendChild(box);
  }
  if (v.comparison) {
    const c = v.comparison;
    app.appendChild(el('p', t('myCompare', c.reject, c.accept, c.both, c.onlyAccept)));
  }
  app.appendChild(el('p', t('myStored'), 'note'));
  const keep = el('select');
  for (const d of [7, 30, 90]) {
    const o = el('option', t('myDays', d));
    o.value = String(d);
    o.selected = d === m.settings.days;
    keep.appendChild(o);
  }
  keep.addEventListener('change', async () => { await send({ type: 'mytests:settings', days: Number(keep.value) }); load(); });
  const tools = add(el('div', undefined, 'tools'), el('span', t('myKeep'), 'svc'), keep);
  if (v.reject.runs.length || v.accept.runs.length) {
    tools.appendChild(button(t('myDeleteSite'), () => send({ type: 'mytests:delete', site: m.site })));
  }
  tools.appendChild(button(t('myDeleteAll'), () => send({ type: 'mytests:delete' })));
  tools.appendChild(button(t('myExport'), async () => {
    const data = await send({ type: 'mytests:export' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
    a.download = `lens-banner-tests-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
  }));
  tools.appendChild(button(t('myTurnOff'), () => send({ type: 'mytests:settings', enabled: false })));
  app.appendChild(tools);
}

const pct = (part, whole) => (whole ? Math.round((100 * part) / whole) : 0);

/** F6: simulation with our own filter lists; nothing is blocked. */
function renderBlocking(b) {
  if (!b || !b.full.services) return;
  app.appendChild(el('h2', t('blockTitle')));
  const dl = el('dl');
  const row = (label, x) => add(dl, el('dt', label), el('dd', t('blockRow', x.requests, pct(x.requests, b.thirdRequests),
    x.bytes ? formatBytes(x.bytes) : '-')));
  row(t('blockVerified', b.verified.services), b.verified);
  row(t('blockFull', b.full.services), b.full);
  add(app, el('p', t('blockIntro', b.thirdRequests), 'note'), dl, el('p', t('blockNote'), 'note'));
}

/** F5: the pages opened in this tab (domain and count only, forgotten when the tab closes). */
function renderJourney(j) {
  if (!j) return;
  app.appendChild(el('h2', t('journeyTitle')));
  const ol = el('ol', undefined, 'steps');
  for (const r of j.rows) ol.appendChild(el('li', `${r.site} \u00b7 ${t('journeyCount', r.tracking)}`, r.current ? '' : 'done'));
  app.appendChild(ol);
  app.appendChild(el('p', j.common.length ? t('journeyCommon', j.previous, j.common.length, j.common.join(', '))
    : t('journeyNone', j.previous)));
  app.appendChild(el('p', t('journeyNote'), 'note'));
}

/** F9: copy a text summary, export the report as JSON, open a pre-filled list correction (never sent by Lens). */
function exportable(data) {
  const s = data.page;
  return {
    format: 'tracker-watch-lens/page-report', version: 1, exported: new Date().toISOString(),
    data: data.index ? { list: data.index.data_version, entries: data.index.list_entries } : null,
    site: s.site, host: s.host, trackingBefore: s.trackingBefore, band: s.band, trackingNewAfter: s.trackingNewAfter,
    thirdPartyDomains: s.thirdPartyDomains, thirdPartyRequests: s.thirdPartyRequests, bytes: s.bytes, stopped: s.stopped,
    operators: s.operators.map((o) => ({ entity: o.entity, services: o.services.map((x) => ({ service: x.service,
      category: x.category, tracking: x.tracking, verified: x.verified, before: x.before, after: x.after,
      did: x.phrases })) })),
    behavioursOther: s.behavioursOther, blocking: s.blocking,
    consent: data.consent, baseline: data.baseline && { weekly: data.baseline.weekly, date: data.baseline.date,
      vantage: data.baseline.vantage, onlyHere: data.baseline.onlyHere, onlyWeekly: data.baseline.onlyWeekly },
    // the user agent and language describe this browser: left out unless the user edits the file
    told: data.told && { thirdWithCookies: data.told.thirdWithCookies, thirdWithReferer: data.told.thirdWithReferer,
      cookieServices: data.told.cookieServices, params: data.told.params },
    reasons: (data.reasons || []).map((r) => r.id),
  };
}

function summaryText(data) {
  const s = data.page;
  const lines = [t('shareLine1', s.host, s.trackingBefore, s.band || '-'),
    t('shareLine2', s.thirdPartyDomains, s.thirdPartyRequests)];
  if (s.trackingNewAfter.length) lines.push(t('shareLine3', s.trackingNewAfter.length));
  lines.push(t('shareLine4', s.operators.filter((o) => o.trackingServices).map((o) => o.entity).slice(0, 12).join(', ')));
  lines.push(t('honesty'));
  lines.push('Tracker Watch Lens');
  return lines.join('\n');
}

function renderShare(tab, data) {
  app.appendChild(el('h2', t('shareTitle')));
  const tools = el('div', undefined, 'tools');
  tools.appendChild(button(t('shareCopy'), async () => {
    try { await navigator.clipboard.writeText(summaryText(data)); notice(t('shareCopied')); }
    catch { notice(t('shareCopyFailed')); }
  }));
  tools.appendChild(button(t('shareExport'), async () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(exportable(data), null, 2)], { type: 'application/json' }));
    a.download = `lens-${data.page.site}-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
  }));
  const services = data.page.operators.flatMap((o) => o.services.map((x) => `- ${x.service} (${o.entity}, ${x.category}${x.verified ? '' : ', not verified'})`));
  const body = [`Site: ${data.page.site}`, `List: ${data.index ? data.index.data_version : '?'}`, '',
    'What is wrong (which entry, and why):', '', '', 'Services Lens listed on this page:', ...services.slice(0, 60)].join('\n');
  const url = 'https://github.com/joseasenjo/tracker-watch/issues/new?' + new URLSearchParams({
    title: `List correction: ${data.page.site}`, body }).toString();
  tools.appendChild(link(t('shareIssue'), url));
  add(app, tools, el('p', t('shareNote'), 'note'));
}

/** Where tracking services were contacted from: the page itself or embedded frames (videos, ad slots). */
function renderFrames(s) {
  if (!s.frames.length) return;
  app.appendChild(el('h2', t('framesTitle')));
  const ul = el('ul');
  for (const f of s.frames) {
    ul.appendChild(add(el('li'), el('b', f.site), el('span', ' \u00b7 ' + t('framesRow', f.services.length, f.requests), 'svc'),
      el('div', f.services.join(', '), 'svc')));
  }
  app.appendChild(ul);
  if (s.onlyFromFrames.length) app.appendChild(el('p', t('framesOnly', s.onlyFromFrames.length), 'note'));
  app.appendChild(el('p', t('framesNote'), 'note'));
}

/** A folded list: long lists of service names stay closed until opened. */
function fold(summary, text) {
  return add(el('details'), el('summary', summary, 'svc'), el('div', text, 'svc'));
}

function renderArrival(a) {
  if (!a || !a.engineName) return;
  const lines = [a.redirect ? t('arrivedRedirect', a.engineName) : t('arrivedVia', a.engineName)];
  if (a.ping) lines.push(t('arrivedPing', a.engineName));
  for (const line of lines) app.appendChild(el('p', line, 'note'));
}

/** A link from the extension's own profile file (never from the page). */
function link(label, url) {
  const a = el('a', label);
  if (/^https:\/\//.test(url)) a.href = url;
  a.target = '_blank';
  a.rel = 'noreferrer noopener';
  return a;
}

function renderSearch(q) {
  if (!q) return;
  app.appendChild(el('h2', t('searchTitle', q.name)));
  const query = q.params.find((p) => p.isQuery);
  app.appendChild(el('p', t('searchParams', query ? query.name : 'q')));
  app.appendChild(el('p', q.params.map((p) => p.name).join(', '), 'svc'));
  const dl = el('dl');
  const row = (k, v) => add(dl, el('dt', k), el('dd', String(v)));
  row(t('linksTotal'), q.links.total);
  row(t('linksRedirect'), q.links.redirect);
  row(t('linksPing'), q.links.ping);
  row(t('linksMousedown'), q.links.mousedown);
  row(t('pingsSent'), q.pings);
  add(app, el('p', t('searchLinks'), 'note'), dl);
  app.appendChild(el('p', t('searchCookies', q.cookies.length)));
  if (q.cookies.length) {
    const ul = el('ul');
    for (const ck of q.cookies) {
      const life = ck.session ? t('cookieSession') : (ck.days !== null ? t('cookieDays', ck.days) : '');
      add(ul, add(el('li'), el('span', ck.name), el('span', ` · ${life}`, 'svc'),
        el('div', ck.purpose || t('cookieNoPurpose'), 'svc')));
    }
    app.appendChild(ul);
    if (q.cookieSource) add(app, add(el('p', undefined, 'note'), el('span', t('cookieSource') + ' '), link(q.cookieSource, q.cookieSource)));
  }
  renderServer(q.name, q.server, q.signedIn, q.checked);
}

/** M3b: what the engine says it keeps on its servers (its words, paraphrased) and where to see or ask for it. */
function renderServer(name, g, signed, checked) {
  if (!g) return;
  app.appendChild(el('h2', t('serverTitle', name)));
  app.appendChild(el('p', t('searchServer'), 'note'));
  if (signed === true) app.appendChild(el('p', t('signedIn', name), 'warn'));
  else if (signed === false) app.appendChild(el('p', t('signedOut', name), 'note'));
  app.appendChild(el('p', g.signed_out));
  if (g.signed_in) app.appendChild(el('p', g.signed_in));
  const ol = el('ol', undefined, 'steps');
  for (const s of g.steps) ol.appendChild(add(el('li'), link(s.label, s.url), el('div', s.what, 'svc')));
  app.appendChild(ol);
  app.appendChild(el('p', t('gdprAccess'), 'note'));
  const src = add(el('p', undefined, 'note'), el('span', t('serverSources', checked) + ' '));
  g.sources.forEach((u, i) => { if (i) src.appendChild(el('span', ' \u00b7 ')); src.appendChild(link(u.replace(/^https:\/\//, ''), u)); });
  app.appendChild(src);
}

function renderTold(told) {
  if (!told) return;
  app.appendChild(el('h2', t('toldTitle')));
  const me = told.self;
  if (me) {
    const dl = el('dl', undefined, 'told');
    const row = (k, v) => add(dl, el('dt', k), el('dd', v));
    if (me.userAgent) row(t('toldAgent'), me.userAgent);
    if (me.language) row(t('toldLanguage'), me.language);
    const hints = Object.values(me.hints || {});
    if (hints.length) row(t('toldHints'), hints.join(' · '));
    row(t('toldGpc'), me.gpc ? t('sent') : t('notSent'));
    row(t('toldDnt'), me.dnt ? t('sent') : t('notSent'));
    if (typeof me.cookies === 'number') row(t('toldSelfCookies'), String(me.cookies));
    if (me.cameFrom) row(t('toldCameFrom'), me.cameFrom);
    app.appendChild(dl);
  } else {
    app.appendChild(el('p', t('toldNoSelf'), 'note'));
  }
  const dl = el('dl');
  const row = (k, v) => add(dl, el('dt', k), el('dd', v));
  row(t('toldCookies'), String(told.thirdWithCookies));
  row(t('toldCookieServices'), String(Object.keys(told.cookieServices || {}).length));
  row(t('toldReferer'), String(told.thirdWithReferer));
  const params = Object.entries(told.params || {}).map(([n, c]) => `${n} ×${c}`);
  if (params.length) row(t('toldParams'), params.join(', '));
  app.appendChild(dl);
  const withCookies = Object.entries(told.cookieServices || {}).sort((a, b) => b[1] - a[1]);
  if (withCookies.length) {
    app.appendChild(fold(t('toldCookieList', withCookies.length),
      withCookies.map(([svc, n]) => `${svc}: ${t('toldCookieNames', n)}`).join(' \u00b7 ')));
  }
  const withAddress = Object.keys(told.refererServices || {}).sort();
  if (withAddress.length) app.appendChild(fold(t('toldRefererList', withAddress.length), withAddress.join(', ')));
  app.appendChild(el('p', t('toldNote'), 'note'));
}

let refresh = null;
async function load() {
  if (!(await hostAccess())) {
    app.replaceChildren(el('h1', t('extName')), el('p', t('noHostAccess')),
      button(t('grantHostAccess'), () => api.permissions.request({ origins: ['<all_urls>'] })));
    return;
  }
  const tab = await currentTab();
  const data = tab ? await api.runtime.sendMessage({ type: 'report', tabId: tab.id, url: tab.url }) : null;
  const scroll = document.scrollingElement.scrollTop;
  render(tab, data);
  document.scrollingElement.scrollTop = scroll;
  // while the page is still loading, numbers can grow: refresh every second until it settles
  clearTimeout(refresh);
  if (data && data.loading) refresh = setTimeout(load, 1000);
}

document.documentElement.lang = api.i18n.getUILanguage();
load();
