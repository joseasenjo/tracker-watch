"""Clean mode (F14) rule sets for declarativeNetRequest, generated from the same data as the analysis.

- verified.json / full.json: one rule each that stops third-party requests to the tracking services of the list
  (verified entries only, or all), every resource type except the page itself. Same selection as the filter
  lists trackerwatch-verified.txt / -full.txt: tag managers, consent tools and paywalls are never included.
- params.json: removes known tracking parameters (src/core/headers.js TRACKING_PARAMS) from the address of the
  pages you open.
- siteonly.json ("site only" mode, used with full + easyprivacy): stops every third-party script, frame and
  connection; images, styles, fonts and media still load (the tracker lists stop those of trackers). Allowed
  anyway: the other domains of the measured sites (data/sites.json first_party_domains, e.g. elmundo.es ->
  uecdn.es), a short list of sign-in, payment, captcha and video services (SAFE below), and the consent
  tools and paywalls of our list.
All are shipped disabled; the user turns them on from the settings page.

Priorities (the highest matching rule wins; allowAllRequests covers everything a frame loads below it):
  1 site only block < 2 allows of site only (static, and the ones added per site from the panel)
  < 3 tracker lists (also inside allowed frames) < 4 EasyPrivacy exceptions < 10 pause on a site.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RESOURCE_TYPES = ["sub_frame", "stylesheet", "script", "image", "font", "object", "xmlhttprequest", "ping",
                  "csp_report", "media", "websocket", "other"]
# what "site only" stops from other sites: code, frames and connections (never images, styles, fonts, media)
SITEONLY_TYPES = ["script", "sub_frame", "xmlhttprequest", "ping", "websocket", "object", "other"]
P_SITEONLY, P_ALLOW, P_LIST, P_LIST_EXCEPTION = 1, 2, 3, 4

# Allowed by default in "site only" mode, so that signing in, paying, solving a captcha and embedded videos keep
# working. Frames get allowAllRequests (what they load works too; the tracker lists still apply inside them).
SAFE_DOMAINS = [
    "accounts.google.com",  # Sign in with Google
    "appleid.apple.com", "appleid.cdn-apple.com",  # Sign in with Apple
    "js.stripe.com", "m.stripe.network", "api.stripe.com", "hooks.stripe.com",  # Stripe payments
    "paypal.com", "paypalobjects.com",  # PayPal
    "hcaptcha.com", "recaptcha.net", "challenges.cloudflare.com",  # captchas
    "youtube.com", "youtube-nocookie.com", "player.vimeo.com",  # embedded videos
]
SAFE_URLS = ["||google.com/recaptcha/", "||gstatic.com/recaptcha/"]  # reCAPTCHA (not the rest of Google)
SAFE_CATEGORIES = {"consent_management", "paywall"}


def tracking_domains(trackers: dict, verified_only: bool) -> list[str]:
    tracking = set(trackers["tracking_categories"])
    return sorted(d for d, e in trackers["domains"].items()
                  if e["category"] in tracking and (e.get("verified") or not verified_only))


def tracking_params(ext: Path) -> list[str]:
    src = (ext / "src" / "core" / "headers.js").read_text(encoding="utf-8")
    block = re.search(r"TRACKING_PARAMS = new Set\(\[(.*?)\]\)", src, re.S)
    return sorted(re.findall(r"'([a-z0-9_]+)'", block.group(1)))


def site_domains(ext: Path) -> list[tuple[str, list[str]]]:
    """(site host without www., its other domains) for each measured site that uses other domains."""
    data = json.loads((ext.parent / "data" / "sites.json").read_text(encoding="utf-8"))
    out = {}
    for site in data["sites"]:
        host = site["url"].split("//", 1)[-1].split("/", 1)[0].lower()
        host = host[4:] if host.startswith("www.") else host
        others = sorted({d.lower() for d in site.get("first_party_domains", [])} - {host})
        if others:
            out[host] = sorted(set(out.get(host, [])) | set(others))
    return sorted(out.items())


def siteonly_rules(ext: Path) -> list[dict]:
    trackers = json.loads((ext / "data" / "trackers.json").read_text(encoding="utf-8"))
    # consent banners and subscription walls of the list too: without them many sites cannot be answered or read
    safe = SAFE_DOMAINS + sorted(d for d, e in trackers["domains"].items() if e["category"] in SAFE_CATEGORIES)
    rules: list[dict] = [{"priority": P_SITEONLY, "action": {"type": "block"},
                          "condition": {"domainType": "thirdParty", "resourceTypes": SITEONLY_TYPES}}]
    frame = {"resourceTypes": ["sub_frame"]}
    rules.append({"priority": P_ALLOW, "action": {"type": "allowAllRequests"},
                  "condition": {"requestDomains": safe, **frame}})
    rules.append({"priority": P_ALLOW, "action": {"type": "allow"}, "condition": {"requestDomains": safe}})
    for url in SAFE_URLS:
        rules.append({"priority": P_ALLOW, "action": {"type": "allowAllRequests"}, "condition": {"urlFilter": url, **frame}})
        rules.append({"priority": P_ALLOW, "action": {"type": "allow"}, "condition": {"urlFilter": url}})
    for host, others in site_domains(ext):
        cond = {"initiatorDomains": [host, *others], "requestDomains": others}
        rules.append({"priority": P_ALLOW, "action": {"type": "allowAllRequests"}, "condition": {**cond, **frame}})
        rules.append({"priority": P_ALLOW, "action": {"type": "allow"}, "condition": cond})
    return [{"id": i + 1, **r} for i, r in enumerate(rules)]


def rulesets(ext: Path) -> dict[str, list[dict]]:
    trackers = json.loads((ext / "data" / "trackers.json").read_text(encoding="utf-8"))
    block = lambda domains: [{"id": 1, "priority": P_LIST, "action": {"type": "block"},  # noqa: E731
                              "condition": {"requestDomains": domains, "domainType": "thirdParty",
                                            "resourceTypes": RESOURCE_TYPES}}]
    params = tracking_params(ext)
    return {
        "verified": block(tracking_domains(trackers, True)),
        "full": block(tracking_domains(trackers, False)),
        # plain url filters, two per parameter: Chromium silently skips a static regex rule whose compiled form
        # exceeds 2 KB (one regex with every name did); each rule removes all the names at once
        "params": [{"id": i + 1, "priority": 1,
                    "action": {"type": "redirect", "redirect": {"transform": {"queryTransform": {"removeParams": params}}}},
                    "condition": {"urlFilter": f"{sep}{name}=", "resourceTypes": ["main_frame"]}}
                   for i, (name, sep) in enumerate((n, s) for n in params for s in "?&")],
        "siteonly": siteonly_rules(ext),
    }
