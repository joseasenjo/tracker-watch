// A plain summary of what the readings and the optional checks showed. It is a list of observations, each one tied to
// the evidence it comes from. It never says "safe" or "dangerous", gives no score, and says so when something was not
// checked. Pure functions: no browser features needed.
import { ACTIONS } from './actions.js';

export const SUMMARY_NOTE = 'This summary lists what this page could observe in this browser. It is not a security audit and not a verdict: ' +
  'it cannot see what any site does with the details, or what happens beyond your connection to this site.';

// Time zones that clearly belong to one country, for the coherence check. Countries that span many zones are left out
// on purpose: no statement is better than a wrong one.
const ZONES = {
  ES: ['Europe/Madrid', 'Africa/Ceuta', 'Atlantic/Canary'], PT: ['Europe/Lisbon', 'Atlantic/Azores', 'Atlantic/Madeira'],
  FR: ['Europe/Paris'], DE: ['Europe/Berlin', 'Europe/Busingen'], GB: ['Europe/London'], IE: ['Europe/Dublin'],
  IT: ['Europe/Rome'], NL: ['Europe/Amsterdam'], BE: ['Europe/Brussels'], CH: ['Europe/Zurich'], AT: ['Europe/Vienna'],
  SE: ['Europe/Stockholm'], NO: ['Europe/Oslo'], DK: ['Europe/Copenhagen'], FI: ['Europe/Helsinki'], PL: ['Europe/Warsaw'],
  CZ: ['Europe/Prague'], GR: ['Europe/Athens'], RO: ['Europe/Bucharest'], HU: ['Europe/Budapest'], JP: ['Asia/Tokyo'],
  IN: ['Asia/Kolkata', 'Asia/Calcutta'], AR: ['America/Argentina/Buenos_Aires', 'America/Buenos_Aires'],
  CL: ['America/Santiago'], CO: ['America/Bogota'], MX: ['America/Mexico_City'],
};

export function countryOfZone(zone) {
  for (const [country, zones] of Object.entries(ZONES)) if (zones.includes(zone)) return country;
  return null;
}

// -> { state: 'consistent' | 'different' | 'unknown', zoneCountry, addressCountry, languageCountry }
export function coherence({ zone, addressCountry, language }) {
  const zoneCountry = countryOfZone(zone);
  const region = /-([A-Za-z]{2})\b/.exec(language || '');
  const languageCountry = region ? region[1].toUpperCase() : null;
  const address = addressCountry ? String(addressCountry).toUpperCase() : null;
  if (!address || !(zoneCountry || languageCountry)) return { state: 'unknown', zoneCountry, addressCountry: address, languageCountry };
  const known = [zoneCountry, languageCountry].filter(Boolean);
  const state = known.every((c) => c === address) ? 'consistent' : 'different';
  return { state, zoneCountry, addressCountry: address, languageCountry };
}

const PICK = (map, id) => (map[id] || '').split('. ')[0].replace(/\.$/, '') + '.';

