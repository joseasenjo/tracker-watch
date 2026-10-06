// Registrable domain ("news.bbc.co.uk" -> "bbc.co.uk") with the public-suffix rules shipped in
// data/psl.json. Mirrors the engine (traceguard.classify.registrable_domain, which uses tldextract
// without private suffixes); tests/parity.test.js checks both give the same answer.

/**
 * @typedef {{ matches: Map<string, SuffixNode>, end: boolean }} SuffixNode
 */

/** @returns {SuffixNode} */
function node() {
  return { matches: new Map(), end: false };
}

/**
 * Build a trie of the rules, labels reversed ("*.ck" -> ck > *; "!www.ck" -> ck > !www).
 * @param {string[]} rules
 * @returns {SuffixNode}
 */
export function buildSuffixTrie(rules) {
  const root = node();
  for (const rule of rules) {
    let current = root;
    for (const label of rule.split('.').reverse()) {
      let next = current.matches.get(label);
      if (!next) {
        next = node();
        current.matches.set(label, next);
      }
      current = next;
    }
    current.end = true;
  }
  return root;
}

/**
 * Index of the first label of the public suffix, or -1 when no rule matches.
 * Same walk as tldextract's Trie: longest rule wins, wildcards take one more label unless excepted.
 * @param {SuffixNode} root
 * @param {string[]} labels
 * @returns {number}
 */
function suffixIndex(root, labels) {
  let current = root;
  let labelIdx = labels.length;
  let suffixIdx = labels.length;
  for (let i = labels.length - 1; i >= 0; i--) {
    const label = labels[i];
    const next = current.matches.get(label);
    if (next) {
      labelIdx -= 1;
      current = next;
      if (current.end) suffixIdx = labelIdx;
      continue;
    }
    if (current.matches.has('*')) {
      return current.matches.has('!' + label) ? labelIdx : labelIdx - 1;
    }
    break;
  }
  return suffixIdx === labels.length ? -1 : suffixIdx;
}

/**
 * Registrable domain of a host. Hosts without a known suffix (IP addresses, "localhost",
 * intranet names) and hosts that are themselves a public suffix are returned unchanged, as the engine does.
 * @param {SuffixNode} trie
 * @param {string} host
 * @returns {string}
 */
export function registrableDomain(trie, host) {
  const clean = String(host).trim().replace(/^\.+|\.+$/g, '').toLowerCase();
  if (!clean) return '';
  if (clean.startsWith('[') && clean.endsWith(']')) return clean;
  const labels = clean.split('.');
  const idx = suffixIndex(trie, labels);
  if (idx <= 0) return clean;
  return labels.slice(idx - 1).join('.');
}
