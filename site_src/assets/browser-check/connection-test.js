// The optional connection test. Like the tracker test, it runs only when the visitor presses its button, because it
// asks two outside services: Cloudflare's public "trace" page (what any website sees of your connection) and
// Cloudflare's public STUN server (what a page can learn through WebRTC). The answers are shown to the visitor and
// nothing is stored or reported anywhere.
export const TRACE_URL = 'https://www.cloudflare.com/cdn-cgi/trace';
export const STUN_URL = 'stun:stun.cloudflare.com:3478';

export function parseTrace(text) {
  const out = {};
  for (const line of String(text).split('\n')) {
    const at = line.indexOf('=');
    if (at > 0) out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return out;
}

// "candidate:842163049 1 udp 1677729535 203.0.113.7 54400 typ srflx raddr ..." -> { type, address }
export function parseCandidate(line) {
  const parts = String(line).trim().split(/\s+/);
  const typ = parts.indexOf('typ');
  if (parts.length < 8 || typ < 0) return null;
  return { type: parts[typ + 1], address: parts[4] };
}

// Compares the address a website sees with the ones WebRTC revealed.
export function compareAddresses(seen, candidates) {
  const publicOnes = [...new Set(candidates.filter((c) => c.type === 'srflx').map((c) => c.address))];
  const local = [...new Set(candidates.filter((c) => c.type === 'host').map((c) => c.address))];
  const hidden = local.filter((a) => a.endsWith('.local')).length;
  const exposedLocal = local.filter((a) => !a.endsWith('.local'));
  let relation;
  if (!publicOnes.length) relation = 'none';
  else if (publicOnes.every((a) => a === seen)) relation = 'same';
  else relation = 'different';
  return { publicOnes, exposedLocal, hiddenLocal: hidden, relation };
}

export async function fetchTrace(fetchFn, url = TRACE_URL) {
  const response = await fetchFn(url, { cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer' });
  return parseTrace(await response.text());
}

export function gatherCandidates(RTC, timeoutMs = 4000) {
  return new Promise((resolve) => {
    const found = [];
    let pc;
    const finish = () => {
      try { pc.close(); } catch (error) { /* already closed */ }
      resolve(found);
    };
    try {
      pc = new RTC({ iceServers: [{ urls: STUN_URL }] });
      pc.createDataChannel('check');
      pc.onicecandidate = (event) => {
        if (!event.candidate) return finish();
        const parsed = parseCandidate(event.candidate.candidate);
        if (parsed) found.push(parsed);
      };
      pc.createOffer().then((offer) => pc.setLocalDescription(offer)).catch(finish);
      setTimeout(finish, timeoutMs);
    } catch (error) {
      resolve(found);
    }
  });
}

export async function runConnectionTest({ fetchFn, RTC }) {
  let trace = null;
  try {
    trace = await fetchTrace(fetchFn);
  } catch (error) {
    trace = null;
  }
  const candidates = RTC ? await gatherCandidates(RTC) : [];
  return { trace, rtc: trace ? compareAddresses(trace.ip, candidates) : compareAddresses('', candidates), rtcAvailable: Boolean(RTC) };
}
