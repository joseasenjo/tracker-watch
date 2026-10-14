// One collector per characteristic. Each returns { value, raw?, stable? } and never throws: a detail the browser
// refuses to give is reported as such. Nothing here makes a network request or stores anything.
import { classifyPixels, detectFonts, hashText, shorten } from './util.js';

const NA = 'Not available';
const FONTS = ['Arial', 'Arial Black', 'Arial Narrow', 'Bookman Old Style', 'Calibri', 'Cambria', 'Cambria Math',
  'Century Gothic', 'Comic Sans MS', 'Consolas', 'Courier', 'Courier New', 'Garamond', 'Georgia', 'Helvetica',
  'Helvetica Neue', 'Impact', 'Lucida Console', 'Lucida Sans Unicode', 'Microsoft Sans Serif', 'MS Gothic',
  'Palatino Linotype', 'Segoe Print', 'Segoe Script', 'Segoe UI', 'Segoe UI Light', 'Segoe UI Semibold',
  'Segoe UI Symbol', 'Tahoma', 'Times', 'Times New Roman', 'Trebuchet MS', 'Verdana', 'Wingdings', 'Menlo', 'Monaco',
  'Optima', 'Gill Sans', 'Futura', 'Avenir', 'San Francisco', 'Apple Color Emoji', 'Roboto', 'Noto Sans',
  'Ubuntu', 'DejaVu Sans', 'Liberation Sans', 'Cantarell', 'Open Sans', 'Lato', 'Source Sans Pro', 'Fira Sans',
  'Candara', 'Corbel', 'Franklin Gothic Medium', 'Rockwell', 'Baskerville', 'Didot', 'Hoefler Text'];

const safe = (fn, fallback = NA) => async () => {
  try {
    const out = await fn();
    return out === undefined || out === null || out === '' ? { value: fallback } : out;
  } catch (error) {
    return { value: fallback };
  }
};

function drawCanvas() {
  const canvas = document.createElement('canvas');
  canvas.width = 280;
  canvas.height = 60;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.textBaseline = 'alphabetic';
  ctx.fillStyle = '#f60';
  ctx.fillRect(125, 1, 62, 20);
  ctx.fillStyle = '#069';
  ctx.font = '15px Arial, sans-serif';
  ctx.fillText('Browser check, ☂ ♞ 😀 àéîõü', 2, 15);
  ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
  ctx.font = '18px Times, serif';
  ctx.fillText('Browser check, ☂ ♞ 😀 àéîõü', 4, 45);
  ctx.globalCompositeOperation = 'multiply';
  ctx.fillStyle = 'rgb(255,0,255)';
  ctx.beginPath(); ctx.arc(50, 50, 50, 0, Math.PI * 2, true); ctx.closePath(); ctx.fill();
  return canvas.toDataURL();
}

// Draws a known colour and reads it back: a browser that adds noise or blocks the reading returns something else.
function canvasIntegrity() {
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 16;
    canvas.height = 16;
    const ctx = canvas.getContext('2d');
    if (!ctx) return 'unavailable';
    ctx.fillStyle = 'rgb(37, 99, 235)';
    ctx.fillRect(0, 0, 16, 16);
    return classifyPixels(ctx.getImageData(0, 0, 16, 16).data, [37, 99, 235, 255]);
  } catch (error) {
    return 'blocked';
  }
}

function webglContext() {
  const canvas = document.createElement('canvas');
  return canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
}

function fontMeasurer() {
  const ctx = document.createElement('canvas').getContext('2d');
  const text = 'mmmmmmmmmmlliWQ@#0123456789';
  return (font) => {
    ctx.font = `64px ${font}`;
    return ctx.measureText(text).width;
  };
}

