"""First-party / third-party classification and tracker lookup."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import tldextract

DEFAULT_TRACKER_LIST = Path(__file__).resolve().parent.parent / "data" / "trackers.seed.json"

# Bundled public-suffix snapshot only: no network access, no on-disk cache.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def registrable_domain(host: str) -> str:
    """'pixelcounter.marca.com' -> 'marca.com'; 'news.bbc.co.uk' -> 'bbc.co.uk'; IPs unchanged."""
    host = host.strip().strip(".").lower()
    if not host:
        return ""
    ext = _EXTRACT(host)
    if not ext.suffix:
        return host
    registered = getattr(ext, "top_domain_under_public_suffix", None) or ext.registered_domain
    return registered or host


class TrackerList:
    def __init__(self, domains: dict[str, dict], tracking_categories: Iterable[str], source: str):
        self.domains = domains
        self.tracking_categories = frozenset(tracking_categories)
        self.source = source

    @classmethod
    def load(cls, path: Path | str | None = None) -> "TrackerList":
        path = Path(path) if path else DEFAULT_TRACKER_LIST
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["domains"], data["_meta"]["tracking_categories"], path.name)

    def lookup(self, host: str) -> dict | None:
        """Longest matching domain suffix wins; returns service, entity, category or None."""
        labels = host.strip(".").lower().split(".")
        for i in range(len(labels) - 1):
            suffix = ".".join(labels[i:])
            entry = self.domains.get(suffix)
            if entry:
                return {"service": suffix, "entity": entry["entity"], "category": entry["category"]}
        return None

    def is_tracking(self, category: str) -> bool:
        return category in self.tracking_categories


class Classifier:
    def __init__(self, first_party_domains: Iterable[str], tracker_list: TrackerList):
        self.first_party = {registrable_domain(d) for d in first_party_domains if d}
        self.tracker_list = tracker_list

    def party(self, host: str) -> str:
        return "first" if registrable_domain(host) in self.first_party else "third"

    def tracker(self, host: str) -> dict | None:
        return self.tracker_list.lookup(host)
