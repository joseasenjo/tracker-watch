"""The two separate tests: the consent banner test and the blocking-list test."""
import json

import pytest

from traceguard.blocker import FilterList
from traceguard.classify import TrackerList
from traceguard.cli import main as cli_main
from traceguard.diff import compare_reports
from traceguard.extended import consent_view, protection_view
from traceguard.report import aggregate_consent, aggregate_runs, build_report
from traceguard.site import build_site

from test_report import DOUBLECLICK, GTM, SITE, make_run, own_run, request
from test_site import ok_report, write

TRACKERS = TrackerList.load()
CRITEO = {"service": "criteo.com", "entity": "Criteo", "category": "advertising"}


# --- filter list -------------------------------------------------------------------------------------------

LIST = """! comment
[Adblock Plus 2.0]
||tracker.example^
||ads.example.com^$third-party
||pixel.example^$image
||script.example^$~script
||only1p.example^$first-party
||cdn.tracker.example^$domain=news.example
||tracker.example/path.js
example.com##.banner
@@||ok.tracker.example^
"""


def test_only_domain_rules_with_supported_options_are_applied():
    fl = FilterList.parse(LIST, "Test", "https://lists.example/test.txt")
    info = fl.describe()
    assert info["rules_total"] == 8 and info["rules_applied"] == 6 and info["rules_skipped"] == 2
    assert info["name"] == "Test" and len(info["sha256"]) == 64


@pytest.mark.parametrize("host, kind, third, expected", [
    ("tracker.example", "script", True, True),
    ("a.b.tracker.example", "xhr", False, True),          # subdomains match
    ("ok.tracker.example", "script", True, False),        # exception
    ("eviltracker.example", "script", True, False),       # not a label boundary
    ("ads.example.com", "image", True, True),
    ("ads.example.com", "image", False, False),           # $third-party
    ("example.com", "image", True, False),                # parent domain is not blocked
    ("pixel.example", "image", True, True),
    ("pixel.example", "script", True, False),             # $image only
    ("script.example", "script", True, False),            # $~script
    ("script.example", "fetch", True, True),
    ("only1p.example", "script", True, False),            # $first-party
    ("only1p.example", "script", False, True),
])
def test_matching(host, kind, third, expected):
    assert FilterList.parse(LIST).blocks(host, kind, third) is expected


# --- report aggregation --------------------------------------------------------------------------------

def consent_run(number, mode, outcome="clicked", after=(), navigated_away=False, cookies_after=0):
    run = own_run(number)
    for r in run["requests"]:
        r["phase"] = "before"
    for host, domain, tracker in after:
        item = request(host, domain, "third", tracker)
        item["phase"] = "after"
        run["requests"].append(item)
    run["mode"] = mode
    run["consent"] = {"action": mode, "outcome": outcome, "cmp": "OneTrust", "button_text": "Reject all",
                      "method": "selector", "navigated_away": navigated_away, "after_seconds": 12.0}
    run["cookies_after"] = [{"name": f"c{i}", "domain": "criteo.com", "party": "third"} for i in range(cookies_after)]
    return run


AFTER_ACCEPT = [("bidder.criteo.com", "criteo.com", CRITEO), ("stats.g.doubleclick.net", "doubleclick.net", DOUBLECLICK)]


def test_passive_figures_ignore_the_after_window():
    runs = [consent_run(n, "accept", after=AFTER_ACCEPT) for n in (1, 2)]
    m = aggregate_runs(runs, TRACKERS)["metrics"]
    assert m["tracking_services"] == 1 and m["third_party_requests"] == 2


def test_consent_summary_counts_after_and_new_services():
    runs = ([consent_run(n, "reject") for n in (1, 2)]
            + [consent_run(n, "accept", after=AFTER_ACCEPT, cookies_after=3) for n in (3, 4)])
    c = aggregate_consent(runs, TRACKERS)
    assert c["reject"]["outcome"] == "clicked" and c["reject"]["metrics"]["tracking_services_after"] == 0
    a = c["accept"]["metrics"]
    assert a["tracking_services_before"] == 1 and a["tracking_services_after"] == 2 and a["tracking_services_new_after"] == 1
    assert a["third_party_cookies_before"] == 1 and a["third_party_cookies_after"] == 3
    assert [s["service"] for s in c["accept"]["services_new_after"]] == ["criteo.com"]


