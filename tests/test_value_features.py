"""Weight of third-party content, reach by company, downloads and community origins."""
import copy
import csv
import json

import pytest

from traceguard.classify import TrackerList
from traceguard.community import add, check_report, check_tree, load_community, main as community_main, normalise
from traceguard.entities import horizontal_bars, inferred_entities, reach
from traceguard.findings import format_bytes
from traceguard.report import aggregate_runs, build_report
from traceguard.site import build_site

from test_report import DOUBLECLICK, GTM, MEASUREMENT, SITE, make_run, own_run, request
from test_site import blocked_report, ok_report, write

TRACKERS = TrackerList.load()


def sized_run(number, sizes):
    run = own_run(number)
    for item, size in zip(run["requests"], sizes):
        if size is not None:
            item["bytes"] = size
    return run


# --- weight ---------------------------------------------------------------------------------------------

def test_weight_is_summed_per_party_and_for_tracking_services():
    runs = [sized_run(n, [1000, 2000, 300, 400]) for n in (1, 2, 3)]
    m = aggregate_runs(runs, TRACKERS)["metrics"]
    assert m["total_bytes"] == 3700 and m["third_party_bytes"] == 700 and m["tracking_bytes"] == 300
    assert m["tracking_requests"] == 1


def test_reports_without_sizes_leave_the_byte_figures_empty_and_add_no_finding():
    report = build_report(SITE, MEASUREMENT, [own_run(1), own_run(2), own_run(3)], TRACKERS)
    m = report["summary"]["metrics"]
    assert m["total_bytes"] is None and m["third_party_bytes"] is None and m["tracking_bytes"] is None
    assert "THIRD_PARTY_WEIGHT" not in [f["code"] for f in report["findings"]]


def test_weight_finding_states_the_share_without_judging():
    report = build_report(SITE, MEASUREMENT, [sized_run(n, [600_000, 200_000, 100_000, 100_000]) for n in (1, 2, 3)], TRACKERS)
    text = next(f["text"] for f in report["findings"] if f["code"] == "THIRD_PARTY_WEIGHT")
    assert "1.0 MB" in text and "20%" in text and "100 kB" in text


def test_format_bytes():
    assert [format_bytes(x) for x in (None, 0, 820, 41_000, 1_300_000)] == ["–", "0 B", "820 B", "41 kB", "1.3 MB"]


# --- reach by company --------------------------------------------------------------------------------------

def service(name, entity, tracking=True, stable=True, category="advertising"):
    return {"service": name, "entity": entity, "category": category, "tracking": tracking, "passes_seen": 3, "stable": stable}


def report_with(name, url, services, status="ok", confidence="high"):
    r = ok_report(name, url)
    r["summary"]["services"] = services
    r["summary"]["status"] = status
    r["summary"]["confidence"] = confidence
    return r


def test_reach_counts_sites_per_operator_and_ignores_unstable_untracked_and_unmeasured():
    reports = {
        "a": report_with("A", "https://a.example/", [service("doubleclick.net", "Google"), service("gtm.example", "Google", tracking=False, category="tag_manager")]),
        "b": report_with("B", "https://b.example/", [service("google-analytics.com", "Google", category="analytics"), service("x.example", "Other", stable=False)]),
        "c": report_with("C", "https://c.example/", [service("doubleclick.net", "Google")], confidence="low"),
        "d": blocked_report("D", "https://d.example/"),
    }
    meta = {"https://a.example/": {"group": "US"}, "https://b.example/": {"group": "UK"}}
    result = reach(reports, meta, {"Other"})
    assert result["measured"] == 2 and result["groups"] == {"US": 1, "UK": 1}
    assert [r["entity"] for r in result["rows"]] == ["Google"]
    google = result["rows"][0]
    assert google["n"] == 2 and google["share"] == 100 and google["by_group"] == {"US": 1, "UK": 1}
    assert google["services"] == ["doubleclick.net", "google-analytics.com"]
    assert google["categories"] == ["advertising", "analytics"] and not google["inferred"]


