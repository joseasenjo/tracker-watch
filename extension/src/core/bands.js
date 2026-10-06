// Fixed bands A–E for the number of tracking services (traceguard.bands), read from data/glossary.json.
// A band is a labelled range of a count, not a verdict. Whether a result is solid enough to get one
// is the caller's decision (the weekly data needs high or medium confidence; a live page is one visit).

/**
 * @typedef {{ label: string, low: number, high: number | null }} Band
 */

/**
 * @param {number | null | undefined} count
 * @param {Band[]} bands
 * @returns {string | null}
 */
export function bandFor(count, bands) {
  if (count === null || count === undefined || !Number.isInteger(count) || count < 0) return null;
  for (const b of bands) {
    if (count >= b.low && (b.high === null || count <= b.high)) return b.label;
  }
  return null;
}
