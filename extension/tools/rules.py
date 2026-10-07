"""Clean mode (F14) rule sets for declarativeNetRequest, generated from the same data as the analysis.

- verified.json / full.json: one rule each that stops third-party requests to the tracking services of the list
  (verified entries only, or all), every resource type except the page itself. Same selection as the filter
  lists trackerwatch-verified.txt / -full.txt: tag managers, consent tools and paywalls are never included.
- params.json: removes known tracking parameters (src/core/headers.js TRACKING_PARAMS) from the address of the
  pages you open.
All are shipped disabled; the user turns them on from the settings page.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RESOURCE_TYPES = ["sub_frame", "stylesheet", "script", "image", "font", "object", "xmlhttprequest", "ping",
                  "csp_report", "media", "websocket", "other"]


def tracking_domains(trackers: dict, verified_only: bool) -> list[str]:
    tracking = set(trackers["tracking_categories"])
    return sorted(d for d, e in trackers["domains"].items()
                  if e["category"] in tracking and (e.get("verified") or not verified_only))


def tracking_params(ext: Path) -> list[str]:
    src = (ext / "src" / "core" / "headers.js").read_text(encoding="utf-8")
    block = re.search(r"TRACKING_PARAMS = new Set\(\[(.*?)\]\)", src, re.S)
    return sorted(re.findall(r"'([a-z0-9_]+)'", block.group(1)))


def rulesets(ext: Path) -> dict[str, list[dict]]:
    trackers = json.loads((ext / "data" / "trackers.json").read_text(encoding="utf-8"))
    block = lambda domains: [{"id": 1, "priority": 1, "action": {"type": "block"},  # noqa: E731
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
    }
