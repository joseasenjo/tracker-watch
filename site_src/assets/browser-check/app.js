// Browser check: reads what a script can see about this browser and shows it with a plain explanation.
// Reading happens only when the visitor presses the button, and only inside this page: nothing is sent, nothing is
// stored. The tracker test is a second, separate button because it is the one thing that contacts other servers.
import { BY_ID, CATALOG, GROUPS, WEIGHTS } from './catalog.js';
import { COLLECTORS } from './collectors.js';
import { runTrackerTest, VERDICT_TEXT } from './tracker-test.js';
import { combinedCode, plural, reportText, shorten } from './util.js';

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

function entryNode(result) {
  const meta = BY_ID[result.id];
  const unavailable = /^Not available|^Hidden by/.test(result.value);
  const summary = el('summary', {}, [
    el('span', { class: 'bc-title', text: meta.title }),
    el('span', { class: 'bc-value', text: result.value.split('\n')[0] }),
    el('span', { class: `pill ${unavailable ? 'ok' : meta.weight === 'high' ? 'warn' : ''}`, text: WEIGHTS[meta.weight] }),
  ]);
  const body = el('div', { class: 'bc-body' }, [
    el('h4', { text: 'What a script reads' }),
    el('pre', { class: 'bc-raw', text: result.value }),
    el('h4', { text: 'What is this?' }),
    el('p', { text: meta.what }),
    el('h4', { text: 'How can it be used to recognise you?' }),
    el('p', { text: meta.how }),
  ]);
  return el('details', { class: 'bc-entry', id: `bc-${meta.id}` }, [summary, body]);
}

function tile(label, value, note) {
  return el('div', { class: 'bc-tile' }, [
    el('div', { class: 'bc-tile-label', text: label }),
    el('div', { class: 'bc-tile-value', text: value }),
    el('p', { class: 'bc-tile-note', text: note }),
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

async function render(results) {
  const by = Object.fromEntries(results.map((r) => [r.id, r]));
  const code = await combinedCode(results);
  const missing = results.filter((r) => /^Not available/.test(r.value)).length;
  const signs = defences(by);
  $('bc-tiles').replaceChildren(
    tile('Details a script could read', String(results.length - missing),
      missing ? `${plural(missing, 'more was', 'more were')} not available in this browser.` : 'All of them were available.'),
    tile('Signs of fingerprint defences', signs.length ? String(signs.length) : 'None seen',
      signs.length ? `Seen: ${signs.join(', ')}.` : 'Nothing suggests that your browser hides or disturbs these details.'),
    tile('Ad filter in the browser', by['ad-filter'].hidden ? 'Detected' : 'Not detected',
      'Checks whether a block of the kind ad filters hide was hidden. It makes no request, so it cannot tell whether trackers are blocked on the network: for that, use the test below.'),
    tile('Privacy signals', by['privacy-signals'].on ? 'On' : 'Off',
      'Do Not Track or Global Privacy Control. They are requests that sites can ignore.'));
  $('bc-code').textContent = shorten(code, 16);
  $('bc-code-box').hidden = false;
  for (const group of GROUPS) {
    $(`bc-group-${group.id}`).replaceChildren(...results.filter((r) => BY_ID[r.id].group === group.id).map(entryNode));
  }
  $('bc-results').hidden = false;
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
  const result = await runTrackerTest({ fetchFn: (url, init) => fetch(url, init), controlUrl: location.href.split('#')[0] });
  button.disabled = false;
  button.textContent = 'Run the test again';
  if (!result.ok) {
    out.textContent = 'This page could not reach its own server, so nothing can be concluded. Check your connection and try again.';
    return;
  }
  const state = { reachable: 'reached the server', blocked: 'blocked', timeout: 'no answer in time' };
  out.replaceChildren(
    el('div', { class: 'bc-tiles' }, [
      tile('Blocking tracking ads?', VERDICT_TEXT[result.ads], 'Advertising servers that serve and measure ads.'),
      tile('Blocking invisible trackers?', VERDICT_TEXT[result.analytics], 'Analytics and tracking-pixel servers that load without being seen.'),
    ]),
    el('div', { class: 'table-scroll' }, [el('table', { class: 't' }, [
      el('thead', {}, [el('tr', {}, ['Server', 'Run by', 'Result'].map((h) => el('th', { text: h })))]),
      el('tbody', {}, result.rows.map((r) => el('tr', {}, [el('td', { text: r.host }), el('td', { text: r.owner }),
        el('td', { text: state[r.state] })]))),
    ])]),
    el('p', { class: 'small', text: 'The result says whether the request left your browser, not what any company does with data. An extension, a network filter or a browser setting can each cause a block.' }));
}

$('bc-read').addEventListener('click', onRead);
$('bc-again').addEventListener('click', onAgain);
$('bc-test').addEventListener('click', onTest);
$('bc-copy').addEventListener('click', async () => {
  const copy = $('bc-copy');
  try {
    await navigator.clipboard.writeText(reportText(last || [], new Date().toISOString().slice(0, 10)));
    copy.textContent = 'Copied';
  } catch (error) {
    copy.textContent = 'Could not copy';
  }
  setTimeout(() => { copy.textContent = 'Copy this report as text'; }, 2500);
});
