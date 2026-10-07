// Settings and your data: access check, "Your week" (F8), your own banner tests, delete everything, privacy policy.
// Every text goes through textContent; the privacy policy is a page generated at build time from PRIVACY.md.
const api = globalThis.browser ?? globalThis.chrome;
const t = (key, ...subs) => api.i18n.getMessage(key, subs.map(String)) || key;
const app = document.getElementById('app');
const status = document.getElementById('status');
const send = (msg) => api.runtime.sendMessage(msg);

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
const add = (parent, ...nodes) => { for (const n of nodes) parent.appendChild(n); return parent; };
function button(label, onClick, cls) {
  const b = el('button', label, cls);
  b.addEventListener('click', async () => { b.disabled = true; await onClick(); render(); });
  return b;
}
function toggle(label, checked, onChange) {
  const input = el('input');
  input.type = 'checkbox';
  input.checked = checked;
  input.addEventListener('change', async () => { await onChange(input.checked); render(); });
  return add(el('label', undefined, 'switch'), input, el('span', label));
}
function download(name, data) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  a.download = name;
  a.click();
}

function bars(view) {
  const box = el('div');
  const top = view.operators.slice(0, 20);
  for (const o of top) {
    const fill = el('div', undefined, 'fill');
    fill.style.width = `${Math.round((100 * o.pages) / Math.max(1, view.pages))}%`;
    add(box, add(el('div', undefined, 'bar'), el('span', o.name), add(el('div', undefined, 'track'), fill),
      el('span', t('weekOfPages', o.pages, view.pages))));
  }
  if (view.operators.length > top.length) box.appendChild(el('p', t('weekMore', view.operators.length - top.length), 'note'));
  return box;
}

async function render() {
  const access = await api.permissions.contains({ origins: ['<all_urls>'] }).catch(() => true);
  const data = await send({ type: 'summary:get' });
  app.replaceChildren(el('h1', t('extName')), el('p', t('optionsIntro'), 'note'));

  if (!access) {
    add(app, el('h2', t('noHostAccessTitle')), el('p', t('noHostAccess')),
      button(t('grantHostAccess'), () => api.permissions.request({ origins: ['<all_urls>'] })));
  }

  const w = data.summary;
  app.appendChild(el('h2', t('weekTitle')));
  app.appendChild(el('p', t('weekExplain'), 'note'));
  app.appendChild(toggle(t('weekToggle'), w.enabled, (on) => send({ type: 'summary:settings', enabled: on })));
  for (const [view, title] of [[w.week, t('weekLast7')], [w.month, t('weekLast30')]]) {
    if (!view.pages) continue;
    add(app, el('h3', title), el('p', t('weekPages', view.pages, view.operators.length)), bars(view));
  }
  if (w.enabled && !w.week.pages && !w.month.pages) app.appendChild(el('p', t('weekEmpty'), 'note'));
  if (w.week.pages || w.month.pages) {
    add(app, add(el('div', undefined, 'tools'),
      button(t('weekExport'), async () => download(`lens-your-week-${new Date().toISOString().slice(0, 10)}.json`,
        { format: 'tracker-watch-lens/your-week', version: 1, week: w.week, month: w.month })),
      button(t('weekDelete'), () => send({ type: 'summary:delete' }))));
  }

  const m = data.mytests;
  app.appendChild(el('h2', t('myTitle')));
  app.appendChild(toggle(t('myToggle'), m.settings.enabled, (on) => send({ type: 'mytests:settings', enabled: on })));
  app.appendChild(el('p', t('myCount', m.sites, m.settings.days), 'note'));
  if (m.sites) {
    add(app, add(el('div', undefined, 'tools'),
      button(t('myExport'), async () => download(`lens-banner-tests-${new Date().toISOString().slice(0, 10)}.json`,
        await send({ type: 'mytests:export' }))),
      button(t('myDeleteAll'), () => send({ type: 'mytests:delete' }))));
  }

  app.appendChild(el('h2', t('deleteAllTitle')));
  app.appendChild(el('p', t('deleteAllText'), 'note'));
  app.appendChild(button(t('deleteAll'), async () => {
    if (!confirm(t('deleteAllConfirm'))) return;
    await send({ type: 'data:deleteAll' });
    status.textContent = t('deleteAllDone');
  }, 'danger'));

  app.appendChild(el('h2', t('privacyTitle')));
  const frame = el('div', undefined, 'privacy');
  try {
    const html = await (await fetch(api.runtime.getURL('privacy.html'))).text();
    // our own file, generated at build time from PRIVACY.md: parsed, never injected as a string
    const doc = new DOMParser().parseFromString(html, 'text/html');
    for (const node of [...doc.body.childNodes]) frame.appendChild(document.importNode(node, true));
  } catch { frame.appendChild(el('p', t('privacyMissing'), 'note')); }
  app.appendChild(frame);
}

document.documentElement.lang = api.i18n.getUILanguage();
render();