def test_majority_outcome_and_clicks_that_left_the_site_are_not_counted():
    runs = [consent_run(1, "reject", outcome="no_button"), consent_run(2, "reject", outcome="no_button"),
            consent_run(3, "reject", navigated_away=True)]
    runs[0]["consent"]["paid_option"] = "Reject and subscribe"
    c = aggregate_consent(runs, TRACKERS)["reject"]
    assert c["outcome"] == "no_button" and c["navigated_away"] == 1 and "metrics" not in c
    assert c["paid_option"] == "Reject and subscribe"


def test_consent_findings_are_factual():
    runs = [consent_run(n, "reject", after=AFTER_ACCEPT[:1]) for n in (1, 2, 3)]
    report = build_report(SITE, {"vantage": "v", "observe_seconds": 12.0, "passes": 3,
                                 "interaction": "consent:reject"}, runs, TRACKERS)
    text = next(f["text"] for f in report["findings"] if f["code"] == "CONSENT_REJECT_CLICKED")
    assert "1 tracking services were contacted in the following 12 seconds" in text and "criteo.com" in text
    assert "unlawful" not in text and "illegal" not in text


def test_blocked_requests_do_not_count_as_contacted_and_are_summarised():
    runs = []
    for n in (1, 2, 3):
        run = own_run(n)
        blocked = request("bidder.criteo.com", "criteo.com", "third", CRITEO)
        blocked["blocked_by_list"] = True
        run["requests"].append(blocked)
        runs.append(run)
    measurement = {"vantage": "v", "observe_seconds": 12.0, "passes": 3,
                   "blocklist": {"name": "Test", "rules_applied": 5, "sha256": "x"}}
    report = build_report(SITE, measurement, runs, TRACKERS)
    assert report["summary"]["metrics"]["tracking_services"] == 1
    assert report["summary"]["protection"] == {"blocked_requests": 1, "blocked_domains": ["criteo.com"]}
    assert any(f["code"] == "PROTECTION_LIST_APPLIED" for f in report["findings"])


def test_consent_and_protection_reports_are_never_compared_with_passive_ones():
    passive = ok_report(vantage="v")
    passive["measurement"].update(interaction="none")
    consent = json.loads(json.dumps(passive))
    consent["measurement"]["interaction"] = "consent:reject"
    protected = json.loads(json.dumps(passive))
    protected["measurement"]["blocklist"] = {"name": "Test"}
    assert not compare_reports(passive, consent)["comparable"]
    assert not compare_reports(passive, protected)["comparable"]


# --- command line --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["https://example.com", "--consent", "maybe"],
    ["https://example.com", "--consent", "reject,reject"],
    ["https://example.com", "--consent", "reject", "--blocklist", "x.txt"],
    ["https://example.com", "--consent", "reject", "--out", "data/runs"],
])
def test_cli_refuses_unsafe_combinations(argv):
    with pytest.raises(SystemExit):
        cli_main(argv)


# --- site views ------------------------------------------------------------------------------------------------

def consent_report(name, url, stem_vantage, reject, accept=None):
    r = ok_report(name, url, tracking=4, vantage=stem_vantage)
    r["measurement"]["interaction"] = "consent:reject+accept"
    r["summary"]["consent"] = {"reject": reject, **({"accept": accept} if accept else {})}
    return r


CLICKED = {"outcome": "clicked", "cmp": "OneTrust", "button_text": "Reject all", "after_seconds": 12.0,
           "metrics": {"tracking_services_before": 4, "tracking_services_after": 2, "tracking_services_new_after": 1,
                       "third_party_cookies_before": 0, "third_party_cookies_after": 1},
           "services_after": [{"service": "criteo.com"}, {"service": "doubleclick.net"}],
           "services_new_after": [{"service": "criteo.com"}]}
