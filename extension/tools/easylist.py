"""Ads (blocker step 3): EasyList converted for the browser, by our own converter (no third-party code).

- rules/easylist.json (declarativeNetRequest): the network rules of EasyList. `||host^` rules without site
  limits are grouped by their options (few rules); every other rule becomes one urlFilter rule, with
  `$third-party`, resource types, `$domain=` (initiatorDomains) and `$match-case`. Skipped, never approximated:
  regular expressions, non-ASCII patterns, and options a browser rule cannot express ($popup, $document, $csp,
  $rewrite, $redirect...).
- data/cosmetic.json: the element hiding rules (`##selector`), generic and per site, their `#@#` exceptions, and
  the sites where `$generichide` / `$elemhide` turn them off. Extended syntax (`#?#`, `#$#`, `:-abp-...`,
  `:has-text`...) is skipped. The background turns them into one style sheet per site (src/core/cosmetic.js).
Licence of the converted rules: CC BY-SA 3.0, credit "The EasyList authors" (see THIRD_PARTY.md).
Priorities as in rules.py: blocks 3 (with the tracker lists), exceptions 4 (above them, below a pause).
"""
from __future__ import annotations

import re
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "vendor"
P_BLOCK, P_EXCEPTION = 3, 4
TYPES = {"script": "script", "image": "image", "stylesheet": "stylesheet", "font": "font", "media": "media",
         "xmlhttprequest": "xmlhttprequest", "websocket": "websocket", "ping": "ping", "subdocument": "sub_frame",
         "object": "object", "other": "other"}
IGNORED = {"important", "all"}
DOMAIN = re.compile(r"^[a-z0-9.-]+\.[a-z0-9-]+$")
HOST_RULE = re.compile(r"^\|\|([a-z0-9.-]+)\^$")
# selectors a style sheet cannot use (extended syntax of blockers)
EXTENDED = re.compile(r":-abp-|:has-text|:contains|:xpath|:matches-|:upward|:remove|:style\(|:min-text|:watch-attr|"
                      r":others|:if|:nth-ancestor|:matches-path")


def _domains(value: str) -> tuple[list[str], list[str]] | None:
    """$domain=a.com|~b.com -> (included, excluded); None when a name cannot be a browser domain (google.*)."""
    inc, exc = [], []
    for d in value.split("|"):
        d = d.strip().lower()
        neg = d.startswith("~")
        d = d[1:] if neg else d
        if not DOMAIN.match(d):
            return None
        (exc if neg else inc).append(d)
    return inc, exc


def network_rule(line: str) -> dict | None:
    """One EasyList network line -> a declarativeNetRequest rule without id/priority, or None (skipped)."""
    allow = line.startswith("@@")
    body = line[2:] if allow else line
    pattern, _, opts = body.rpartition("$") if "$" in body else (body, "", "")
    if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 1:
        return None  # regular expression
    if not pattern or pattern in ("*", "|", "||") or not pattern.isascii() or pattern.startswith("||*"):
        return None
    cond: dict = {}
    types, not_types = set(), set()
    case = False
    for option in filter(None, (o.strip() for o in opts.split(","))):
        neg = option.startswith("~")
        name, _, value = (option[1:] if neg else option).partition("=")
        name = name.lower()
        if name in IGNORED:
            continue
        if name in ("third-party", "3p"):
            cond["domainType"] = "firstParty" if neg else "thirdParty"
        elif name in ("first-party", "1p"):
            cond["domainType"] = "thirdParty" if neg else "firstParty"
        elif name in TYPES:
            (not_types if neg else types).add(TYPES[name])
        elif name == "domain" and not neg:
            parsed = _domains(value)
            if parsed is None:
                return None
            if parsed[0]:
                cond["initiatorDomains"] = sorted(parsed[0])
            if parsed[1]:
                cond["excludedInitiatorDomains"] = sorted(parsed[1])
        elif name == "match-case":
            case = True
        else:
            return None  # popup, document, generichide, csp, rewrite, redirect...
    if types:
        cond["resourceTypes"] = sorted(types - not_types)
        if not cond["resourceTypes"]:
            return None
    elif not_types:
        cond["excludedResourceTypes"] = sorted(not_types | {"main_frame"})
    cond["urlFilter"] = pattern if case else pattern.lower()
    if case:
        cond["isUrlFilterCaseSensitive"] = True
    return {"action": {"type": "allow" if allow else "block"}, "condition": cond}


