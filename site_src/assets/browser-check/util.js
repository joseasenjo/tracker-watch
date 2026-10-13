// Small pure helpers (no browser features needed, so they can be tested with Node).

// A short, repeatable code for a piece of text. SHA-256 where the browser offers it, otherwise a simple hash.
export async function hashText(text, subtle = globalThis.crypto && globalThis.crypto.subtle) {
  if (subtle && subtle.digest) {
    const bytes = new TextEncoder().encode(text);
    const digest = await subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
  }
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}

export function shorten(code, length = 12) {
  return String(code).slice(0, length);
}

// Which of the candidate fonts are installed. `measure(font)` returns the width of a test text rendered with that
// font after falling back to each base family; a font counts as installed when any width differs from the base.
export function detectFonts(candidates, bases, measure) {
  const baseWidth = {};
  for (const base of bases) baseWidth[base] = measure(base, base);
  return candidates.filter((font) => bases.some((base) => measure(`"${font}", ${base}`, base) !== baseWidth[base]));
}

// One line per characteristic, ready to copy: "Title: value". A value that spans lines is joined with " / " so that
// every detail stays on one line and a report can be read back by compareReports.
export function reportText(results, when, code = '') {
  const lines = [`Browser check, ${when}. Read locally; nothing was sent anywhere.`];
  if (code) lines.push(`Combined code: ${code}`);
  lines.push('');
  for (const r of results) lines.push(`${r.title}: ${String(r.value).replace(/\s*\n\s*/g, ' / ')}`);
  return lines.join('\n');
}

// "Title: value" lines of a copied report -> Map(title -> value). Anything else in the text is ignored.
export function parseReport(text) {
  const map = new Map();
  for (const line of String(text).split('\n')) {
    const at = line.indexOf(': ');
    if (at > 0 && !line.startsWith('Browser check,')) map.set(line.slice(0, at).trim(), line.slice(at + 2).trim());
  }
  return map;
}

// What changed and what stayed the same between two reports. Details present in only one are listed as such.
export function compareReports(textA, textB) {
  const a = parseReport(textA);
  const b = parseReport(textB);
  const rows = [];
  for (const title of new Set([...a.keys(), ...b.keys()])) {
    const left = a.get(title);
    const right = b.get(title);
    rows.push({ title, a: left ?? '(missing)', b: right ?? '(missing)', same: left === right });
  }
  return { rows, same: rows.filter((r) => r.same).length, different: rows.filter((r) => !r.same) };
}

// The code a tracker could compute from the values below: if two sites get the same code, they can link the visits.
export async function combinedCode(results, hash = hashText) {
  const stable = results.filter((r) => r.stable !== false).map((r) => `${r.id}=${r.raw ?? r.value}`);
  return hash(stable.join('\n'));
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}
