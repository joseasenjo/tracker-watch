"""Find a consent banner and press its "reject all" or "accept all" button. One click, nothing else.

Used only by the optional consent measurement (`python -m traceguard --consent reject,accept`). The main
weekly measurement never interacts with the page.

Detection is deliberately conservative:
- Known consent tools are tried first by their documented button ids/classes, in every frame.
- Otherwise a button is accepted only if its whole visible text is a known reject/accept phrase (English,
  German, Spanish, French, Italian) AND it sits inside a container that talks about cookies, consent or
  privacy. A link that would leave the page is never clicked.
- "Not found" means "our detector did not find it", never "the site has no such option".
"""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from .classify import registrable_domain

ACTIONS = ("reject", "accept")

# (tool, banner selectors, reject button selectors, accept button selectors)
CMPS = [
    ("OneTrust", "#onetrust-banner-sdk", "#onetrust-reject-all-handler", "#onetrust-accept-btn-handler"),
    ("Cookiebot", "#CybotCookiebotDialog", "#CybotCookiebotDialogBodyButtonDecline",
     "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll, #CybotCookiebotDialogBodyButtonAccept"),
    ("Didomi", "#didomi-notice, #didomi-popup", "#didomi-notice-disagree-button", "#didomi-notice-agree-button"),
    ("Quantcast", ".qc-cmp2-container", '.qc-cmp2-summary-buttons button[mode="secondary"]',
     '.qc-cmp2-summary-buttons button[mode="primary"]'),
    ("Google Funding Choices", ".fc-consent-root", ".fc-cta-do-not-consent", ".fc-cta-consent"),
    ("Sourcepoint", "div[id^='sp_message_container']", "button.sp_choice_type_13", "button.sp_choice_type_11"),
    ("Usercentrics", "#usercentrics-root, #uc-center-container", '[data-testid="uc-deny-all-button"]',
     '[data-testid="uc-accept-all-button"]'),
    ("CookieYes", ".cky-consent-container", ".cky-btn-reject", ".cky-btn-accept"),
    ("TrustArc", "#truste-consent-track", "#truste-consent-required", "#truste-consent-button"),
    ("Osano", ".osano-cm-dialog", ".osano-cm-denyAll", ".osano-cm-accept-all"),
    ("consentmanager", "#cmpbox", ".cmpboxbtnno", ".cmpboxbtnyes"),
]

REJECT_PHRASES = [
    # English
    "reject all", "reject", "reject all cookies", "reject cookies", "reject non-essential cookies",
    "reject additional cookies", "decline", "decline all", "decline cookies", "refuse", "refuse all", "deny",
    "deny all", "disagree", "i do not agree", "i don't agree", "do not accept", "continue without accepting",
    "necessary cookies only", "only necessary cookies", "use necessary cookies only", "essential cookies only",
    "only essential cookies", "reject optional cookies", "no, thanks", "no thanks",
    # German
    "alle ablehnen", "ablehnen", "nur notwendige", "nur notwendige cookies", "nur erforderliche cookies",
    "nur essenzielle cookies", "nur technisch notwendige cookies", "nicht einverstanden", "verweigern",
    "alle verweigern", "weiter ohne einwilligung", "ohne zustimmung fortfahren",
    # Spanish
    "rechazar", "rechazar todo", "rechazar todas", "rechazar cookies", "rechazar todas las cookies", "no acepto",
    "continuar sin aceptar", "solo necesarias", "solo cookies necesarias",
    # French
    "tout refuser", "refuser", "refuser tout", "je refuse", "continuer sans accepter",
    # Italian
    "rifiuta", "rifiuta tutto", "rifiuta tutti", "continua senza accettare",
]
ACCEPT_PHRASES = [
    # English
    "accept all", "accept", "accept cookies", "accept all cookies", "accept and continue", "accept & continue",
    "accept and close", "accept & close", "i accept", "agree", "i agree", "agree and close", "agree & close",
    "agree and continue", "yes, i agree", "yes i agree", "yes, i'm happy", "allow all", "allow cookies",
    "allow all cookies", "ok", "got it", "i understand",
    # German
    "alle akzeptieren", "akzeptieren", "alles akzeptieren", "alle cookies akzeptieren", "akzeptieren und weiter",
    "akzeptieren & weiter", "zustimmen", "alle zustimmen", "zustimmen und weiter", "zustimmen & weiter",
    "ich stimme zu", "einverstanden", "alle erlauben", "alle cookies erlauben",
    # Spanish
    "aceptar", "aceptar todo", "aceptar todas", "aceptar cookies", "aceptar todas las cookies",
    "aceptar y continuar", "aceptar y cerrar", "acepto", "de acuerdo", "consentir", "permitir todas",
    # French
    "tout accepter", "accepter", "accepter tout", "j'accepte", "accepter et fermer", "accepter et continuer",
    "d'accord",
    # Italian
    "accetta", "accetta tutto", "accetta tutti", "accetto",
]
CONTEXT_WORDS = (r"cookie|consent|privacy|datenschutz|einwillig|zustimm|privacidad|consentimiento|confidentialit"
                 r"|vie privée|gdpr|rgpd|dsgvo|personal data|personenbezogen|partner|vendor|tracking|tcf")