def test_inferred_entities_need_every_entry_to_be_inferred():
    rows = [{"entity": "A", "evidence": "x"}, {"entity": "A", "evidence": "y"},
            {"entity": "B", "evidence": "x"}, {"entity": "B", "evidence": None}]
    assert inferred_entities(rows) == {"A"}


def test_bar_chart_escapes_names_and_handles_empty_input():
    assert horizontal_bars([], 5) == "" and horizontal_bars([{"entity": "x", "n": 1}], 0) == ""
    svg = horizontal_bars([{"entity": "<b>Evil</b>", "n": 2}], 4)
    assert "<b>" not in svg and "2 of 4" in svg


# --- community origins -----------------------------------------------------------------------------------------

SLUG = "brazil-home"


def contributed(vantage=SLUG, **changes):
    report = ok_report("News A", "https://a.example/", tracking=40, vantage=vantage)
    report["measurement"].update({"passes": 3, "observe_seconds": 12.0, "interaction": "none"})
    report["generated_at"] = "2026-10-04T12:00:00+00:00"
    report["tool"]["name"] = "traceguard"
    for run in report["runs"]:  # a real scanner report carries all of these
        run.update(status="ok", security_headers={"strict_transport_security": True, "content_security_policy": "absent"})
    report.update(changes)
    return report


def put(tmp_path, report, slug=SLUG, day="2026-10-04", stem="a", label="Brazil", contributor="somebody"):
    origin = tmp_path / "community" / slug
    (origin / day).mkdir(parents=True, exist_ok=True)
    (origin / "meta.json").write_text(json.dumps({"label": label, "contributor": contributor}), encoding="utf-8")
    (origin / day / f"{stem}.json").write_text(json.dumps(report), encoding="utf-8")


SITES = {"https://a.example/": {"name": "News A", "url": "https://a.example/", "group": "US", "first_party_domains": ["a.example"]}}


def test_a_well_formed_report_passes():
    assert check_report(contributed(), SLUG, SITES) == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda r: r["measurement"].update(vantage="other"), "folder name"),
    (lambda r: r["measurement"].update(interaction="click"), "passive"),
    (lambda r: r["measurement"].update(passes=1), "passes"),
    (lambda r: r["measurement"].update(observe_seconds=600), "observe_seconds"),
    (lambda r: r["site"].update(url="https://unknown.example/"), "not in the list"),
    (lambda r: r["runs"][0]["requests"][0].update(url="https://a.example/x?token=abc"), "query string"),
    (lambda r: r["runs"][0]["requests"][0].update(host="a.example/evil"), "invalid host"),
    (lambda r: r["runs"][0]["requests"][0].update(host="b.example"), "does not match"),
    (lambda r: r["runs"][0]["requests"][0].update(bytes=-5), "size"),
    (lambda r: r["runs"][0].update(cookies=[{"name": "a", "domain": "x.com", "value": "secret"}]), "cookie values"),
    (lambda r: r.update(generated_at="2031-01-01T00:00:00+00:00"), "generated_at"),
    (lambda r: r.update(generated_at="2026-10-04T12:00:00"), "generated_at"),
    (lambda r: r["tool"].update(name="other"), "tool name"),
])
def test_malformed_or_unsafe_reports_are_rejected(mutate, expected):
    report = contributed()
    mutate(report)
    problems = check_report(report, SLUG, SITES)
    assert problems and any(expected in p for p in problems), problems


def test_garbage_is_rejected_without_crashing():
    for junk in ([], "x", {"site": 1}, {"site": {"url": "u"}, "measurement": [], "runs": {}, "tool": {}, "generated_at": "x"}):
        assert check_report(junk, SLUG, SITES)


