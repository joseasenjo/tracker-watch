import json
from datetime import datetime, timedelta, timezone

import pytest

from traceguard import ondemand
from traceguard.ondemand import check_limits, extract_url, main, refusal_comment, render_comment

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
OLD_ACCOUNT = "2020-01-01T00:00:00Z"


def issue(author, hours_ago):
    return {"author": author, "created_at": (NOW - timedelta(hours=hours_ago)).isoformat()}


def test_extract_url_prefers_the_site_address_field():
    body = "### Site address\n\nhttps://www.example-news.com/\n\n### Why\n\nsee https://other.example/x"
    assert extract_url(body) == "https://www.example-news.com/"
    assert extract_url("just text") is None
    assert extract_url("### Site address\n\n_No response_") is None
    assert extract_url("look at http://a.example/page.") == "http://a.example/page"


def test_two_requests_per_account_are_allowed_and_the_third_is_refused():
    assert check_limits("ana", OLD_ACCOUNT, [issue("ana", 1)], NOW)[0]
    assert check_limits("ana", OLD_ACCOUNT, [issue("ana", 5), issue("ana", 1)], NOW)[0]
    allowed, reason = check_limits("ana", OLD_ACCOUNT, [issue("ana", 8), issue("ana", 5), issue("ana", 1)], NOW)
    assert not allowed and "at most 2 requests per account" in reason


def test_requests_outside_the_window_and_from_others_do_not_count():
    history = [issue("ana", 30), issue("ana", 26), issue("bob", 1), issue("bob", 2), issue("ana", 1)]
    assert check_limits("ana", OLD_ACCOUNT, history, NOW)[0]


def test_author_matching_ignores_case():
    history = [issue("Ana", 3), issue("ANA", 2), issue("ana", 1)]
    assert not check_limits("ana", OLD_ACCOUNT, history, NOW)[0]


def test_new_accounts_are_refused():
    recent = (NOW - timedelta(days=2)).isoformat()
    allowed, reason = check_limits("ana", recent, [issue("ana", 1)], NOW)
    assert not allowed and "at least 7 days old" in reason


def test_daily_cap_for_the_whole_site():
    history = [issue(f"user{i}", 1) for i in range(31)]
    allowed, reason = check_limits("user0", OLD_ACCOUNT, history, NOW)
    assert not allowed and "daily capacity" in reason


def test_comment_for_a_measured_site_is_factual_and_marks_the_result_as_unranked():
    report = {"site": {"url": "https://news.example/"}, "measurement": {"vantage": "github-actions-us", "passes": 2,
                                                                         "observe_seconds": 10.0},
              "summary": {"status": "ok", "confidence": "high", "passes_ok": 2, "passes_total": 2,
                          "metrics": {"tracking_services": 4, "third_party_domains": 9, "third_party_requests": 30,
                                      "third_party_cookies": 1, "cookies_total": 6},
                          "services": [{"service": "doubleclick.net", "entity": "Google", "category": "advertising",
                                        "tracking": True, "stable": True}]}}
    text = render_comment(report)
    assert "Tracking services contacted: **4** (band B)" in text and "`doubleclick.net`: Google, advertising" in text
    assert "not** added to the public ranking" in text and "not legal conclusions" in text


def test_comment_for_a_blocked_site_gives_no_figures():
    report = {"site": {"url": "https://news.example/"}, "measurement": {"vantage": "v", "passes": 2, "observe_seconds": 10.0},
              "summary": {"status": "blocked", "passes_ok": 0, "passes_total": 2}}
    text = render_comment(report)
    assert "No figures: the site refused the automated browser" in text and "do not try to bypass" in text


def test_hostile_text_never_reaches_the_comment():
    report = {"site": {"url": "https://evil<script>@user.example/"}, "measurement": {"vantage": "v", "passes": 2, "observe_seconds": 10.0},
              "summary": {"status": "blocked", "passes_ok": 0, "passes_total": 2}}
    text = render_comment(report)
    assert "<script>" not in text and "@" not in text.split("**Analysis of ")[1].split("**")[0]


@pytest.mark.parametrize("body, expected_reason", [
    ("### Site address\n\nnot a link", "no-url"),
    ("### Site address\n\nhttp://127.0.0.1/admin", "unsafe-url"),
    ("### Site address\n\nftp://files.example/x https://ok.example/", None),
])
def test_cli_dry_run_applies_validation(tmp_path, body, expected_reason, monkeypatch):
    monkeypatch.setattr(ondemand, "check_target", lambda url: url if "127.0.0.1" not in url else (_ for _ in ()).throw(ondemand.UnsafeURL("private address")))
    files = {"body": tmp_path / "b.txt", "hist": tmp_path / "h.json", "out": tmp_path / "o.md", "res": tmp_path / "r.json"}
    files["body"].write_text(body, encoding="utf-8")
    files["hist"].write_text(json.dumps([]), encoding="utf-8")
    code = main(["--body-file", str(files["body"]), "--author", "ana", "--account-created", OLD_ACCOUNT,
                 "--history-file", str(files["hist"]), "--out", str(files["out"]), "--result-file", str(files["res"]),
                 "--dry-run"])
    result = json.loads(files["res"].read_text(encoding="utf-8"))
    assert code == 0
    if expected_reason:
        assert result == {"processed": False, "reason": expected_reason}
        assert files["out"].read_text(encoding="utf-8").startswith("This request was not processed.")
    else:
        assert result["processed"] is True


def test_cli_refuses_when_the_limit_is_reached_without_scanning(tmp_path):
    body, hist, out = tmp_path / "b.txt", tmp_path / "h.json", tmp_path / "o.md"
    body.write_text("### Site address\n\nhttps://ok.example/", encoding="utf-8")
    now = datetime.now(timezone.utc)
    hist.write_text(json.dumps([{"author": "ana", "created_at": (now - timedelta(minutes=m)).isoformat()} for m in (1, 2, 3)]),
                    encoding="utf-8")
    main(["--body-file", str(body), "--author", "ana", "--account-created", OLD_ACCOUNT,
          "--history-file", str(hist), "--out", str(out), "--dry-run"])
    assert "Limit reached" in out.read_text(encoding="utf-8")
