"""The outlet list (data/sites.json) keeps a valid, balanced shape."""
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from traceguard.classify import registrable_domain

SITES = json.loads((Path(__file__).resolve().parent.parent / "data" / "sites.json").read_text(encoding="utf-8"))["sites"]
DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def test_every_entry_has_the_required_fields_and_valid_values():
    for site in SITES:
        assert {"name", "url", "first_party_domains", "group", "kind"} <= set(site), site
        assert site["url"].startswith("https://") and urlsplit(site["url"]).hostname, site
        assert site["group"] in ("US", "UK", "DE", "ES") and site["kind"] in ("news", "sports"), site
        assert all(DOMAIN.match(d) for d in site["first_party_domains"]), site


def test_names_and_addresses_are_unique():
    assert len({s["name"] for s in SITES}) == len(SITES)
    assert len({urlsplit(s["url"]).hostname.removeprefix("www.") for s in SITES}) == len(SITES)


def test_each_country_has_twelve_sites_and_spain_includes_sports():
    counts = Counter(s["group"] for s in SITES)
    assert counts == {"US": 12, "UK": 12, "DE": 12, "ES": 12}
    spain = [s for s in SITES if s["group"] == "ES"]
    assert sum(1 for s in spain if s["kind"] == "sports") >= 5 and sum(1 for s in spain if s["kind"] == "news") >= 5
    assert {"elmundo.es", "elpais.com", "marca.com", "as.com"} <= {registrable_domain(urlsplit(s["url"]).hostname) for s in spain}


def test_site_names_map_to_distinct_file_names():
    from traceguard.cli import _slug
    assert len({_slug(s["url"]) for s in SITES}) == len(SITES)
