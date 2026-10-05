"""Domain-level subset of an Adblock-style filter list, used to measure a page "with protection".

Only network rules of the form `||host^` (optionally with `$third-party` and resource-type options), and
their `@@` exceptions, are applied. Path rules, wildcards, cosmetic rules and rules limited to some sites
(`$domain=`) are skipped, so the result describes a conservative subset of what a real blocker using the same
list would do. The list itself is never stored in this repository: it is downloaded when the scan runs, and
each report records its name, address, SHA-256 and how many rules were applied.

Usage (to inspect a list): python -m traceguard.blocker easyprivacy.txt
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

# Playwright resource types -> Adblock option names
TYPE_OPTIONS = {
    "script": "script", "image": "image", "stylesheet": "stylesheet", "font": "font", "media": "media",
    "xhr": "xmlhttprequest", "fetch": "xmlhttprequest", "websocket": "websocket", "ping": "ping",
    "beacon": "ping", "document": "subdocument", "other": "other", "texttrack": "other", "eventsource": "other",
    "manifest": "other",
}
KNOWN_TYPES = set(TYPE_OPTIONS.values()) | {"object", "subdocument"}
IGNORED_OPTIONS = {"important", "match-case", "all"}
RULE = re.compile(r"^(@@)?\|\|([a-z0-9.-]+)\^(?:\$(.*))?$")


class Rule:
    __slots__ = ("third_party", "types", "not_types")

    def __init__(self, third_party: bool | None, types: frozenset, not_types: frozenset):
        self.third_party = third_party  # None = any party
        self.types = types
        self.not_types = not_types

    def applies(self, resource_type: str, third_party: bool) -> bool:
        if self.third_party is not None and self.third_party != third_party:
            return False
        kind = TYPE_OPTIONS.get(resource_type, "other")
        if self.types and kind not in self.types:
            return False
        return kind not in self.not_types


def _parse_options(text: str | None) -> Rule | None:
    """None when the rule uses an option we do not support (the rule is then skipped, not approximated)."""
    third_party, types, not_types = None, set(), set()
    for option in (text or "").split(","):
        option = option.strip().lower()
        if not option or option in IGNORED_OPTIONS:
            continue
        negated = option.startswith("~")
        name = option[1:] if negated else option
        if name in ("third-party", "3p"):
            third_party = not negated
        elif name in ("first-party", "1p"):
            third_party = negated
        elif name in KNOWN_TYPES:
            (not_types if negated else types).add(name)
        else:
            return None  # domain=, redirect, csp, removeparam, popup, document...
    return Rule(third_party, frozenset(types), frozenset(not_types))


class FilterList:
    def __init__(self, name: str, url: str, sha256: str, block: dict, allow: dict, total: int, skipped: int):
        self.name, self.url, self.sha256 = name, url, sha256
        self.block, self.allow = block, allow
        self.total, self.skipped = total, skipped

    @classmethod
    def parse(cls, text: str, name: str = "filter list", url: str = "") -> "FilterList":
        block: dict[str, list[Rule]] = {}
        allow: dict[str, list[Rule]] = {}
        total = skipped = 0
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(("!", "[")) or "##" in line or "#@#" in line or "#?#" in line:
                continue
            total += 1
            match = RULE.match(line.lower())
            rule = _parse_options(match.group(3)) if match else None
            if rule is None:
                skipped += 1
                continue
            (allow if match.group(1) else block).setdefault(match.group(2).strip("."), []).append(rule)
        return cls(name, url, hashlib.sha256(text.encode("utf-8")).hexdigest(), block, allow, total, skipped)

    @classmethod
    def load(cls, path: Path | str, name: str | None = None, url: str = "") -> "FilterList":
        path = Path(path)
        return cls.parse(path.read_text(encoding="utf-8", errors="replace"), name or path.stem, url)

    @property
    def rules_applied(self) -> int:
        return sum(len(v) for v in self.block.values()) + sum(len(v) for v in self.allow.values())

    def describe(self) -> dict:
        return {"name": self.name, "url": self.url, "sha256": self.sha256, "rules_total": self.total,
                "rules_applied": self.rules_applied, "rules_skipped": self.skipped,
                "subset": "domain rules (||host^) with party and resource-type options only"}

    @staticmethod
    def _matches(table: dict, host: str, resource_type: str, third_party: bool) -> bool:
        labels = host.strip(".").lower().split(".")
        for i in range(len(labels) - 1):
            for rule in table.get(".".join(labels[i:]), ()):
                if rule.applies(resource_type, third_party):
                    return True
        return False

    def blocks(self, host: str, resource_type: str, third_party: bool) -> bool:
        return (self._matches(self.block, host, resource_type, third_party)
                and not self._matches(self.allow, host, resource_type, third_party))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    info = FilterList.load(argv[0]).describe()
    for key, value in info.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