NOT_FOUND = {"outcome": "no_button", "cmp": "Sourcepoint", "paid_option": "Reject and subscribe"}


@pytest.fixture
def tests_site(tmp_path):
    a = ok_report("News A", "https://a.example/", tracking=10, vantage="github-actions-us")
    a["summary"]["metrics"].update(third_party_requests=100, third_party_bytes=2_000_000)
    write(tmp_path, "2026-10-04", {"a": a, "b": ok_report("News B", "https://b.example/", tracking=3, vantage="github-actions-us")})
    folder = tmp_path / "consent" / "local-windows-spain" / "2026-10-05"
    folder.mkdir(parents=True)
    (folder / "a.json").write_text(json.dumps(consent_report("News A", "https://a.example/", "local-windows-spain", CLICKED, CLICKED)))
    (folder / "b.json").write_text(json.dumps(consent_report("News B", "https://b.example/", "local-windows-spain", NOT_FOUND)))
    (folder / "zz.json").write_text(json.dumps(consent_report("Not in list", "https://zz.example/", "local-windows-spain", NOT_FOUND)))
    prot = ok_report("News A", "https://a.example/", tracking=2, vantage="github-actions-us")
    prot["summary"]["metrics"].update(third_party_requests=40, third_party_bytes=500_000)
    prot["summary"]["protection"] = {"blocked_requests": 55, "blocked_domains": ["criteo.com"]}
    prot["measurement"]["blocklist"] = {"name": "EasyPrivacy", "url": "https://easylist.to/easylist/easyprivacy.txt",
                                        "sha256": "abc", "rules_total": 100, "rules_applied": 60}
    pfolder = tmp_path / "protected" / "github-actions-us" / "2026-10-06"
    pfolder.mkdir(parents=True)
    (pfolder / "a.json").write_text(json.dumps(prot))
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": [{"name": "News A", "url": "https://a.example/", "group": "UK"},
                                           {"name": "News B", "url": "https://b.example/", "group": "DE"}]}))
    out = tmp_path / "site"
    ctx = build_site(tmp_path / "runs", out, sites_file=str(sites))
    return out, ctx


def test_consent_view_counts_and_flags_pay_or_accept(tests_site):
    _, ctx = tests_site
    o = ctx["consent"]["origins"][0]
    assert o["label"] == "a PC in Spain" and o["modes"] == ["reject", "accept"]
    s = o["stats"]
    assert s["measured"] == 3 and s["reject_clicked"] == 1 and s["still_tracking_after_reject"] == 1
    assert s["reject_not_found"] == 2 and s["pay_or_accept"] == 2


def test_protection_view_pairs_with_the_closest_passive_measurement(tests_site):
    _, ctx = tests_site
    o = ctx["protection"]["origins"][0]
    assert o["base_date"] == "2026-10-04" and o["stats"]["paired"] == 1
    row = o["rows"][0]
    assert row["requests_cut"] == 60 and row["bytes_cut"] == 75 and row["tracking_cut"] == 8


def test_test_pages_render_with_safe_links_and_downloads(tests_site):
    out, _ = tests_site
    consent = (out / "consent.html").read_text(encoding="utf-8")
    assert "Reject and subscribe" in consent and "pressed" in consent and "not found" in consent
    assert 'href="sites/a.html"' in consent and 'sites/zz.html' not in consent
    protection = (out / "protection.html").read_text(encoding="utf-8")
    assert "EasyPrivacy" in protection and "60%" in protection and "not a recommendation" in protection
    site_a = (out / "sites" / "a.html").read_text(encoding="utf-8")
    assert "After the banner (separate test)" in site_a and "With a blocking list (separate test)" in site_a
    assert "consent.csv" in (out / "method.html").read_text(encoding="utf-8")
    assert (out / "data" / "consent" / "local-windows-spain" / "2026-10-05" / "a.json").exists()
    assert (out / "data" / "protected" / "github-actions-us" / "2026-10-06" / "a.json").exists()
    rows = (out / "data" / "consent.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 + 3 * 2
    assert "EasyPrivacy" in (out / "data" / "protection.csv").read_text(encoding="utf-8")


def test_pages_say_the_test_has_not_run_when_there_is_no_data(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report(vantage="github-actions-us")})
    build_site(tmp_path / "runs", tmp_path / "site")
    assert "has not been run yet" in (tmp_path / "site" / "consent.html").read_text(encoding="utf-8")
    assert "has not been run yet" in (tmp_path / "site" / "protection.html").read_text(encoding="utf-8")


