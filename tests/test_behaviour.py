"""What each tracking service did in the browser, counted from the raw request log."""
import pytest

from traceguard.behaviour import operator_footprint, phrases, service_activity, totals
from traceguard.classify import TrackerList
from traceguard.entities import reach
from traceguard.report import build_report
from traceguard.site import build_site

from test_report import DOUBLECLICK, MEASUREMENT, SITE, make_run, own_run, request
from test_site import ok_report, write

TRACKERS = TrackerList.load()
CRITEO = {"service": "criteo.com", "entity": "Criteo", "category": "advertising"}


def req(host, domain, kind, tracker, **extra):
    item = request(host, domain, "third", tracker)
    item["resource_type"] = kind
    item.update(extra)
    return item


def cookie(name, domain, session=False, days=None, party="third"):
    c = {"name": name, "domain": domain, "party": party, "session": session, "secure": True, "http_only": False,
         "same_site": "None"}
    if days is not None:
        c["lifetime_days"] = days
    return c


def run(number, requests, cookies=(), observations=()):
    r = make_run(number, [request("news.example", "news.example", "first"), *requests], cookies=list(cookies),
                 observations=list(observations))
    return r


def report(runs):
    return build_report(SITE, MEASUREMENT, runs, TRACKERS)


def three(requests, cookies=(), observations=()):
    return report([run(n, requests, cookies, observations) for n in (1, 2, 3)])


def test_requests_are_counted_by_kind_per_service():
    r = three([req("a.doubleclick.net", "doubleclick.net", "script", DOUBLECLICK),
               req("b.doubleclick.net", "doubleclick.net", "script", DOUBLECLICK),
               req("c.doubleclick.net", "doubleclick.net", "image", DOUBLECLICK),
               req("d.doubleclick.net", "doubleclick.net", "fetch", DOUBLECLICK),
               req("e.doubleclick.net", "doubleclick.net", "document", DOUBLECLICK),
               req("f.doubleclick.net", "doubleclick.net", "font", DOUBLECLICK),
               req("bidder.criteo.com", "criteo.com", "ping", CRITEO)])
    a = service_activity(r, TRACKERS)
    assert a["doubleclick.net"]["requests"] == {"script": 2, "image": 1, "background": 1, "frame": 1, "other": 1}
    assert a["criteo.com"]["requests"]["background"] == 1 and a["criteo.com"]["total"] == 1


def test_first_party_untracked_and_unstable_services_are_left_out():
    flaky = req("x.criteo.com", "criteo.com", "script", CRITEO)
    r = report([run(1, [flaky]), run(2, []), run(3, [])])
    assert service_activity(r, TRACKERS) == {}
    gtm = {"service": "googletagmanager.com", "entity": "Google", "category": "tag_manager"}
    assert service_activity(three([req("www.googletagmanager.com", "googletagmanager.com", "script", gtm)]), TRACKERS) == {}


def test_counts_are_medians_of_the_passes_and_ignore_the_after_click_window():
    base = req("a.doubleclick.net", "doubleclick.net", "script", DOUBLECLICK)
    extra = [req(f"h{i}.doubleclick.net", "doubleclick.net", "script", DOUBLECLICK) for i in range(10)]
    r = report([run(1, [base, *extra]), run(2, [base]), run(3, [base])])
    assert service_activity(r, TRACKERS)["doubleclick.net"]["requests"]["script"] == 1
    after = dict(base, phase="after")
    runs = [run(n, [dict(base, phase="before"), after, after]) for n in (1, 2, 3)]
    assert service_activity(report(runs), TRACKERS)["doubleclick.net"]["requests"]["script"] == 1


def test_bytes_are_summed_when_sizes_were_recorded():
    item = req("a.doubleclick.net", "doubleclick.net", "script", DOUBLECLICK, bytes=1500)
    assert service_activity(three([item, dict(item, bytes=500)]), TRACKERS)["doubleclick.net"]["bytes"] == 2000
    assert service_activity(three([req("a.doubleclick.net", "doubleclick.net", "script", DOUBLECLICK)]), TRACKERS)["doubleclick.net"]["bytes"] is None


def test_cookies_are_attributed_by_domain_and_need_a_majority_of_passes():
    items = [req("a.doubleclick.net", "doubleclick.net", "script", DOUBLECLICK)]
    runs = [run(1, items, [cookie("IDE", "doubleclick.net", days=390), cookie("tmp", "doubleclick.net", session=True),
                           cookie("rare", "doubleclick.net")]),
            run(2, items, [cookie("IDE", "doubleclick.net", days=390), cookie("tmp", "doubleclick.net", session=True)]),
            run(3, items, [cookie("IDE", "doubleclick.net", days=390), cookie("tmp", "doubleclick.net", session=True),
                           cookie("other", "unknown.example")])]
    cookies = service_activity(report(runs), TRACKERS)["doubleclick.net"]["cookies"]
    assert [c["name"] for c in cookies] == ["IDE", "tmp"]
    assert cookies[0] == {"name": "IDE", "persistent": True, "days": 390} and cookies[1]["persistent"] is False