def test_forged_summary_is_replaced_by_our_own_classification():
    report = contributed()
    report["runs"][0]["requests"][2].update(host="cdn.unknown.example", url="https://cdn.unknown.example/x")
    forged = normalise(report, SITES["https://a.example/"], TRACKERS)
    assert forged["summary"]["metrics"]["tracking_services"] != 40
    assert forged["summary"]["metrics"]["tracking_services"] == 1  # only doubleclick.net, found in our list
    assert forged["reclassified_at"]


def test_party_is_recomputed_from_our_first_party_domains():
    report = contributed()
    report["site"]["url"] = "https://news.com/"
    report["runs"][0]["requests"][0].update(party="third", host="static.news.com", url="https://static.news.com/x")
    again = normalise(report, {"name": "News", "url": "https://news.com/", "first_party_domains": []}, TRACKERS)
    assert again["runs"][0]["requests"][0]["party"] == "first"


def test_load_community_labels_every_origin_as_unverified_and_skips_bad_files(tmp_path):
    put(tmp_path, contributed())
    bad = contributed()
    bad["runs"][0]["requests"][0]["url"] = "https://a.example/x?secret=1"
    put(tmp_path, bad, stem="b", day="2026-10-04")
    extra, labels, warnings = load_community(tmp_path / "community", TRACKERS, SITES)
    assert labels == {SLUG: "Brazil (community, unverified)"}
    assert list(extra[SLUG]["2026-10-04"]) == ["a"]
    assert any("b.json" in w for w in warnings)


def test_check_tree_reports_bad_names_meta_and_dates(tmp_path):
    put(tmp_path, contributed(), slug="XX", label="x")
    put(tmp_path, contributed(), label="<script>")
    put(tmp_path, contributed(vantage="ok-one"), slug="ok-one", day="not-a-date")
    found = check_tree(tmp_path / "community", SITES)
    assert "XX" in found and f"{SLUG}/meta.json" in found and "ok-one/not-a-date/a.json" in found


def test_add_copies_reports_and_refuses_a_mismatched_vantage(tmp_path):
    scan = tmp_path / "scan" / "2026-10-04"
    scan.mkdir(parents=True)
    (scan / "a.json").write_text(json.dumps(contributed()), encoding="utf-8")
    root = tmp_path / "data" / "community"
    assert add(scan, root, SLUG, "Brazil", "somebody", SITES) == 1
    assert check_tree(root, SITES) == {}
    (scan / "a.json").write_text(json.dumps(contributed(vantage="local")), encoding="utf-8")
    with pytest.raises(Exception, match="vantage"):
        add(scan, root, SLUG, "Brazil", "somebody", SITES)


def test_cli_check_and_add(tmp_path, capsys):
    scan = tmp_path / "scan" / "2026-10-04"
    scan.mkdir(parents=True)
    (scan / "a.json").write_text(json.dumps(contributed()), encoding="utf-8")
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": list(SITES.values())}), encoding="utf-8")
    root = tmp_path / "community"
    assert community_main(["add", str(scan), "--slug", SLUG, "--label", "Brazil", "--root", str(root), "--sites-file", str(sites)]) == 0
    assert community_main(["check", str(root), "--sites-file", str(sites)]) == 0
    (root / SLUG / "meta.json").write_text("{}", encoding="utf-8")
    assert community_main(["check", str(root), "--sites-file", str(sites)]) == 1


# --- the site and its downloads -----------------------------------------------------------------------------------

@pytest.fixture
def full_site(tmp_path):
    a = ok_report("News A", "https://a.example/", tracking=3, vantage="github-actions-us")
    a["summary"]["metrics"].update(total_bytes=2_000_000, third_party_bytes=1_500_000, tracking_bytes=300_000, tracking_requests=4)
    write(tmp_path, "2026-10-04", {"a": a, "b": blocked_report()})
    put(tmp_path, contributed(), label="Brazil")
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": [dict(SITES["https://a.example/"]),
                                           {"name": "News B", "url": "https://b.example/", "group": "UK", "kind": "sports"}]}))
    out = tmp_path / "site"
    ctx = build_site(tmp_path / "runs", out, sites_file=str(sites), base_url="https://example.org/tw/")
    return out, ctx


