import json

from traceguard.classify import TrackerList
from traceguard.report import aggregate_runs, build_report
from traceguard.scanner import script_host_from_stack, strip_query

TRACKERS = TrackerList.load()


def request(host, domain, party, tracker=None):
    return {"t_ms": 10, "method": "GET", "resource_type": "script", "url": f"https://{host}/x",
            "host": host, "domain": domain, "party": party, "tracker": tracker}


DOUBLECLICK = {"service": "doubleclick.net", "entity": "Google", "category": "advertising"}
GTM = {"service": "googletagmanager.com", "entity": "Google", "category": "tag_manager"}


def make_run(number, requests, cookies=(), observations=(), status="ok", http_status=200):
    return {
        "pass": number, "status": status, "http_status": http_status, "error": None,
        "final_url": "https://news.example/", "navigation_redirects": [],
        "security_headers": {"strict_transport_security": True, "content_security_policy": "absent"},
        "tls_protocol": "TLS 1.3", "requests": list(requests), "internal_requests_blocked": [],
        "cookies": list(cookies), "storage_items": {"local": 2, "session": 1},
        "observations": list(observations),
    }


def own_run(number, extra=()):
    return make_run(number, [
        request("news.example", "news.example", "first"),
        request("cdn.news.example", "news.example", "first"),
        request("stats.g.doubleclick.net", "doubleclick.net", "third", DOUBLECLICK),
        request("www.googletagmanager.com", "googletagmanager.com", "third", GTM),
        *extra,
    ], cookies=[{"name": "a", "domain": "doubleclick.net", "party": "third", "session": False,
                 "secure": True, "http_only": False, "same_site": "None"},
                {"name": "b", "domain": "news.example", "party": "first", "session": True,
                 "secure": True, "http_only": True, "same_site": "Lax"}],
        observations=[{"kind": "key_listener", "script_domain": "news.example", "party": "first"},
                      {"kind": "canvas_read", "script_domain": "doubleclick.net", "party": "third"}])


SITE = {"name": "News", "url": "https://news.example/", "first_party_domains": ["news.example"]}
MEASUREMENT = {"vantage": "test", "observe_seconds": 12.0, "passes": 3}


def test_first_party_requests_are_not_counted_as_third_party():
    summary = aggregate_runs([own_run(1), own_run(2), own_run(3)], TRACKERS)
    assert summary["metrics"]["third_party_requests"] == 2
    assert summary["metrics"]["third_party_domains"] == 2


def test_tag_managers_are_not_counted_as_tracking_services():
    summary = aggregate_runs([own_run(1), own_run(2), own_run(3)], TRACKERS)
    assert summary["metrics"]["tracking_services"] == 1
    assert summary["metrics"]["third_party_cookies"] == 1


def test_intermittent_domains_are_marked_unstable():
    flaky = request("ads.flaky.example", "flaky.example", "third")
    runs = [own_run(1, [flaky]), own_run(2), own_run(3)]
    domains = {d["domain"]: d for d in aggregate_runs(runs, TRACKERS)["third_party_domains"]}
    assert domains["flaky.example"]["passes_seen"] == 1 and not domains["flaky.example"]["stable"]
    assert domains["doubleclick.net"]["stable"]


def test_requests_without_classification_are_ignored_instead_of_crashing():
    late = {"t_ms": 99, "method": "GET", "resource_type": "image", "url": "https://late.example/x",
            "host": "late.example"}
    summary = aggregate_runs([own_run(1, [late]), own_run(2), own_run(3)], TRACKERS)
    assert summary["metrics"]["third_party_requests"] == 2


def test_median_is_used_across_passes():
    extra = [request(f"h{i}.t.example", "t.example", "third") for i in range(10)]
    runs = [own_run(1, extra), own_run(2), own_run(3)]
    assert aggregate_runs(runs, TRACKERS)["metrics"]["third_party_requests"] == 2


def test_blocked_run_has_no_metrics_and_no_score():
    runs = [make_run(i, [], status="blocked", http_status=403) for i in (1, 2, 3)]
    report = build_report(SITE, MEASUREMENT, runs, TRACKERS)
    assert report["summary"]["status"] == "blocked"
    assert "metrics" not in report["summary"]
    assert "score" not in json.dumps(report).lower().replace("passes_ok", "")
    assert [f["code"] for f in report["findings"]] == ["MEASUREMENT_BLOCKED"]


