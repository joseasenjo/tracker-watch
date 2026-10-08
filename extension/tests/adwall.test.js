// Walls that ask the visitor to turn off the ad blocker: which short texts count (the rules are in profiles/adwall.json,
// the same file content.js carries; the page-level behaviour is covered by the E2E test).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const rules = JSON.parse(readFileSync(new URL('../profiles/adwall.json', import.meta.url), 'utf8'));
const phrase = new RegExp(rules.phrase, 'i');
const action = new RegExp(rules.action, 'i');
const isWall = (text) => {
  const t = text.replace(/\s+/g, ' ').trim();
  return t.length >= rules.min_chars && t.length <= rules.max_chars && phrase.test(t) && action.test(t);
};

const WALLS = {
  'ABC (es)': 'Estás utilizando un bloqueador de anuncios. La publicidad permite que podamos ofrecerte cada día información de calidad. Para seguir navegando necesitarás desactivar tu bloqueador de anuncios o, si lo prefieres, suscribirte a ABC Premium.',
  'SPORT (es)': 'Permite los anuncios para apoyar el periodismo. Parece que tienes un navegador, extensión, conexión o antivirus que bloquea la publicidad en nuestro sitio. La publicidad es la única forma de que nuestro trabajo sea posible. Desactiva el bloqueo de anuncios en Sport.',
  'English': "We noticed you're using an ad blocker. Please disable it or subscribe to keep reading.",
  'English, blocking ads': 'It looks like you are blocking ads. Allow ads on our site to continue.',
  'German': 'Bitte deaktivieren Sie Ihren Adblocker, um diese Seite weiter zu nutzen.',
  'French': 'Nous avons détecté un bloqueur de publicités. Désactivez-le pour continuer à lire.',
  'Italian': 'Abbiamo rilevato un blocco annunci attivo. Disattivalo per continuare.',
  'Portuguese': 'Detetámos um bloqueador de anúncios. Desative-o para continuar.',
};

const NOT_WALLS = {
  'an article about ad blockers (no request)': 'Los bloqueadores de anuncios son cada vez más populares entre los usuarios de internet según un estudio.',
  'a cookie banner': 'Usamos cookies propias y de terceros para mejorar tu experiencia. Acepta o configura tus preferencias.',
  'a subscription box': 'Suscríbete a nuestra newsletter y recibe cada mañana las noticias más importantes en tu correo.',
  'too short': 'Adblock: desactiva',
  'a whole article body': `Los bloqueadores de anuncios han cambiado la economía de la prensa. ${'Los editores buscan nuevas fórmulas y los lectores piden que se respete su privacidad. '.repeat(14)} Permite que el debate continúe.`,
  'a normal page': 'Noticias de última hora, deportes, economía y opinión. Lee las últimas informaciones del día en nuestra portada.',
};

for (const [name, text] of Object.entries(WALLS)) {
  test(`a wall: ${name}`, () => assert.ok(isWall(text), text));
}
for (const [name, text] of Object.entries(NOT_WALLS)) {
  test(`not a wall: ${name}`, () => assert.ok(!isWall(text), text));
}
