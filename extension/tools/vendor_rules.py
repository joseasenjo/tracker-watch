"""Rules built from the third-party lists in extension/vendor/ (fetched by tools/fetch_lists.py).

- easyprivacy.json (declarativeNetRequest): the domain rules of EasyPrivacy (`||host^` with party and type options,
  and their `@@` exceptions), read with the engine's own parser (traceguard/blocker.py). Path rules and rules limited
  to some sites are skipped, as in the website's "blocking list" test: a conservative subset of EasyPrivacy.
  Licence of the converted rules: CC BY-SA 3.0, credit "The EasyList authors" (see THIRD_PARTY.md).
- cname_trackers.json (data): the CNAME targets of disguised trackers (AdGuard cname-trackers, MIT) with the
  company each belongs to, used by Firefox, which lets extensions resolve host names.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "extension" / "vendor"
# Adblock type option -> declarativeNetRequest resource type
TYPES = {"script": "script", "image": "image", "stylesheet": "stylesheet", "font": "font", "media": "media",
         "xmlhttprequest": "xmlhttprequest", "websocket": "websocket", "ping": "ping", "subdocument": "sub_frame",
         "object": "object", "other": "other"}


def _rule_signature(rule) -> tuple:
    return (rule.third_party, tuple(sorted(TYPES[t] for t in rule.types)), tuple(sorted(TYPES[t] for t in rule.not_types)))


def _condition(domains: list[str], signature: tuple) -> dict:
    third_party, types, not_types = signature
    cond: dict = {"requestDomains": domains}
    if third_party is not None:
        cond["domainType"] = "thirdParty" if third_party else "firstParty"
    if types:
        cond["resourceTypes"] = list(types)
    else:
        cond["excludedResourceTypes"] = sorted({"main_frame", *not_types})  # never the page itself
    return cond


def easyprivacy_rules() -> tuple[list[dict], dict]:
    """(rules, stats). Empty when the list has not been fetched."""
    path = VENDOR / "easyprivacy.txt"
    if not path.exists():
        return [], {"domains": 0}
    sys.path.insert(0, str(ROOT))
    from traceguard.blocker import FilterList  # noqa: E402 (the engine's parser, so both read the list alike)

    fl = FilterList.parse(path.read_text(encoding="utf-8"), "EasyPrivacy")
    groups: dict[tuple, dict[str, list[str]]] = {}
    for action, table in (("block", fl.block), ("allow", fl.allow)):
        for domain, rules in table.items():
            for rule in rules:
                groups.setdefault((action, _rule_signature(rule)), {}).setdefault(domain, None)
    out = []
    for i, ((action, signature), domains) in enumerate(sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))):
        out.append({"id": i + 1, "priority": 2 if action == "allow" else 1, "action": {"type": action},
                    "condition": _condition(sorted(domains), signature)})
    return out, {"domains": len(fl.block), "exceptions": len(fl.allow), "skipped": fl.skipped, "rules": len(out)}


def cname_trackers() -> dict:
    """{target domain: company} from the AdGuard list (`! Company: X` comments, then `||target^` lines)."""
    path = VENDOR / "cname_original_trackers.txt"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    company = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("! Company:"):
            company = line.split(":", 1)[1].strip()
        m = re.match(r"^\|\|([a-z0-9.-]+)\^$", line.strip())
        if m and company:
            out[m.group(1)] = company
    return dict(sorted(out.items()))