# A banner button offering a paid alternative ("pay or accept"). Recorded, never clicked.
PAID_WORDS = (r"subscri|abonn|\babo\b|pur-abo|pur abo|suscr|werbefrei|ohne werbung|ad-free|ad free|sans pub"
              r"|sin publicidad|senza pubblicit")

PAID_JS = r"""
({paid, accept}) => {
  // A paid alternative counts only when it sits in the same small block as the banner's accept button,
  // so subscription links elsewhere on the page (header, footer, "cancel subscription") are ignored.
  const paidRe = new RegExp(paid, "i");
  const notPaid = /kündig|désabonn|unsubscri|cancel|manage|verwalten|gestionar/i;
  const norm = (s) => (s || "").replace(/[‘’]/g, "'").replace(/\s+/g, " ").trim().toLowerCase().replace(/[.!]$/, "");
  const acceptSet = new Set(accept);
  const buttons = (root) => root.querySelectorAll("button, a, [role=button]");
  const found = [];
  const walk = (root) => {
    buttons(root).forEach((el) => {
      const text = (el.innerText || el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
      const r = el.getBoundingClientRect();
      if (text && text.length <= 80 && r.width > 3 && r.height > 3 && paidRe.test(text) && !notPaid.test(text)) found.push([el, text]);
    });
    root.querySelectorAll("*").forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });
  };
  walk(document);
  for (const [el, text] of found) {
    let node = el.parentElement;
    for (let i = 0; i < 6 && node && node.tagName !== "BODY" && node.tagName !== "HTML"; i++, node = node.parentElement) {
      if ((node.innerText || "").length > 3000) break;
      if ([...buttons(node)].some((b) => b !== el && acceptSet.has(norm(b.innerText || b.getAttribute("aria-label"))))) return text;
    }
  }
  return null;
}
"""

FIND_JS = r"""
({phrases, context, token}) => {
  const norm = (s) => (s || "").replace(/[‘’]/g, "'").replace(/\s+/g, " ").trim().toLowerCase().replace(/[.!]$/, "");
  const wanted = new Set(phrases);
  const contextRe = new RegExp(context, "i");
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && parseFloat(s.opacity || "1") > 0.05;
  };
  const leaves = (el) => {  // a link that would navigate away is never a consent button
    if (el.tagName !== "A") return false;
    const href = (el.getAttribute("href") || "").trim();
    return !(href === "" || href === "#" || href.startsWith("javascript:"));
  };
  // The button must sit in a small block (not the whole page) whose text talks about cookies, consent or
  // privacy; inside a consent tool's own frame, the frame address is enough.
  const inContext = (el) => {
    let node = el.parentElement || (el.getRootNode && el.getRootNode().host) || null;
    for (let i = 0; i < 10 && node && node.tagName !== "BODY" && node.tagName !== "HTML"; i++) {
      const text = node.innerText || node.textContent || "";
      if (text.length > 3000) break;
      if (contextRe.test(text)) return true;
      node = node.parentElement || (node.getRootNode && node.getRootNode().host) || null;
    }
    return window !== window.top && /privacy|consent|cmp|sourcepoint|cookie/i.test(location.href);
  };
  const candidates = [];
  const walk = (root) => {
    root.querySelectorAll("button, a, [role=button], input[type=button], input[type=submit]").forEach((el) => candidates.push(el));
    root.querySelectorAll("*").forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); });
  };
  walk(document);
  for (const el of candidates) {
    const text = norm(el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("title"));
    if (!text || text.length > 60 || !wanted.has(text)) continue;
    if (!visible(el) || leaves(el) || !inContext(el)) continue;
    el.setAttribute("data-tg-consent", token);
    return text;
  }
  return null;
}
"""


