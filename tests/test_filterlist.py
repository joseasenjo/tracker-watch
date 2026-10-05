"""The downloadable filter lists."""
import json
from pathlib import Path

import pytest

from traceguard.blocker import FilterList
from traceguard.classify import DEFAULT_TRACKER_LIST
from traceguard.filterlist import build, main, tracking_entries, write
from traceguard.site import build_site

from test_site import ok_report, write as write_runs

DATA = {
    "_meta": {"tracking_categories": ["advertising", "analytics"]},
    "domains": {
        "ads.example": {"entity": "Ads", "category": "advertising"},
        "stats.example": {"entity": "Stats", "category": "analytics"},
        "guess.example": {"entity": "Guess", "category": "advertising", "evidence": "inferred"},
        "tags.example": {"entity": "Tags", "category": "tag_manager"},
        "consent.example": {"entity": "Consent", "category": "consent_management"},
        "wall.example": {"entity": "Wall", "category": "paywall"},
    },
}


def lines(text):
    return [l for l in text.splitlines() if l.startswith("||")]


def test_only_tracking_categories_are_exported_and_never_consent_tags_or_paywalls():
    files = build(DATA, "2026-10-05")
    full = lines(files["trackerwatch-full.txt"]["text"])
    assert sorted(full) == ["||ads.example^$third-party", "||guess.example^$third-party", "||stats.example^$third-party"]
    for banned in ("tags.example", "consent.example", "wall.example"):
        assert banned not in files["trackerwatch-full.txt"]["text"] + files["trackerwatch-verified.txt"]["text"]


def test_the_verified_file_has_no_unverified_entry_and_the_full_one_lists_them_apart():
    files = build(DATA, "2026-10-05")
    verified, full = files["trackerwatch-verified.txt"], files["trackerwatch-full.txt"]
    assert "guess.example" not in verified["text"] and verified["count"] == 2 and verified["unverified"] == 0
    assert full["count"] == 3 and full["unverified"] == 1
    marker = "! Identified from general knowledge of the vendor, not verified one by one:"
    assert full["text"].index(marker) < full["text"].index("||guess.example^")
    assert full["text"].index("||ads.example^") < full["text"].index(marker)


def test_header_has_the_syntax_marker_version_expiry_licence_and_the_honest_notice():
    text = build(DATA, "2026-10-05", "https://o.github.io/r/")["trackerwatch-full.txt"]["text"]
    head = text.split("||")[0]
    assert text.startswith("[Adblock Plus 2.0]\n")
    for expected in ("! Version: 2026.10.05", "! Expires: 7 days", "CC BY 4.0", "! Entries: 3",
                     "! Homepage: https://o.github.io/r/filters.html", "does nothing by itself",
                     "not complete protection", "never are"):
        assert expected in head
    assert "Homepage" not in build(DATA, "2026-10-05")["trackerwatch-full.txt"]["text"]


def test_rules_block_other_sites_requests_but_not_visiting_the_company_itself():
    blocker = FilterList.parse(build(DATA, "2026-10-05")["trackerwatch-full.txt"]["text"])
    assert blocker.blocks("ads.example", "script", True)
    assert blocker.blocks("sub.ads.example", "image", True)       # subdomains are covered
    assert not blocker.blocks("ads.example", "document", False)    # the company's own site and tools
    assert not blocker.blocks("tags.example", "script", True)
    assert not blocker.blocks("notads.example", "script", True)


def test_an_invalid_domain_in_the_list_stops_the_export():
    bad = {"_meta": DATA["_meta"], "domains": {"ads.example/evil": {"entity": "X", "category": "advertising"}}}
    with pytest.raises(ValueError):
        tracking_entries(bad)
    injected = {"_meta": DATA["_meta"], "domains": {"a.example\n||b.example": {"entity": "X", "category": "advertising"}}}
    with pytest.raises(ValueError):
        build(injected)


def test_the_shipped_list_exports_cleanly_and_matches_the_list(tmp_path):
    data = json.loads(Path(DEFAULT_TRACKER_LIST).read_text(encoding="utf-8"))
    files = write(tmp_path, "2026-10-05")
    tracking = set(data["_meta"]["tracking_categories"])
    expected = {d for d, e in data["domains"].items() if e["category"] in tracking}
    assert files["trackerwatch-full.txt"]["count"] == len(expected)
    exported = {l[2:].split("^")[0] for l in lines((tmp_path / "trackerwatch-full.txt").read_text(encoding="utf-8"))}
    assert exported == expected
    verified = {l[2:].split("^")[0] for l in lines((tmp_path / "trackerwatch-verified.txt").read_text(encoding="utf-8"))}
    assert verified == {d for d in expected if not data["domains"][d].get("evidence")} and len(verified) < len(expected)
    assert not any(c in data["domains"] for c in ("googletagmanager.com",) if data["domains"][c]["category"] in tracking)


def test_command_line(tmp_path, capsys):
    assert main(["--out", str(tmp_path / "f"), "--homepage", "https://o.github.io/r/"]) == 0
    assert "trackerwatch-verified.txt" in capsys.readouterr().out and (tmp_path / "f" / "trackerwatch-full.txt").exists()


def test_site_publishes_the_files_the_page_and_the_card(tmp_path):
    write_runs(tmp_path, "2026-10-04", {"a": ok_report("News A", "https://a.example/", vantage="github-actions-us")})
    ctx = build_site(tmp_path / "runs", tmp_path / "site", base_url="https://o.github.io/r/", repo_url="https://github.com/o/r")
    out = tmp_path / "site"
    for name in ("trackerwatch-verified.txt", "trackerwatch-full.txt"):
        assert (out / "data" / name).read_text(encoding="utf-8").startswith("[Adblock Plus 2.0]")
    page = (out / "filters.html").read_text(encoding="utf-8")
    assert "https://o.github.io/r/data/trackerwatch-verified.txt" in page and "uBlock Origin" in page
    assert "not complete protection" in page and "$third-party" in page
    for benefit in ("Fewer ads and fewer trackers contacted", "often load faster and use less data", "Control per site"):
        assert benefit in page
    assert "pause the blocker on that one site" in page
    home = (out / "index.html").read_text(encoding="utf-8")
    assert "<h3>Filter lists</h3>" in home and 'href="filters.html"' in home
    index = json.loads((out / "data" / "index.json").read_text(encoding="utf-8"))
    assert "data/trackerwatch-full.txt" in index["files"].values()
    assert {f["file"] for f in ctx["filters"]} == {"trackerwatch-verified.txt", "trackerwatch-full.txt"}
    assert "trackerwatch-full.txt" in (out / "method.html").read_text(encoding="utf-8")
