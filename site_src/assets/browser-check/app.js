// Browser check: reads what a script can see about this browser and shows it with a plain explanation.
// Everything happens in this page. Nothing is sent, nothing is stored.
import { BY_ID, CATALOG, GROUPS, WEIGHTS } from './catalog.js';
import { COLLECTORS } from './collectors.js';
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

function tile(label, value, note, tone) {
  return el('div', { class: 'bc-tile' }, [
    el('div', { class: 'bc-tile-label', text: label }),
    el('div', { class: `bc-tile-value ${tone || ''}`, text: value }),
    el('p', { class: 'bc-tile-note', text: note }),
  ]);
}

async function run() {
  const status = document.getElementById('bc-status');
  const results = [];
  const settled = await Promise.all(CATALOG.map(async (meta) => ({ ...(await COLLECTORS[meta.id]()), id: meta.id, title: meta.title })));
  results.push(...settled);
  const by = Object.fromEntries(results.map((r) => [r.id, r]));

  const code = await combinedCode(results);
  const missing = results.filter((r) => /^Not available/.test(r.value)).length;

  const tiles = document.getElementById('bc-tiles');
  tiles.replaceChildren(
    tile('Details a script could read', String(results.length - missing),
      missing ? `${plural(missing, 'more was', 'more were')} not available in this browser.` : 'All of them were available.'),
    tile('Ad filter in the browser', by['ad-filter'].hidden ? 'Detected' : 'Not detected',
      'Checks whether a block of the kind ad filters hide was hidden. It does not make any request, so it cannot tell whether trackers are blocked on the network.'),
    tile('Drawing noise', by.canvas.randomised ? 'Added' : 'Not added',
      by.canvas.randomised ? 'The same drawing gave two different results: your browser disturbs it on purpose.'
        : 'The same drawing gave the same result twice: it would be stable between visits.'),
    tile('Privacy signals', by['privacy-signals'].on ? 'On' : 'Off',
      'Do Not Track or Global Privacy Control. They are requests that sites can ignore.'));

  document.getElementById('bc-code').textContent = shorten(code, 16);
  document.getElementById('bc-code-box').hidden = false;

  for (const group of GROUPS) {
    const host = document.getElementById(`bc-group-${group.id}`);
    host.replaceChildren(...results.filter((r) => BY_ID[r.id].group === group.id).map(entryNode));
  }
  status.hidden = true;
  document.getElementById('bc-results').hidden = false;

  const copy = document.getElementById('bc-copy');
  copy.addEventListener('click', async () => {
    const text = reportText(results, new Date().toISOString().slice(0, 10));
    try {
      await navigator.clipboard.writeText(text);
      copy.textContent = 'Copied';
    } catch (error) {
      copy.textContent = 'Could not copy';
    }
    setTimeout(() => { copy.textContent = 'Copy this report as text'; }, 2500);
  });
}

run().catch(() => {
  const status = document.getElementById('bc-status');
  status.textContent = 'This browser did not let the page read its details. Nothing was sent anywhere.';
});
