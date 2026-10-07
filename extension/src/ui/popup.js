// The panel. Every text from the page or the data goes through textContent: never innerHTML.
const api = globalThis.browser ?? globalThis.chrome;
const t = (key, ...subs) => api.i18n.getMessage(key, subs.map(String)) || key;
const app = document.getElementById('app');

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

  renderMyTests(tab, data.mytests, Boolean(c.payOrAccept));

  app.appendChild(el('h2', t('operatorsTitle')));
  for (const op of s.operators) {
    const box = el('div', undefined, 'op');
    box.appendChild(el('b', op.entity));
    const ul = el('ul');
    for (const svc of op.services) {
      const li = el('li');
      const labels = [category(svc.category)];
      if (!svc.tracking) labels.push(t('notCounted'));
      if (!svc.verified) labels.push(t('unverified'));
      add(li, el('span', svc.service), el('span', ` · ${labels.join(' · ')}`, 'svc'));
      if (svc.phrases.length) li.appendChild(el('div', svc.phrases.join('; '), 'svc'));
      ul.appendChild(li);
    }
    add(app, add(box, ul));
  }

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
  if (data.index) app.appendChild(el('p', t('dataVersion', data.index.data_version, data.index.list_entries), 'note'));
  fetch(api.runtime.getURL('data/build.json')).then((r) => r.json())
    .then((b) => app.appendChild(el('p', `build ${b.built}`, 'note')), () => {});
}

const send = (msg) => api.runtime.sendMessage(msg);
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
  app.appendChild(el('p', t('searchServer'), 'note'));
  if (q.accountLinks.length) {
    app.appendChild(add(el('ul'), ...q.accountLinks.map((l) => add(el('li'), link(l.label, l.url)))));
  } else {
    app.appendChild(el('p', t('noAccountLinks'), 'note'));
  }
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
  app.appendChild(el('p', t('toldNote'), 'note'));
}

let refresh = null;
async function load() {
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