def network_rules(text: str) -> tuple[list[dict], dict]:
    """(rules with ids and priorities, stats). Plain host rules are grouped: one rule per set of options."""
    grouped: dict[tuple, list[str]] = {}
    single: list[dict] = []
    total = skipped = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("!", "[")) or "#" in line and re.search(r"#[@?$%]?#", line):
            continue
        total += 1
        rule = network_rule(line)
        if rule is None:
            skipped += 1
            continue
        cond = rule["condition"]
        m = HOST_RULE.match(cond["urlFilter"])
        if m and not cond.get("isUrlFilterCaseSensitive") and not cond.get("initiatorDomains") \
                and not cond.get("excludedInitiatorDomains"):
            key = (rule["action"]["type"], cond.get("domainType"), tuple(cond.get("resourceTypes", ())),
                   tuple(cond.get("excludedResourceTypes", ())))
            grouped.setdefault(key, []).append(m.group(1).strip("."))
        else:
            single.append(rule)
    out = []
    for (action, party, types, not_types), domains in sorted(grouped.items(), key=lambda kv: str(kv[0])):
        cond: dict = {"requestDomains": sorted(set(domains))}
        if party:
            cond["domainType"] = party
        if types:
            cond["resourceTypes"] = list(types)
        if not_types:
            cond["excludedResourceTypes"] = list(not_types)
        out.append({"action": {"type": action}, "condition": cond})
    out += single
    rules = [{"id": i + 1, "priority": P_EXCEPTION if r["action"]["type"] == "allow" else P_BLOCK, **r}
             for i, r in enumerate(out)]
    domains = sum(len(r["condition"].get("requestDomains", [])) for r in rules)
    return rules, {"lines": total, "skipped": skipped, "rules": len(rules), "host_domains": domains}


def cosmetic(text: str) -> dict:
    """Element hiding: {generic: [sel], sites: {domain: [sel]}, exceptions: {domain: [sel]},
    generichide: [domain], elemhide: [domain]} (a domain covers its subdomains)."""
    generic: set[str] = set()
    sites: dict[str, set[str]] = {}
    exceptions: dict[str, set[str]] = {}
    generichide: set[str] = set()
    elemhide: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("!", "[")):
            continue
        m = re.match(r"^@@\|\|([a-z0-9.-]+)\^\$(.+)$", line)
        if m:
            opts = {o.strip() for o in m.group(2).split(",")}
            if "generichide" in opts or "ghide" in opts:
                generichide.add(m.group(1))
            if "elemhide" in opts or "ehide" in opts:
                elemhide.add(m.group(1))
            continue
        exc = "#@#" in line
        sep = "#@#" if exc else "##"
        if sep not in line or re.search(r"#[?$%]#", line):
            continue
        where, _, sel = line.partition(sep)
        sel = sel.strip()
        if not sel or EXTENDED.search(sel) or "{" in sel or "}" in sel:
            continue
        names = [d.strip().lower() for d in where.split(",") if d.strip()]
        inc = [d for d in names if not d.startswith("~")]
        neg = [d[1:] for d in names if d.startswith("~")]
        if any(not DOMAIN.match(d) for d in inc + neg):
            continue  # entity names (google.*) and odd entries
        if exc:
            for d in inc or []:
                exceptions.setdefault(d, set()).add(sel)
            continue
        for d in inc:
            sites.setdefault(d, set()).add(sel)
        if not inc:
            generic.add(sel)
        for d in neg:  # a.com,~shop.a.com##x or ~b.com##x: not on those
            exceptions.setdefault(d, set()).add(sel)
    return {"generic": sorted(generic), "sites": {d: sorted(s) for d, s in sorted(sites.items())},
            "exceptions": {d: sorted(s) for d, s in sorted(exceptions.items())},
            "generichide": sorted(generichide), "elemhide": sorted(elemhide)}


def easylist() -> tuple[list[dict], dict, dict]:
    """(network rules, cosmetic data, stats). Empty when the list has not been fetched."""
    path = VENDOR / "easylist.txt"
    if not path.exists():
        return [], {"generic": [], "sites": {}, "exceptions": {}, "generichide": [], "elemhide": []}, {}
    text = path.read_text(encoding="utf-8")
    rules, stats = network_rules(text)
    cos = cosmetic(text)
    stats.update(generic=len(cos["generic"]), sites=len(cos["sites"]))
    return rules, cos, stats


if __name__ == "__main__":
    print(easylist()[2])