# --- the detector, on local pages (needs Chromium; skipped otherwise) ------------------------------------------

@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        p = sync_api.sync_playwright().start()
        b = p.chromium.launch()
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"Chromium not available: {exc}")
    yield b
    b.close()
    p.stop()


def page_with(browser, html):
    page = browser.new_page()
    page.set_content(html)
    page.evaluate("() => { window.__clicked = []; document.addEventListener('click', e => window.__clicked.push((e.target.innerText || e.target.id || '').trim()), true); }")
    return page


def test_known_tool_ids_are_used_first(browser):
    from traceguard.consent import act
    page = page_with(browser, '<div id="onetrust-banner-sdk"><p>We use cookies</p>'
                              '<button id="onetrust-accept-btn-handler">Accept All Cookies</button>'
                              '<button id="onetrust-reject-all-handler">Reject All</button></div>')
    record = act(page, "reject", settle_ms=0)
    assert record["outcome"] == "clicked" and record["cmp"] == "OneTrust" and record["method"] == "selector"
    assert page.evaluate("window.__clicked") == ["Reject All"]


def test_text_match_inside_a_consent_block_in_german_and_shadow_dom(browser):
    from traceguard.consent import act
    page = page_with(browser, '<div id="host"></div><script>const r = document.getElementById("host").attachShadow({mode: "open"});'
                              'r.innerHTML = "<section><p>Wir verwenden Cookies und Partner</p><button>Alle akzeptieren</button>'
                              '<button>Alle ablehnen</button></section>";</script>')
    record = act(page, "reject", settle_ms=0)
    assert record["outcome"] == "clicked" and record["method"] == "text" and record["button_text"] == "alle ablehnen"


def test_buttons_outside_a_consent_block_and_links_away_are_never_clicked(browser):
    from traceguard.consent import act
    page = page_with(browser, '<form><p>Newsletter</p><button type="button">Accept</button></form>'
                              '<div><p>This site uses cookies.</p><a href="https://elsewhere.example/">Reject all</a></div>')
    assert act(page, "accept", settle_ms=0)["outcome"] == "no_banner"
    record = act(page, "reject", settle_ms=0)
    assert record["outcome"] == "no_banner" and page.evaluate("window.__clicked") == []


def test_banner_without_reject_records_the_paid_option_and_clicks_nothing(browser):
    from traceguard.consent import act
    page = page_with(browser, '<div class="cmp"><p>We and our partners use cookies for personalised ads.</p>'
                              '<button>Accept all</button><button>Reject all and subscribe</button></div>')
    record = act(page, "reject", settle_ms=0)
    assert record["outcome"] == "no_button" and record["paid_option"] == "Reject all and subscribe"
    assert page.evaluate("window.__clicked") == []


def test_subscription_links_outside_the_banner_are_not_a_paid_option(browser):
    from traceguard.consent import act
    page = page_with(browser, '<header><a href="#">Abo</a></header>'
                              '<div class="cmp"><p>Wir und unsere Partner nutzen Cookies.</p><button>Alle akzeptieren</button></div>'
                              '<footer><p>Datenschutz und Cookies</p><a href="#">Abo kündigen</a></footer>')
    record = act(page, "reject", settle_ms=0)
    assert record["outcome"] == "no_button" and record["paid_option"] is None