def _visible(frame, selector: str):
    try:
        locator = frame.locator(selector).first
        if locator.count() and locator.is_visible():
            return locator
    except Exception:
        pass
    return None


def _button_text(locator) -> str | None:
    try:
        text = (locator.inner_text(timeout=1000) or locator.get_attribute("value") or "").strip()
        return " ".join(text.split())[:60] or None
    except Exception:
        return None


def _frames(page):
    return [f for f in page.frames if not f.is_detached()][:25]


def find_button(page, action: str) -> dict:
    """Locate the banner and the button for `action` without clicking.

    Returns {"banner": bool, "cmp": str|None, "method": "selector"|"text"|None, "frame_host": str|None,
             "button_text": str|None, "_locator": locator|None}.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown consent action {action!r}")
    found = {"banner": False, "cmp": None, "method": None, "frame_host": None, "button_text": None, "_locator": None}
    frames = _frames(page)
    for frame in frames:  # 1. known consent tools
        for name, banner, reject, accept in CMPS:
            button = _visible(frame, reject if action == "reject" else accept)
            other = _visible(frame, accept if action == "reject" else reject)
            if button or other or _visible(frame, banner):
                found.update(banner=True, cmp=found["cmp"] or name)
            if button:
                found.update(cmp=name, method="selector", frame_host=urlsplit(frame.url).hostname,
                             button_text=_button_text(button), _locator=button)
                return found
    for wanted, phrases in ((True, REJECT_PHRASES if action == "reject" else ACCEPT_PHRASES),
                            (False, ACCEPT_PHRASES if action == "reject" else REJECT_PHRASES)):
        for frame in frames:  # 2. visible text, inside a consent/privacy container
            token = secrets.token_hex(6)
            try:
                text = frame.evaluate(FIND_JS, {"phrases": phrases, "context": CONTEXT_WORDS, "token": token})
            except Exception:
                continue
            if not text:
                continue
            found["banner"] = True
            if wanted:
                found.update(method="text", frame_host=urlsplit(frame.url).hostname, button_text=text,
                             _locator=frame.locator(f'[data-tg-consent="{token}"]').first)
                return found
            break  # the opposite button proves there is a banner; keep looking for nothing else
    return found


def paid_option(page) -> str | None:
    """Text of a banner button that offers a paid alternative (e.g. "Reject all and subscribe"), if any."""
    for frame in _frames(page):
        try:
            text = frame.evaluate(PAID_JS, {"paid": PAID_WORDS, "accept": ACCEPT_PHRASES})
        except Exception:
            continue
        if text:
            return " ".join(text.split())[:80]
    return None


def act(page, action: str, settle_ms: int = 1500) -> dict:
    """Click the `action` button of the consent banner, once. Returns a JSON-safe record of what happened.

    outcome: "clicked", "no_banner" (nothing detected), "no_button" (a banner was detected but not the
    button for this action, e.g. no reject on the first layer), "click_failed".
    """
    before = registrable_domain(urlsplit(page.url).hostname or "")
    found = find_button(page, action)
    locator = found.pop("_locator")
    record = {"action": action, **found, "outcome": None, "navigated_away": False}
    if locator is None:
        record["outcome"] = "no_button" if found["banner"] else "no_banner"
        if found["banner"]:
            record["paid_option"] = paid_option(page)
        return record
    try:
        locator.click(timeout=4000)
        record["outcome"] = "clicked"
    except Exception as exc:
        record["outcome"] = "click_failed"
        record["error"] = type(exc).__name__
        return record
    page.wait_for_timeout(settle_ms)
    after = registrable_domain(urlsplit(page.url).hostname or "")
    record["navigated_away"] = bool(before and after and before != after)
    return record