def test_error_status_is_reported_as_error_not_as_a_clean_result():
    runs = [make_run(1, [], status="error", http_status=None)]
    runs[0]["error"] = "TimeoutError: page.goto"
    report = build_report(SITE, MEASUREMENT, runs, TRACKERS)
    assert report["summary"]["status"] == "error"
    assert [f["code"] for f in report["findings"]] == ["MEASUREMENT_ERROR"]


def test_findings_are_factual_and_never_claim_keylogging_or_intent():
    report = build_report(SITE, MEASUREMENT, [own_run(1), own_run(2), own_run(3)], TRACKERS)
    text = " ".join(f["text"] for f in report["findings"]).lower()
    codes = {f["code"] for f in report["findings"]}
    assert {"THIRD_PARTY_REQUESTS", "TRACKING_SERVICES", "THIRD_PARTY_COOKIES", "SCRIPT_BEHAVIOUR", "NO_CSP"} <= codes
    assert "keylogger" not in text and "spying" not in text and "to track you" not in text
    assert "NO_HSTS" not in codes


def test_first_party_script_behaviour_is_not_reported():
    report = build_report(SITE, MEASUREMENT, [own_run(1), own_run(2), own_run(3)], TRACKERS)
    behaviours = [f for f in report["findings"] if f["code"] == "SCRIPT_BEHAVIOUR"]
    assert [b["evidence"]["script_domain"] for b in behaviours] == ["doubleclick.net"]


def test_confidence_reflects_how_many_passes_were_measured():
    blocked = lambda n: make_run(n, [], status="blocked", http_status=403)
    high = aggregate_runs([own_run(1), own_run(2), own_run(3)], TRACKERS)
    medium = aggregate_runs([own_run(1), own_run(2), blocked(3)], TRACKERS)
    low = aggregate_runs([blocked(1), blocked(2), own_run(3)], TRACKERS)
    assert (high["confidence"], medium["confidence"], low["confidence"]) == ("high", "medium", "low")
    assert low["failed_passes"] == {"blocked": 2}


def test_low_confidence_is_reported_as_a_notable_finding():
    runs = [make_run(1, [], status="blocked", http_status=403), make_run(2, [], status="blocked", http_status=403),
            own_run(3)]
    report = build_report(SITE, MEASUREMENT, runs, TRACKERS)
    finding = next(f for f in report["findings"] if f["code"] == "LOW_CONFIDENCE")
    assert finding["severity"] == "notable" and "1 of 3" in finding["text"] and "indicative only" in finding["text"]


def test_full_confidence_adds_no_confidence_finding():
    report = build_report(SITE, MEASUREMENT, [own_run(1), own_run(2), own_run(3)], TRACKERS)
    assert "LOW_CONFIDENCE" not in {f["code"] for f in report["findings"]}


def test_incomplete_passes_give_incomplete_status_and_no_figures():
    runs = [make_run(i, [], status="incomplete", http_status=200) for i in (1, 2, 3)]
    runs[0]["error"] = "only 1 request(s) observed (minimum 5)"
    report = build_report(SITE, MEASUREMENT, runs, TRACKERS)
    assert report["summary"]["status"] == "incomplete"
    assert "metrics" not in report["summary"]
    assert [f["code"] for f in report["findings"]] == ["MEASUREMENT_INCOMPLETE"]


def test_report_is_json_serialisable_and_versioned():
    report = build_report(SITE, MEASUREMENT, [own_run(1), own_run(2), own_run(3)], TRACKERS)
    parsed = json.loads(json.dumps(report))
    assert parsed["schema_version"] == "0.2"
    assert parsed["tool"]["name"] == "traceguard"
    assert parsed["measurement"]["vantage"] == "test"


def test_strip_query_removes_query_and_fragment():
    assert strip_query("https://a.example/p/x?token=secret#frag") == "https://a.example/p/x"


def test_script_host_from_stack():
    assert script_host_from_stack("https://cdn.tracker.example/lib.js:1:2") == "cdn.tracker.example"
    assert script_host_from_stack(None) is None
