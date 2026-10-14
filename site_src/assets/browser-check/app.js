// Browser check: reads what a script can see about this browser and shows it with a plain explanation.
// Reading happens only when the visitor presses the button, and only inside this page: nothing is sent, nothing is
// stored. The tracker test is a second, separate button because it is the one thing that contacts other servers.
import { ACTIONS } from './actions.js';
import { BY_ID, CATALOG, GROUPS, WEIGHTS } from './catalog.js';
import { runConnectionTest } from './connection-test.js';
import { COLLECTORS } from './collectors.js';
import { SUMMARY_NOTE, summarise } from './summary.js';
import { preloadProbe, rowText, runTrackerTest, VERDICT_TEXT } from './tracker-test.js';
import { combinedCode, compareReports, plural, reportText, shorten, timezoneExample } from './util.js';

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'text') node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.appendChild(child);
  return node;
}

const $ = (id) => document.getElementById(id);

// Worked examples shown only in the detailed view, for the details where seeing the numbers helps.
const EXTRAS = { timezone: () => timezoneExample() };

function entryNode(result) {
  const meta = BY_ID[result.id];
  const unavailable = /^Not available|^Hidden by/.test(result.value);
  const summary = el('summary', { class: 'bc-head' }, [
    el('span', { class: 'bc-title', text: meta.title }),
    el('span', { class: 'bc-value', text: result.value.split(String.fromCharCode(10))[0] }),
    el('span', { class: `pill ${unavailable ? 'ok' : meta.weight === 'high' ? 'warn' : ''}`, text: WEIGHTS[meta.weight] }),
  ]);
  const body = el('div', { class: 'bc-detail' }, [
    el('h4', { text: 'What a script reads' }),
    el('pre', { class: 'bc-raw', text: result.value }),
    EXTRAS[meta.id] ? el('p', { class: 'bc-extra', text: EXTRAS[meta.id]() }) : null,
    el('h4', { text: 'What is this?' }),
    el('p', { text: meta.what }),
    el('h4', { text: 'How can it be used to recognise you?' }),
    el('p', { text: meta.how }),
    el('h4', { text: 'What can I do about it?' }),
    el('p', { text: ACTIONS[meta.id] }),
  ]);
  return el('details', { class: 'bc-entry', id: `bc-${meta.id}`, 'data-id': meta.id }, [summary, body]);
}

function tile(label, value, note, extra = null) {
  return el('div', { class: 'bc-tile' }, [
    el('div', { class: 'bc-tile-label', text: label }),
    el('div', { class: 'bc-tile-value', text: value }),
    el('p', { class: 'bc-tile-note', text: note }),
    extra,
  ]);
}

// The list behind the first tile: every detail that was read, each linking to its line, and the ones that were not available.
function detailList(results) {
  const read = results.filter((r) => !/^Not available/.test(r.value));
  const missing = results.filter((r) => /^Not available/.test(r.value));
  const link = (r) => el('li', {}, [el('a', { href: `#bc-${r.id}`, text: r.title })]);
  return el('details', { class: 'bc-tile-list' }, [
    el('summary', { text: 'See the list' }),
    el('p', { class: 'small', text: `Read in this browser (${read.length}):` }),
    el('ul', {}, read.map(link)),
    missing.length ? el('p', { class: 'small', text: `Not available in this browser (${missing.length}):` }) : null,
    missing.length ? el('ul', {}, missing.map((r) => el('li', { text: r.title }))) : null,
  ]);
}

async function readAll() {
  return Promise.all(CATALOG.map(async (meta) => ({ ...(await COLLECTORS[meta.id]()), id: meta.id, title: meta.title })));
}

// Signs that the browser is trying to hide or disturb a detail (what a fingerprinting defence does).
function defences(by) {
  const signs = [];
  if (by.canvas.randomised) signs.push('drawing noise');
  if (/^Hidden by/.test(by['webgl-renderer'].value)) signs.push('graphics card name hidden');
  if (/^Not available/.test(by.webgl.value)) signs.push('3D drawing not available');
  if (by.fonts.value.startsWith('0 of')) signs.push('no fonts detectable');
  return signs;
}

