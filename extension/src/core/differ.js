// F4 "Why your number can differ": reasons computed from this visit, not a fixed text. Each reason is a fact
// observed here (or a stated limit of the weekly measurement); none is a guess about a particular company.
// Returns message ids with arguments; the panel turns them into sentences.

/**
 * @typedef {{ id: string, args: Array<string | number> }} Reason
 */

const DAY = 86400000;

/**
 * @param {ReturnType<import('./report.js').summarizePage>} summary
 * @param {import('./page.js').PageState} page
 * @param {ReturnType<import('./report.js').compareWithBaseline> | null} baseline
 * @param {{ browser: 'chromium' | 'firefox', nowMs: number }} env
 * @returns {Reason[]}
 */
export function differenceReasons(summary, page, baseline, env) {
  /** @type {Reason[]} */
  const out = [];
  const services = summary.operators.flatMap((o) => o.services);
  const consentTools = [...new Set(services.filter((s) => s.category === 'consent_management').map((s) => s.entity))];
  const tagManagers = services.filter((s) => s.category === 'tag_manager').map((s) => s.service);

  if (summary.stopped.client) out.push({ id: 'whyExtension', args: [summary.stopped.client] });
  if (summary.stopped.browser) out.push({ id: 'whyBrowserProtection', args: [summary.stopped.browser] });
  if (summary.stopped.cancelled) out.push({ id: 'whyCancelled', args: [summary.stopped.cancelled] });
  if (baseline && env.browser === 'firefox') out.push({ id: 'whyOtherBrowser', args: ['Firefox'] });

  const banners = page.consent.banners;
  if (page.consent.previous) {
    out.push({ id: 'whyAnsweredJustBefore', args: [page.consent.previous.tool || 'consent'] });
  } else if (page.interactionAt !== null) {
    out.push({ id: 'whyClicked', args: [] });
  } else if (banners.length) {
    out.push({ id: 'whyBannerWaiting', args: [banners.join(', ')] });
  } else if (consentTools.length) {
    // contacted but nothing on screen: possibly answered on an earlier visit (we cannot know which answer)
    out.push({ id: 'whyAnsweredBefore', args: [consentTools.join(', ')] });
  }

  const recognised = Object.keys(page.told.recognised || {}).length;
  if (recognised) out.push({ id: 'whyRecognised', args: [recognised] });
  if (summary.cachedRequests) out.push({ id: 'whyCache', args: [summary.cachedRequests] });
  if (tagManagers.length) out.push({ id: 'whyTagManagers', args: [tagManagers.join(', ')] });

  if (baseline) {
    const lang = page.told.self && page.told.self.language ? page.told.self.language.split(',')[0] : null;
    out.push({ id: 'whyPlace', args: [baseline.vantage || '?', lang || '?'] });
    const age = Math.floor((env.nowMs - Date.parse(baseline.date + 'T00:00:00Z')) / DAY);
    if (age > 7) out.push({ id: 'whyAge', args: [age] });
  }
  if (summary.truncated) out.push({ id: 'whyTruncated', args: [] });
  out.push({ id: 'whyAuctions', args: [] });
  return out;
}
