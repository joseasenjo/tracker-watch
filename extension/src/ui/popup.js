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
  const head = add(el('div', undefined, 'headline'), el('span', String(s.trackingBefore), 'big'), el('span', t('headline')));
  const chips = add(el('div', undefined, 'chips'), el('span', t('oneVisit'), 'chip'));
  if (s.band) chips.appendChild(el('span', t('band', s.band), 'chip'));
  add(app, head, chips);
  renderArrival(data.arrival);
  const c = data.consent || { banners: [], toolsContacted: [], click: null };
  if (c.banners.length) app.appendChild(el('p', t('bannerShown', c.banners.join(', ')), 'note'));
  else if (c.toolsContacted.length) app.appendChild(el('p', t('consentContacted', c.toolsContacted.join(', ')), 'note'));
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
    }
    app.appendChild(el('p', text, 'note'));
    if (s.trackingNewAfter.length) {
      app.appendChild(el('p', t('newAfter', s.trackingNewAfter.length, s.thirdPartyRequestsAfter)));
      app.appendChild(el('p', s.trackingNewAfter.join(', '), 'svc'));
    } else {
      app.appendChild(el('p', t('noneAfter', s.thirdPartyRequestsAfter)));
    }
  }

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

async function load() {
  const tab = await currentTab();
  const data = tab ? await api.runtime.sendMessage({ type: 'report', tabId: tab.id, url: tab.url }) : null;
  render(tab, data);
}

document.documentElement.lang = api.i18n.getUILanguage();
load();
