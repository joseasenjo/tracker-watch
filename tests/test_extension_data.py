"""The data snapshot shipped inside the browser extension (extension/tools/build_data.py)."""
import importlib.util
import json
from pathlib import Path

import pytest

from traceguard.classify import TrackerList, registrable_domain

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("build_data", ROOT / "extension" / "tools" / "build_data.py")
build_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_data)


@pytest.fixture(scope="module")
def files():
    return build_data.build_all()


def _json(files, name):
    return json.loads(next(t for p, t in files.items() if p.name == name))


def test_build_is_deterministic(files):
    assert build_data.build_all() == files


def test_files_on_disk_are_up_to_date():
    # Fails when the list, the glossary or the weekly data changed without regenerating the snapshot.
    assert build_data.main(["--check"]) == 0


def test_trackers_mirror_the_seed_list(files):
    trackers = _json(files, "trackers.json")
    seed = TrackerList.load()
    assert set(trackers["domains"]) == set(seed.domains)
    assert trackers["unverified"] == sum(1 for e in seed.domains.values() if e.get("evidence"))
    assert "evidence" not in json.dumps(trackers)  # only the flag travels, not the internal note


def test_sites_cover_the_latest_run(files):
    sites = _json(files, "sites.json")
    latest = sorted(p for p in (ROOT / "data" / "runs").iterdir() if p.is_dir())[-1]
    assert sites["date"] == latest.name
    assert set(sites["sites"]) == {p.stem for p in latest.glob("*.json")}
    for site in sites["sites"].values():
        assert site["host"] == registrable_domain(site["host"])
        assert site["band"] is None or site["band"] in "ABCDE"
        if site["status"] == "ok":
            # headline = median count per pass; list = services seen in most passes: equal or off by one or two
            assert abs(len(site["services"]) - site["tracking_services"]) <= 2, site["name"]


def test_psl_has_wildcards_exceptions_and_punycode(files):
    rules = set(_json(files, "psl.json")["rules"])
    assert {"co.uk", "*.ck", "!www.ck", "xn--fiqs8s"} <= rules


def test_index_hashes_match(files):
    import hashlib
    index = _json(files, "index.json")
    for path, text in files.items():
        if path.name in index["files"]:
            assert index["files"][path.name] == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_parity_cases_are_engine_answers(files):
    cases = _json(files, "parity.json")["cases"]
    by_host = {c["host"]: c for c in cases}
    assert by_host["news.bbc.co.uk"]["registrable"] == "bbc.co.uk"
    assert by_host["stats.g.doubleclick.net"]["lookup"]["service"] == "doubleclick.net"
