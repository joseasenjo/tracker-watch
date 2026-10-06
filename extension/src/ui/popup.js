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
    app.appendChild(el('p', s.trackingNewAfter.length ? s.trackingNewAfter.join(', ') : '0', 'note'));
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
  app.appendChild(el('p', t('why'), 'note'));
  if (s.truncated) app.appendChild(el('p', t('truncated'), 'note'));
  app.appendChild(el('p', t('honesty'), 'note'));
  if (data.index) app.appendChild(el('p', t('dataVersion', data.index.data_version, data.index.list_entries), 'note'));
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