def test_script_behaviours_are_attached_to_the_service_whose_domain_ran_the_script():
    obs = [{"kind": "canvas_read", "script_domain": "doubleclick.net", "party": "third"},
           {"kind": "canvas_read", "script_domain": "other.example", "party": "third"}]
    r = three([req("a.doubleclick.net", "doubleclick.net", "script", DOUBLECLICK)], observations=obs)
    assert service_activity(r, TRACKERS)["doubleclick.net"]["behaviours"] == ["canvas_read"]


def test_phrases_are_plain_and_hedged():
    activity = {"requests": {"script": 1, "image": 3, "background": 0, "frame": 0, "other": 0},
                "cookies": [{"name": "a", "persistent": True, "days": 390}, {"name": "b", "persistent": False, "days": None}],
                "behaviours": ["canvas_read"], "total": 4, "bytes": None}
    text = "; ".join(phrases(activity))
    assert "ran 1 script in the browser" in text and "made 3 image requests" in text
    assert "set 2 cookies (1 that stays after you close the browser, the longest lasting about 390 days)" in text
    assert "a script from this domain read image data from a canvas element" in text
    assert phrases({"requests": {k: 0 for k in ("script", "image", "background", "frame", "other")},
                    "cookies": [{"name": "s", "persistent": False, "days": None}], "behaviours": [], "total": 0, "bytes": None}) \
        == ["set 1 cookie (all end when you close the browser)"]


def test_totals_and_operator_footprint():
    one = {"requests": {"script": 2, "image": 0, "background": 1, "frame": 0, "other": 0}, "total": 3,
           "cookies": [{"name": "a", "persistent": True, "days": None}], "behaviours": [], "bytes": None}
    two = {"requests": {"script": 0, "image": 4, "background": 0, "frame": 0, "other": 0}, "total": 4,
           "cookies": [], "behaviours": [], "bytes": None}
    assert totals({"x.com": one, "y.com": two}) == {"services": 2, "scripts": 2, "requests": 7, "cookies": 1, "persistent_cookies": 1}
    by_site = {"a": {"x.com": one}, "b": {"x.com": two}, "c": {"other.com": one}}
    assert operator_footprint(by_site, {"x.com"}) == {"script": 1, "image": 1, "background": 1, "frame": 0, "cookies": 1, "sites": 2}


def test_reach_carries_the_footprint_only_when_activities_are_given():
    r = report([run(n, [req("a.doubleclick.net", "doubleclick.net", "script", DOUBLECLICK)]) for n in (1, 2, 3)])
    r["site"]["url"] = "https://news.example/"
    plain = reach({"a": r}, {}, set())
    assert plain["rows"][0]["footprint"] is None
    full = reach({"a": r}, {}, set(), {"a": service_activity(r, TRACKERS)})
    assert full["rows"][0]["footprint"]["script"] == 1 and full["rows"][0]["footprint"]["sites"] == 1


@pytest.fixture
def built(tmp_path):
    a = ok_report("News A", "https://a.example/", tracking=1, vantage="github-actions-us")
    a["runs"][0]["requests"][1]["resource_type"] = "script"
    a["runs"][0]["cookies"] = [cookie("IDE", "doubleclick.net", days=390)]
    a["runs"][0]["status"] = "ok"
    write(tmp_path, "2026-10-04", {"a": a})
    return build_site(tmp_path / "runs", tmp_path / "site", repo_url="https://github.com/o/r"), tmp_path / "site"


def test_site_page_lists_what_each_service_did_with_a_clear_limit(built):
    ctx, out = built
    page = (out / "sites" / "a.html").read_text(encoding="utf-8")
    assert "What each tracking service did on your computer" in page
    assert "ran 1 script in the browser" in page and "not what was sent" in page
    assert "glossary.html#on-your-computer" in page
    assert ctx["entries"][0]["activity_totals"]["scripts"] == 1


def test_companies_and_glossary_explain_the_activities(built):
    _, out = built
    assert "On its 1 site it ran scripts on 1" in (out / "companies.html").read_text(encoding="utf-8")
    glossary = (out / "glossary.html").read_text(encoding="utf-8")
    assert 'id="on-your-computer"' in glossary and "never its value" in glossary and "not an accusation" in glossary