// by: results keyed by id ({ value, raw, randomised, hidden, on }), tracker: result of runTrackerTest or null,
// connection: result of runConnectionTest or null.
export function summarise({ by, tracker = null, connection = null, language = '', zone = '' }) {
  const observed = [];
  const worth = [];
  const change = [];

  const readable = Object.values(by).filter((r) => !/^Not available/.test(r.value)).length;
  observed.push(`A script on any page could read ${readable} details of this browser; they are listed below.`);

  const fonts = /^(\d+) of/.exec(by.fonts ? by.fonts.value : '');
  const noise = Boolean(by.canvas && by.canvas.randomised);
  const hiddenGpu = Boolean(by['webgl-renderer'] && /^Hidden by/.test(by['webgl-renderer'].value));
  if (noise && by.canvas.varies) worth.push('Your browser adds noise to the canvas drawing, so a script gets a different value each time. That makes the value less useful for recognising you, although a changing value can itself be noticed.');
  else if (noise) worth.push('A known colour drawn on a canvas came back different, so your browser alters or blocks what a script reads back from it. That is a fingerprint defence, and the value may stay the same during a visit.');
  if (hiddenGpu) worth.push('Your browser hides the name of the graphics card from pages.');
  if (!noise && !hiddenGpu) worth.push('None of the signs we check were seen that this browser hides or disturbs the details a script reads for a fingerprint. That is not a guarantee: only a few signs are checked.');
  if (by['privacy-signals'] && !by['privacy-signals'].on) worth.push('Do Not Track and Global Privacy Control are both off, so sites are not asked to avoid tracking you. They are requests that sites can ignore.');
  if (by['ad-filter']) {
    worth.push(by['ad-filter'].hidden
      ? 'A page element of the kind ad filters hide was hidden: a filter is active in this browser, and a page can notice it.'
      : 'No filter that hides page elements was detected. This check does not cover filters that work on the network; the tracker test does.');
  }

  if (tracker && tracker.ok) {
    const total = tracker.rows.length;
    const stopped = tracker.rows.filter((r) => r.state === 'blocked').length;
    const answered = tracker.rows.filter((r) => r.state === 'reachable').length;
    observed.push(`In the tracker test, the requests to ${stopped} of ${total} advertising and analytics servers were stopped and ${answered} got an answer.`);
    if (answered > 0) {
      worth.push('Some of those servers got an answer, so nothing stopped the requests from this page. A blocker that answers with a stand-in file also counts as an answer.');
      change.push({ id: 'ad-filter', text: 'Try a blocker or a browser with tracking protection, then run the tracker test again.' });
    }
  } else {
    observed.push('The tracker test has not been run, so this summary says nothing about whether trackers are stopped.');
  }

  if (connection && connection.trace) {
    const t = connection.trace;
    observed.push(`This connection to the site used ${t.tls || 'an unknown version of TLS'} over ${t.http || 'an unknown version of HTTP'}, so what travels between you and the site is encrypted. The site still sees your address.`);
    if (t.loc) observed.push(`Websites see an address that suggests ${t.loc}.`);
    const c = coherence({ zone, addressCountry: t.loc, language });
    if (c.state === 'consistent') worth.push(`Your time zone, your language and the country of your address agree (${c.addressCountry}).`);
    if (c.state === 'different') worth.push(`Your time zone or language point to ${[c.zoneCountry, c.languageCountry].filter(Boolean).join(' / ')} while your address suggests ${c.addressCountry}. This can be a VPN or travel; a script can see the difference.`);
    if (connection.rtc && connection.rtc.relation === 'different') {
      worth.push('WebRTC revealed an address other than the one websites see. With a VPN, that can mean the VPN does not cover WebRTC.');
      change.push({ id: null, text: 'Check whether your VPN or browser has a setting to stop WebRTC from revealing your address.' });
    }
    if (connection.rtc && connection.rtc.exposedLocal && connection.rtc.exposedLocal.length) {
      worth.push('WebRTC revealed an address from your local network, which current browsers usually hide.');
    }
  } else if (connection && !connection.trace) {
    observed.push('The connection check could not reach its service, so nothing about your address is shown.');
  } else {
    observed.push('The connection check has not been run, so this summary says nothing about your address or about WebRTC.');
  }

  const privacyOff = by['privacy-signals'] && !by['privacy-signals'].on;
  if (privacyOff) change.push({ id: 'privacy-signals', text: PICK(ACTIONS, 'privacy-signals') });
  if (!noise && !hiddenGpu) {
    change.push({ id: 'canvas', text: 'Pages can read a drawing signature from your browser that stays the same between visits. Brave adds small random noise to it, Tor Browser asks before allowing it, and Firefox can do the same with its fingerprinting protection.' });
  }
  if (fonts && Number(fonts[1]) >= 25) change.push({ id: 'fonts', text: PICK(ACTIONS, 'fonts') });

  return { observed, worth, change: change.slice(0, 3) };
}