let first = null;
let last = null;
let lastCode = '';
let trackerResult = null;
let connectionResult = null;

function currentSummary() {
  if (!last) return null;
  const by = Object.fromEntries(last.map((r) => [r.id, r]));
  return summarise({ by, tracker: trackerResult, connection: connectionResult, language: navigator.language,
    zone: (by.timezone && by.timezone.zone) || '' });
}

// Results of the optional checks, added to the copied report so that a comparison shows what a blocker changes.
// Never the address itself: only the country it suggests and what WebRTC showed.
function reportExtras() {
  const extras = [];
  if (trackerResult) {
    const stopped = trackerResult.rows.filter((r) => r.state === 'blocked').length;
    extras.push({ title: 'Tracker test, servers stopped', value: `${stopped} of ${trackerResult.rows.length}` });
  }
  if (connectionResult && connectionResult.trace) {
    extras.push({ title: 'Connection check, country of the address', value: connectionResult.trace.loc || 'unknown' },
      { title: 'Connection check, WebRTC', value: connectionResult.rtc.relation });
  }
  return extras;
}

function fullReport() {
  return reportText([...(last || []), ...reportExtras()], new Date().toISOString().slice(0, 10), lastCode, currentSummary());
}

function listNode(items, makeItem) {
  return el('ul', { class: 'bc-summary-list' }, items.map(makeItem));
}

function renderSummary() {
  const summary = currentSummary();
  if (!summary) return;
  const plain = (text) => el('li', { text });
  const changeItem = (c) => {
    const li = el('li', { text: c.text + ' ' });
    if (c.id) li.appendChild(el('a', { href: `#bc-${c.id}`, text: 'See the detail' }));
    return li;
  };
  $('bc-summary-body').replaceChildren(
    el('h4', { text: 'Observed' }), listNode(summary.observed, plain),
    el('h4', { text: 'Worth knowing' }), listNode(summary.worth, plain),
    summary.change.length ? el('h4', { text: 'You could change' }) : null,
    summary.change.length ? listNode(summary.change, changeItem) : null,
    el('p', { class: 'small', text: SUMMARY_NOTE }));
}

async function render(results) {
  const by = Object.fromEntries(results.map((r) => [r.id, r]));
  const code = await combinedCode(results);
  const missing = results.filter((r) => /^Not available/.test(r.value)).length;
  const signs = defences(by);
  $('bc-tiles').replaceChildren(
    tile('Details a script could read', String(results.length - missing),
      missing ? `${plural(missing, 'more was', 'more were')} not available in this browser.` : 'All of them were available.',
      detailList(results)),
    tile('Signs of fingerprint defences', signs.length ? String(signs.length) : 'None seen',
      signs.length ? `Seen: ${signs.join(', ')}.` : 'Nothing suggests that your browser hides or disturbs these details.'),
    tile('Ad filter in the browser', by['ad-filter'].hidden ? 'Detected' : 'Not detected',
      'Checks whether a block of the kind ad filters hide was hidden. It makes no request, so it cannot tell whether trackers are blocked on the network: for that, use the test below.'),
    tile('Privacy signals', by['privacy-signals'].on ? 'On' : 'Off',
      'Do Not Track or Global Privacy Control. They are requests that sites can ignore.'));
  lastCode = shorten(code, 16);
  $('bc-code').textContent = lastCode;
  $('bc-code-box').hidden = false;
  for (const group of GROUPS) {
    $(`bc-group-${group.id}`).replaceChildren(...results.filter((r) => BY_ID[r.id].group === group.id).map(entryNode));
  }
  $('bc-results').hidden = false;
  applySelection();
  renderSummary();
  return code;
}

async function onRead() {
  const button = $('bc-read');
  button.disabled = true;
  button.textContent = 'Reading…';
  try {
    last = await readAll();
    first = await render(last);
    $('bc-intro-actions').hidden = true;
    $('bc-after').hidden = false;
    $('bc-compare').textContent = '';
  } catch (error) {
    $('bc-status').hidden = false;
    $('bc-status').textContent = 'This browser did not let the page read its details. Nothing was sent anywhere.';
    button.disabled = false;
    button.textContent = 'Read my browser';
  }
}