async function audioValue() {
  const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  if (!Offline) return null;
  const context = new Offline(1, 44100, 44100);
  const oscillator = context.createOscillator();
  oscillator.type = 'triangle';
  oscillator.frequency.value = 10000;
  const compressor = context.createDynamicsCompressor();
  for (const [key, value] of [['threshold', -50], ['knee', 40], ['ratio', 12], ['attack', 0], ['release', 0.25]]) {
    if (compressor[key] && compressor[key].setValueAtTime) compressor[key].setValueAtTime(value, context.currentTime);
  }
  oscillator.connect(compressor);
  compressor.connect(context.destination);
  oscillator.start(0);
  const buffer = await new Promise((resolve, reject) => {
    context.oncomplete = (event) => resolve(event.renderedBuffer);
    const started = context.startRendering();
    if (started && started.catch) started.catch(reject);
    setTimeout(() => reject(new Error('timeout')), 4000);
  });
  const data = buffer.getChannelData(0);
  let sum = 0;
  for (let i = 4500; i < 5000; i++) sum += Math.abs(data[i]);
  return sum;
}

function storageAvailable(name) {
  try {
    return Boolean(window[name]); // reading the property throws when the browser blocks it; nothing is written
  } catch (error) {
    return false;
  }
}

export const COLLECTORS = {
  'user-agent': safe(async () => {
    let value = navigator.userAgent;
    const hints = navigator.userAgentData;
    if (hints && hints.brands) {
      value += `\nClient hints: ${hints.brands.map((b) => `${b.brand} ${b.version}`).join(', ')}; mobile: ${hints.mobile}`;
    }
    return { value };
  }),
  languages: safe(async () => {
    const list = (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language]).filter(Boolean);
    return { value: `${list.join(', ')} (preferred: ${navigator.language})`, raw: list.join(',') };
  }),
  'privacy-signals': safe(async () => {
    const dnt = navigator.doNotTrack || window.doNotTrack || navigator.msDoNotTrack;
    const gpc = navigator.globalPrivacyControl;
    const dntText = dnt === '1' || dnt === 'yes' ? 'on' : dnt === '0' || dnt === 'no' ? 'off' : 'not set';
    const gpcText = gpc === true ? 'on' : gpc === false ? 'off' : 'not reported';
    return { value: `Do Not Track: ${dntText}; Global Privacy Control: ${gpcText}`, on: dntText === 'on' || gpcText === 'on' };
  }),
  cookies: safe(async () => ({ value: navigator.cookieEnabled ? 'Yes' : 'No' })),
  storage: safe(async () => {
    const parts = [`local storage: ${storageAvailable('localStorage') ? 'yes' : 'no'}`,
      `session storage: ${storageAvailable('sessionStorage') ? 'yes' : 'no'}`,
      `IndexedDB: ${window.indexedDB ? 'yes' : 'no'}`];
    return { value: parts.join(', ') };
  }),
  plugins: safe(async () => {
    const names = Array.from(navigator.plugins || [], (p) => p.name);
    return { value: names.length ? names.join('; ') : 'None reported', raw: names.join(';') };
  }),
  timezone: safe(async () => {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const offset = new Date().getTimezoneOffset();
    return { value: `${zone}, offset ${offset} min from universal time`, zone };
  }),
  screen: safe(async () => ({
    value: `${screen.width}x${screen.height}, colour depth ${screen.colorDepth}, pixel ratio ${window.devicePixelRatio}`,
    raw: `${screen.width}x${screen.height}x${screen.colorDepth}` })),
  fonts: safe(async () => {
    // The first measurements after the page opens can run before the browser has settled its font fallbacks: wait for
    // the fonts to be ready and measure again until three passes in a row agree (at most fifteen passes).
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    let found = detectFonts(FONTS, ['monospace', 'sans-serif', 'serif'], fontMeasurer());
    let agreeing = 0;
    for (let pass = 0; pass < 15 && agreeing < 3; pass++) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      const again = detectFonts(FONTS, ['monospace', 'sans-serif', 'serif'], fontMeasurer());
      agreeing = again.join() === found.join() ? agreeing + 1 : 0;
      found = again;
    }
    return { value: found.length ? `${found.length} of ${FONTS.length} tested: ${found.join(', ')}` : `0 of ${FONTS.length} tested`,
      raw: found.join(',') };
  }),
  canvas: safe(async () => {
    const first = drawCanvas();
    if (first === null) return { value: NA };
    const second = drawCanvas();
    const code = await hashText(first);
    const varies = first !== second;
    const integrity = canvasIntegrity();
    const altered = integrity === 'altered' || integrity === 'blank' || integrity === 'blocked';
    const note = varies ? ' (changes on every read: your browser adds noise)'
      : altered ? ' (a known colour came back different: your browser alters or blocks what a script reads back)' : '';
    return { value: `${shorten(code)}…${note}`, raw: varies ? 'randomised' : code, randomised: varies || altered,
      varies, integrity, stable: !varies };
  }),
  webgl: safe(async () => {
    const gl = webglContext();
    if (!gl) return { value: NA };
    const names = ['MAX_TEXTURE_SIZE', 'MAX_VIEWPORT_DIMS', 'MAX_VERTEX_ATTRIBS', 'MAX_VARYING_VECTORS',
      'ALIASED_LINE_WIDTH_RANGE', 'ALIASED_POINT_SIZE_RANGE', 'MAX_RENDERBUFFER_SIZE', 'MAX_COMBINED_TEXTURE_IMAGE_UNITS'];
    const params = names.map((n) => `${n}=${gl.getParameter(gl[n])}`);
    const extensions = (gl.getSupportedExtensions() || []).sort().join(',');
    const code = await hashText(`${params.join(';')}|${extensions}`);
    return { value: `${shorten(code)}…`, raw: code };
  }),
  'webgl-renderer': safe(async () => {
    const gl = webglContext();
    if (!gl) return { value: NA };
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    if (!info) return { value: 'Hidden by the browser' };
    return { value: `${gl.getParameter(info.UNMASKED_VENDOR_WEBGL)} ~ ${gl.getParameter(info.UNMASKED_RENDERER_WEBGL)}` };
  }),
  audio: safe(async () => {
    const sum = await audioValue();
    if (sum === null) return { value: NA };
    const code = await hashText(String(sum));
    return { value: `${shorten(code)}… (sample sum ${sum.toFixed(6)})`, raw: code };
  }),
  platform: safe(async () => ({ value: navigator.platform })),
  touch: safe(async () => ({
    value: `Touch points: ${navigator.maxTouchPoints || 0}; touch events: ${'ontouchstart' in window ? 'yes' : 'no'}` })),
  'cpu-class': safe(async () => ({ value: navigator.cpuClass || NA })),
  cores: safe(async () => ({ value: String(navigator.hardwareConcurrency) })),
  memory: safe(async () => ({ value: navigator.deviceMemory ? `${navigator.deviceMemory} GB` : NA })),
  'client-hints': safe(async () => {
    const hints = navigator.userAgentData;
    if (!hints || !hints.getHighEntropyValues) return { value: NA };
    const high = await hints.getHighEntropyValues(['architecture', 'bitness', 'model', 'platformVersion', 'uaFullVersion']);
    const parts = Object.entries(high).filter(([key, value]) => key !== 'brands' && key !== 'mobile' && value !== '' && value !== undefined)
      .map(([key, value]) => `${key}: ${Array.isArray(value) ? JSON.stringify(value) : value}`);
    return { value: parts.join('; ') || NA };
  }),
  preferences: safe(async () => {
    const asks = [['colour scheme', ['dark', 'light'], 'prefers-color-scheme'], ['motion', ['reduce'], 'prefers-reduced-motion'],
      ['contrast', ['more', 'less'], 'prefers-contrast'], ['colour gamut', ['rec2020', 'p3', 'srgb'], 'color-gamut'],
      ['pointer', ['fine', 'coarse', 'none'], 'pointer'], ['hover', ['hover', 'none'], 'hover']];
    const parts = asks.map(([label, options, query]) => {
      const hit = options.find((o) => window.matchMedia(`(${query}: ${o})`).matches);
      return `${label}: ${hit || 'default'}`;
    });
    if (window.matchMedia('(forced-colors: active)').matches) parts.push('forced colours: on');
    if (window.matchMedia('(dynamic-range: high)').matches) parts.push('high dynamic range: yes');
    return { value: parts.join('; ') };
  }),
  connection: safe(async () => {
    const c = navigator.connection;
    if (!c) return { value: NA };
    return { value: `${c.effectiveType || 'unknown'}, about ${c.downlink ?? '?'} Mbit/s, data saver: ${c.saveData ? 'on' : 'off'}`, stable: false };
  }),
  voices: safe(async () => {
    if (!window.speechSynthesis) return { value: NA };
    // Browsers load the voice list in the background: wait until the count stops changing (at most 2 seconds).
    let voices = speechSynthesis.getVoices();
    let steady = 0;
    for (let waited = 0; waited < 2000 && steady < 3; waited += 100) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      const now = speechSynthesis.getVoices();
      steady = now.length === voices.length && now.length > 0 ? steady + 1 : 0;
      voices = now;
    }
    if (!voices.length) return { value: 'None reported' };
    const names = voices.map((v) => `${v.name} (${v.lang})`);
    return { value: `${names.length} voices: ${names.slice(0, 6).join(', ')}${names.length > 6 ? ', …' : ''}`, raw: names.sort().join(',') };
  }),
  codecs: safe(async () => {
    const media = ['audio/mpeg', 'audio/ogg; codecs="vorbis"', 'audio/ogg; codecs="opus"', 'audio/aac', 'audio/flac',
      'audio/webm; codecs="opus"', 'video/mp4; codecs="avc1.42E01E"', 'video/mp4; codecs="hev1.1.6.L93.B0"',
      'video/mp4; codecs="av01.0.05M.08"', 'video/webm; codecs="vp8"', 'video/webm; codecs="vp9"', 'video/ogg; codecs="theora"'];
    const probe = document.createElement('video');
    const ok = media.filter((type) => probe.canPlayType(type) !== '');
    return { value: `${ok.length} of ${media.length} tested: ${ok.join(' | ')}`, raw: ok.join('|') };
  }),
  webgpu: safe(async () => {
    if (!navigator.gpu || !navigator.gpu.requestAdapter) return { value: NA };
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) return { value: 'No adapter available' };
    const info = adapter.info || (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : null);
    if (!info) return { value: 'Available, details hidden' };
    const parts = ['vendor', 'architecture', 'device', 'description'].filter((k) => info[k]).map((k) => `${k}: ${info[k]}`);
    return { value: parts.join('; ') || 'Available, details hidden' };
  }),
  intl: safe(async () => {
    const options = Intl.DateTimeFormat().resolvedOptions();
    const sample = new Date(Date.UTC(2020, 11, 31, 23, 59, 58));
    const parts = [`locale ${options.locale}`, `calendar ${options.calendar}`, `numbering ${options.numberingSystem}`,
      `number ${new Intl.NumberFormat().format(1234567.891)}`, `date ${sample.toLocaleDateString()}`,
      `time ${sample.toLocaleTimeString()}`, `hour cycle ${options.hourCycle || 'default'}`];
    return { value: parts.join('; ') };
  }),
  emoji: safe(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 220;
    canvas.height = 40;
    const ctx = canvas.getContext('2d');
    if (!ctx) return { value: NA };
    ctx.font = '28px sans-serif';
    ctx.textBaseline = 'top';
    ctx.fillText('😀🧑‍🚀🏳️‍🌈🫠🐢', 2, 4);
    const code = await hashText(canvas.toDataURL());
    return { value: `${shorten(code)}…`, raw: code };
  }),
  'ad-filter': safe(async () => {
    const bait = document.createElement('div');
    bait.className = 'adsbox ad-banner ad_unit textads banner-ads sponsored-ad';
    bait.style.cssText = 'position:absolute;left:-9999px;top:0;width:10px;height:10px;';
    bait.innerHTML = '&nbsp;';
    document.body.appendChild(bait);
    await new Promise((resolve) => setTimeout(resolve, 150));
    const hidden = bait.offsetHeight === 0 || getComputedStyle(bait).display === 'none';
    bait.remove();
    return { value: hidden ? 'An element of that kind was hidden: a filter is active' : 'Not hidden: no filter of that kind detected', hidden };
  }),
};