def test_companies_page_and_site_page_list_the_operators(full_site):
    out, ctx = full_site
    page = (out / "companies.html").read_text(encoding="utf-8")
    assert "Google" in page and "1 of 1" in page and "not</b> merged" in page
    assert "Google" in (out / "sites" / "a.html").read_text(encoding="utf-8")
    assert 'href="companies.html"' in (out / "index.html").read_text(encoding="utf-8")


def test_weight_is_shown_on_the_index_and_site_page(full_site):
    out, _ = full_site
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "Third-party data" in index and "1.5 MB" in index and "(75%)" in index
    assert "1.5 MB" in (out / "sites" / "a.html").read_text(encoding="utf-8")


def test_downloads_exist_and_have_the_documented_columns(full_site):
    out, ctx = full_site
    data = out / "data"
    latest = list(csv.DictReader((data / "latest.csv").open(encoding="utf-8")))
    assert latest[0]["third_party_bytes"] == "1500000" and latest[1]["third_party_bytes"] == ""
    history = list(csv.DictReader((data / "history.csv").open(encoding="utf-8")))
    kinds = {(r["origin"], r["origin_kind"]) for r in history}
    assert ("github-actions-us", "main") in kinds and (SLUG, "community-unverified") in kinds
    entities = list(csv.DictReader((data / "entities.csv").open(encoding="utf-8")))
    assert entities[0]["operator"] == "Google" and entities[0]["sites_measured"] == "1"
    index = json.loads((data / "index.json").read_text(encoding="utf-8"))
    assert index["latest_date"] == "2026-10-04" and {o["kind"] for o in index["origins"]} == {"main", "community-unverified"}
    site_a = next(s for s in index["sites"] if s["id"] == "a")
    assert site_a["latest"]["third_party_bytes"] == 1_500_000
    for report in site_a["reports"]:
        assert (out / report["path"]).exists(), report


def test_community_origin_appears_labelled_unverified_with_contribution_steps(full_site):
    out, ctx = full_site
    origins = (out / "origins.html").read_text(encoding="utf-8")
    assert "Brazil (community, unverified)" in origins and "Community origins are unverified" in origins
    assert "traceguard.community add" in origins
    assert ctx["origins"]["unverified"] == ["Brazil (community, unverified)"]
    assert (out / "data" / "origins" / SLUG / "2026-10-04" / "a.json").exists()


def test_community_label_cannot_inject_html(tmp_path):
    a = ok_report("News A", "https://a.example/", tracking=3, vantage="github-actions-us")
    write(tmp_path, "2026-10-04", {"a": a})
    put(tmp_path, contributed(), label="<img src=x onerror=alert(1)>")
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": [dict(SITES["https://a.example/"])]}))
    ctx = build_site(tmp_path / "runs", tmp_path / "site", sites_file=str(sites))
    assert ctx["origins"]["unverified"] == [] and any("skipped" in w for w in ctx["community_warnings"])
    assert "onerror" not in (tmp_path / "site" / "origins.html").read_text(encoding="utf-8")


def test_reports_missing_run_fields_are_rejected_not_crashing_the_build(tmp_path):
    report = contributed()
    del report["runs"][0]["security_headers"]
    assert any("missing" in p for p in check_report(report, SLUG, SITES))
    report = contributed()
    report["runs"][0]["cookies"] = [{"name": "x"}]
    assert any("cookie" in p for p in check_report(report, SLUG, SITES))


def test_home_page_summarises_what_the_tool_offers_and_why_clicks_matter(full_site):
    out, _ = full_site
    home = (out / "index.html").read_text(encoding="utf-8")
    assert "What you get here" in home and "Why also test what happens after a click?" in home
    for page in ("companies.html", "origins.html", "consent.html", "protection.html"):
        assert f'href="{page}"' in home