async function onAgain() {
  const again = await readAll();
  const code = await combinedCode(again);
  last = again;
  await render(again);
  $('bc-compare').textContent = code === first
    ? 'Same combined code as the first reading: these details are stable, so a script would see you as the same browser each time.'
    : 'The combined code is different from the first reading: something changed between the two (for example noise your browser adds, or a setting you changed).';
}

async function onTest() {
  const out = $('bc-test-out');
  const button = $('bc-test');
  button.disabled = true;
  button.textContent = 'Testing…';
  out.textContent = '';
  const result = await runTrackerTest({ fetchFn: (url, init) => fetch(url, init), preloadFn: (url, ms) => preloadProbe(url, document, ms),
    controlUrl: location.href.split('#')[0] });
  trackerResult = result.ok ? result : null;
  renderSummary();
  button.disabled = false;
  button.textContent = 'Run the test again';
  if (!result.ok) {
    out.textContent = 'This page could not reach its own server, so nothing can be concluded. Check your connection and try again.';
    return;
  }
  out.replaceChildren(
    el('div', { class: 'bc-tiles' }, [
      tile('Blocking tracking ads?', VERDICT_TEXT[result.ads], 'Advertising servers that serve and measure ads.'),
      tile('Blocking invisible trackers?', VERDICT_TEXT[result.analytics], 'Analytics and tracking-pixel servers that load without being seen.'),
    ]),
    el('div', { class: 'table-scroll' }, [el('table', { class: 't' }, [
      el('thead', {}, [el('tr', {}, ['Server', 'Run by', 'Result'].map((h) => el('th', { text: h })))]),
      el('tbody', {}, result.rows.map((r) => el('tr', {}, [el('td', { text: r.host }), el('td', { text: r.owner }),
        el('td', { text: rowText(r) })]))),
    ])]),
    el('p', { class: 'small', text: 'The result says whether the request left your browser, not what any company does with data. An extension, a network filter or a browser setting can each cause a block.' }));
}

$('bc-read').addEventListener('click', onRead);
$('bc-again').addEventListener('click', onAgain);
$('bc-test').addEventListener('click', onTest);
$('bc-copy').addEventListener('click', async () => {
  const copy = $('bc-copy');
  try {
    await navigator.clipboard.writeText(fullReport());
    copy.textContent = 'Copied';
  } catch (error) {
    copy.textContent = 'Could not copy';
  }
  setTimeout(() => { copy.textContent = 'Copy this report as text'; }, 2500);
});

$('bc-compare-btn').addEventListener('click', () => {
  const pasted = $('bc-paste').value;
  const out = $('bc-compare-out');
  if (!pasted.trim()) {
    out.textContent = 'Paste a report from another browser or window first.';
    return;
  }
  const mine = fullReport();
  const result = compareReports(mine, pasted);
  if (!result.rows.some((r) => r.a !== '(missing)' && r.b !== '(missing)')) {
    out.textContent = 'That text does not look like a report from this page. Use "Copy this report as text" in the other browser.';
    return;
  }
  const head = el('p', { text: `${plural(result.same, 'detail is', 'details are')} identical and ${plural(result.different.length, 'differs', 'differ')}.` });
  if (!result.different.length) {
    out.replaceChildren(head, el('p', { text: 'Both browsers show the same details: a script would see them as the same browser.' }));
    return;
  }
  out.replaceChildren(head, el('div', { class: 'table-scroll' }, [el('table', { class: 't' }, [
    el('thead', {}, [el('tr', {}, ['Detail', 'This browser', 'The pasted report'].map((h) => el('th', { text: h })))]),
    el('tbody', {}, result.different.map((r) => el('tr', {}, [el('td', { text: r.title }), el('td', { text: r.a }), el('td', { text: r.b })]))),
  ])]));
});

