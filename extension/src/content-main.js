// Runs in every frame in the page's own world (MAIN), before the page's scripts, to notice the same API calls
// as the weekly engine (traceguard/scanner.py INIT_SCRIPT): reading a canvas, asking for the location,
// opening a WebRTC connection. For each call it passes on only the kind and the host of the calling script
// (from the stack), once per kind and host, to the extension's isolated script through a DOM event.
// The page can see these wrappers and could fake the event: what arrives is an indication, never proof.
// It changes nothing else: the wrapped functions keep `this`, their arguments and their results.
(() => {
  const EVENT = 'trackerwatch-lens:behaviour';
  const seen = new Set();
  const caller = () => {
    try {
      const urls = (new Error().stack || '').match(/https?:\/\/[^\s)\]]+/g);
      return urls && urls.length ? new URL(urls[0]).hostname : '';
    } catch { return ''; }
  };
  const record = (kind) => {
    const host = caller();
    const key = kind + ' ' + host;
    if (seen.has(key) || seen.size >= 100) return;
    seen.add(key);
    try { document.dispatchEvent(new CustomEvent(EVENT, { detail: JSON.stringify({ kind, host }) })); } catch { /* ignore */ }
  };
  const wrap = (target, name, kind) => {
    try {
      const original = target && target[name];
      if (typeof original !== 'function') return;
      target[name] = function () { record(kind); return original.apply(this, arguments); };
    } catch { /* not writable */ }
  };
  wrap(HTMLCanvasElement.prototype, 'toDataURL', 'canvas_read');
  wrap(HTMLCanvasElement.prototype, 'toBlob', 'canvas_read');
  if (window.CanvasRenderingContext2D) wrap(CanvasRenderingContext2D.prototype, 'getImageData', 'canvas_read');
  if (window.Geolocation) {
    wrap(Geolocation.prototype, 'getCurrentPosition', 'geolocation_request');
    wrap(Geolocation.prototype, 'watchPosition', 'geolocation_request');
  }
  if (window.RTCPeerConnection) {
    try {
      window.RTCPeerConnection = new Proxy(window.RTCPeerConnection, {
        construct(target, args, newTarget) { record('webrtc_connection'); return Reflect.construct(target, args, newTarget); },
      });
    } catch { /* not writable */ }
  }
})();
