import json

from traceguard.diff import compare_directories, compare_reports


def service(name, tracking=True, stable=True):
    return {"service": name, "entity": "E", "category": "advertising" if tracking else "tag_manager",
            "tracking": tracking, "passes_seen": 3 if stable else 1, "stable": stable}


def report(services, domains=(), *, vantage="github-actions-us", status="ok", at="2026-10-04T05:00:00+00:00",
           requests=10):
    metrics = {"third_party_requests": requests, "third_party_domains": len(domains),
               "tracking_services": sum(1 for s in services if s["tracking"] and s["stable"]),
               "third_party_cookies": 0, "cookies_total": 1, "storage_items": 0}
    return {
        "site": {"name": "News", "url": "https://news.example/"}, "generated_at": at,
        "measurement": {"vantage": vantage, "locale": "en-US", "timezone": "UTC", "tracker_list": "seed"},
        "summary": {"status": status, "services": list(services), "metrics": metrics,
                    "third_party_domains": [{"domain": d, "passes_seen": 3, "stable": True} for d in domains]},
    }


def test_reports_the_difference_in_stable_tracking_services():
    old = report([service("a.example"), service("b.example")])
    new = report([service("b.example"), service("c.example")], requests=14)
    result = compare_reports(old, new)
    assert result["comparable"] and result["notable"]
    assert [s["service"] for s in result["added_tracking_services"]] == ["c.example"]
    assert [s["service"] for s in result["removed_tracking_services"]] == ["a.example"]
    assert result["metrics"]["third_party_requests"]["delta"] == 4


def test_unstable_services_do_not_count_as_changes():
    old = report([service("a.example")])
    new = report([service("a.example"), service("flaky.example", stable=False)])
    result = compare_reports(old, new)
    assert result["added_tracking_services"] == [] and not result["notable"]


def test_tag_managers_are_not_tracking_changes():
    old = report([])
    new = report([service("gtm.example", tracking=False)])
    assert compare_reports(old, new)["added_tracking_services"] == []


def test_different_vantage_is_not_comparable():
    result = compare_reports(report([], vantage="local-windows-spain"), report([]))
    assert result["comparable"] is False
    assert "different vantage" in result["reasons"][0]


def test_failed_measurement_is_not_comparable():
    result = compare_reports(report([]), report([], status="blocked"))
    assert result["comparable"] is False
    assert "current measurement status is blocked" in result["reasons"]


def test_domain_changes_are_listed():
    result = compare_reports(report([], ["x.example"]), report([], ["y.example"]))
    assert result["added_domains"] == ["y.example"] and result["removed_domains"] == ["x.example"]


def test_compare_directories_uses_latest_folder_and_most_recent_earlier_report(tmp_path):
    for day, services in (("2026-09-27", [service("a.example")]), ("2026-10-04", [service("b.example")])):
        folder = tmp_path / day
        folder.mkdir()
        (folder / "news-example.json").write_text(json.dumps(report(services, at=day)), encoding="utf-8")
    (tmp_path / "2026-10-04" / "new-site.json").write_text(json.dumps(report([])), encoding="utf-8")
    results = compare_directories(tmp_path)
    assert len(results) == 1 and results[0]["notable"]


def test_compare_directories_needs_two_dates(tmp_path):
    (tmp_path / "2026-10-04").mkdir()
    assert compare_directories(tmp_path) == []