$('bc-conn').addEventListener('click', async () => {
  const out = $('bc-conn-out');
  const button = $('bc-conn');
  button.disabled = true;
  button.textContent = 'Checking…';
  out.textContent = '';
  const result = await runConnectionTest({ fetchFn: (url, init) => fetch(url, init), RTC: window.RTCPeerConnection });
  button.disabled = false;
  button.textContent = 'Check again';
  connectionResult = result;
  renderSummary();
  const trace = result.trace;
  const rtc = result.rtc;
  const rtcText = !result.rtcAvailable ? 'Not available in this browser'
    : rtc.relation === 'none' ? 'No public address revealed (blocked or hidden)'
    : rtc.relation === 'same' ? 'Same address as websites see'
    : 'A different address from the one websites see';
  const rtcNote = rtc.relation === 'different'
    ? 'WebRTC revealed an address other than the one your normal browsing shows. With a VPN this can mean the VPN is not covering WebRTC.'
    : rtc.relation === 'same' ? 'Nothing extra is revealed through WebRTC.' : 'A page cannot learn an extra address through WebRTC here.';
  const tiles = [];
  if (trace) {
    tiles.push(tile('Address websites see', trace.ip || 'Unknown', 'The address your requests come from. Every site you visit receives it.'),
      tile('Country it suggests', trace.loc || 'Unknown', 'Deduced from the address, not from your device. A VPN changes it.'),
      tile('Connection security', `${trace.tls || '?'} over ${trace.http || '?'}`, 'The encryption and web protocol version of this connection.'));
  }
  tiles.push(tile('Address through WebRTC', rtcText, rtcNote));
  const lines = [];
  if (!trace) lines.push(el('p', { class: 'note', text: 'The connection service did not answer, so only the WebRTC check is shown.' }));
  if (rtc.exposedLocal.length) lines.push(el('p', { class: 'small', text: `Local address revealed: ${rtc.exposedLocal.join(', ')}. Most browsers now hide it behind a random name.` }));
  else if (rtc.hiddenLocal) lines.push(el('p', { class: 'small', text: 'Your local network address is hidden behind a random name, which is what current browsers do.' }));
  out.replaceChildren(el('div', { class: 'bc-tiles' }, tiles), ...lines);
});

// Open or close every line at once. Nothing is hidden by either: it only saves clicking.
function setOpen(open) {
  for (const entry of document.querySelectorAll('.bc-entry')) entry.open = open;
}

function applySelection() {
  const chosen = $('bc-select').value;
  for (const entry of document.querySelectorAll('.bc-entry')) entry.hidden = chosen !== 'all' && entry.dataset.id !== chosen;
  for (const wrap of document.querySelectorAll('.bc-group')) wrap.hidden = !wrap.querySelector('.bc-entry:not([hidden])');
}

function fillSelect() {
  const select = $('bc-select');
  select.replaceChildren(el('option', { value: 'all', text: 'All characteristics' }));
  for (const group of GROUPS) {
    const box = el('optgroup', { label: group.title });
    for (const meta of CATALOG.filter((m) => m.group === group.id)) box.appendChild(el('option', { value: meta.id, text: meta.title }));
    select.appendChild(box);
  }
}

fillSelect();
$('bc-collapse').addEventListener('click', () => setOpen(false));
$('bc-expand').addEventListener('click', () => setOpen(true));
$('bc-select').addEventListener('change', applySelection);

// A link to a detail (from the list or from the summary) opens that line, even if the selector had hidden it.
document.addEventListener('click', (event) => {
  const link = event.target.closest && event.target.closest('.bc-tile-list a, .bc-summary-list a');
  if (!link) return;
  $('bc-select').value = 'all';
  applySelection();
  const entry = document.getElementById(link.getAttribute('href').slice(1));
  if (entry) entry.open = true;
});

// The summary is shown only when the visitor asks for it, after looking at the details.
$('bc-summary-btn').addEventListener('click', () => {
  renderSummary();
  const card = $('bc-summary');
  card.hidden = false;
  $('bc-summary-btn').setAttribute('aria-expanded', 'true');
  $('bc-summary-btn').textContent = 'Refresh the summary';
  card.scrollIntoView({ behavior: 'smooth', block: 'start' });
});
