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

// One line per characteristic, ready to copy: "Title: value".
export function reportText(results, when) {
  const lines = [`Browser check, ${when}. Read locally; nothing was sent anywhere.`, ''];
  for (const r of results) lines.push(`${r.title}: ${r.value}`);
  return lines.join('\n');
}

// The code a tracker could compute from the values below: if two sites get the same code, they can link the visits.
export async function combinedCode(results, hash = hashText) {
  const stable = results.filter((r) => r.stable !== false).map((r) => `${r.id}=${r.raw ?? r.value}`);
  return hash(stable.join('\n'));
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}
