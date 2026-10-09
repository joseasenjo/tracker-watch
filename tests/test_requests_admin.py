"""Public requests without approval: limits file, short-link requests, the on-demand analysis, the local admin
server, the glossary and the pages that offer the two tools."""
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from traceguard import ondemand
from traceguard.admin import make_server, recent_requests
from traceguard.categories import CATEGORIES, describe
from traceguard.limits import DEFAULTS, LimitsError, check_account, load_limits, main as limits_main, save_limits, validate
from traceguard.links import generate_code, load_links, main as links_main, process_request
from traceguard.site import build_site

from test_links import BLOCK, PUBLIC
from test_site import ok_report, write

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
OLD = "2020-01-01T00:00:00Z"


def hist(author, hours_ago, number=1):
    return {"number": number, "author": author, "created_at": (NOW - timedelta(hours=hours_ago)).isoformat()}


# --- limits file ---------------------------------------------------------------------------------------------

def test_missing_file_means_defaults_and_values_are_filled_in(tmp_path):
    assert load_limits(tmp_path / "none.json") == DEFAULTS
    assert validate({"scan": {"per_account": 1}})["links"] == DEFAULTS["links"]
    assert validate({"blocked_accounts": ["Bad-User", "bad-user"]})["blocked_accounts"] == ["bad-user"]


@pytest.mark.parametrize("data", [
    [], {"scan": {"per_account": -1}}, {"scan": {"per_account": 1.5}}, {"scan": {"per_account": True}},
    {"scan": {"per_account": 9999}}, {"links": {"enabled": "yes"}}, {"scan": {"max_total": 5}},
    {"other": {}}, {"blocked_accounts": ["x y"]}, {"blocked_accounts": "bob"}, {"scan": []},
    {"blocked_accounts": ["a"] * 201}])
def test_invalid_limits_are_rejected(data):
    with pytest.raises(LimitsError):
        validate(data)


def test_broken_file_is_an_error_not_unlimited(tmp_path):
    path = tmp_path / "limits.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LimitsError):
        load_limits(path)


def test_save_roundtrip_and_cli(tmp_path, capsys):
    path = tmp_path / "limits.json"
    assert limits_main(["--file", str(path), "set", "scan.per_account=1", "links.enabled=false"]) == 0
    saved = load_limits(path)
    assert saved["scan"]["per_account"] == 1 and saved["links"]["enabled"] is False
    assert limits_main(["--file", str(path), "block", "Spammer"]) == 0
    assert load_limits(path)["blocked_accounts"] == ["spammer"]
    assert limits_main(["--file", str(path), "unblock", "spammer"]) == 0 and load_limits(path)["blocked_accounts"] == []
    assert limits_main(["--file", str(path), "set", "scan.per_account=-4"]) == 2
    assert limits_main(["--file", str(path), "set", "nonsense"]) == 2
    assert save_limits({"scan": {"window_hours": 48}}, path)["scan"]["window_hours"] == 48


def test_shipped_limits_file_is_valid():
    limits = load_limits("data/limits.json")  # the owner may change the values from the dashboard
    assert set(limits) == set(DEFAULTS) and set(limits["scan"]) == set(DEFAULTS["scan"])


def test_check_account_covers_switch_blocklist_age_window_and_daily_cap():
    s = dict(DEFAULTS["scan"])
    assert check_account(s, [], "ana", OLD, [hist("ana", 1)], NOW)[0]
    assert "switched off" in check_account({**s, "enabled": False}, [], "ana", OLD, [hist("ana", 1)], NOW)[1]
    assert "cannot use" in check_account(s, ["ANA"], "ana", OLD, [hist("ana", 1)], NOW)[1]
    assert "7 days" in check_account(s, [], "ana", (NOW - timedelta(days=1)).isoformat(), [hist("ana", 1)], NOW)[1]
    three = [hist("ana", 5, 1), hist("ana", 3, 2), hist("ana", 1, 3)]
    assert "at most 2" in check_account(s, [], "ana", OLD, three, NOW)[1]
    assert check_account({**s, "per_account": 3}, [], "ana", OLD, three, NOW)[0]
    crowd = [hist(f"u{i}", 1, i) for i in range(31)]
    assert "daily capacity" in check_account(s, [], "u0", OLD, crowd, NOW)[1]


def test_on_demand_analysis_reads_the_shared_limits_file(tmp_path):
    limits = tmp_path / "limits.json"
    body, history, out = tmp_path / "b.txt", tmp_path / "h.json", tmp_path / "c.md"
    body.write_text("### Site address\n\nhttps://example.com/", encoding="utf-8")
    history.write_text(json.dumps([{"author": "ana", "created_at": datetime.now(timezone.utc).isoformat()}]), encoding="utf-8")
    base = ["--body-file", str(body), "--author", "ana", "--account-created", OLD, "--history-file", str(history),
            "--limits", str(limits), "--out", str(out), "--result-file", str(tmp_path / "r.json"), "--dry-run"]
    save_limits({"scan": {"enabled": False}}, limits)
    ondemand.main(base)
    assert "switched off" in out.read_text(encoding="utf-8")
    save_limits({"blocked_accounts": ["ana"]}, limits)
    ondemand.main(base)
    assert "cannot use" in out.read_text(encoding="utf-8")
    limits.write_text("broken", encoding="utf-8")
    ondemand.main(base)
    assert "paused" in out.read_text(encoding="utf-8")
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["processed"] is False


# --- short links without approval ----------------------------------------------------------------------------

BODY = "### Target address\n\nhttps://example.com/page\n\n### Preferred short code\n\n{code}\n\n### Why do you need it?\n\nslides"
OK_CHECK = {"status": "ok", "tracking": 4, "band": "B", "confidence": "high", "vantage": "github-actions-us",
            "date": "2026-10-11", "domains": []}


def request(tmp_path, body=None, author="ana", history=None, limits=None, scan=lambda url: dict(OK_CHECK), created=OLD):
    return process_request(body if body is not None else BODY.format(code="my-talk"), author, created,
                           history if history is not None else [hist(author, 1)], limits or DEFAULTS,
                           tmp_path / "links.json", "https://owner.github.io/tracker-watch", now=NOW,
                           blocklist=BLOCK, resolver=PUBLIC, scan=scan)


def test_a_valid_request_creates_the_link_with_no_approval(tmp_path):
    result = request(tmp_path)
    assert result["created"] and result["code"] == "my-talk"
    assert "https://owner.github.io/tracker-watch/go/my-talk/" in result["comment"]
    assert "contacted 4 tracking services" in result["comment"]
    saved = load_links(tmp_path / "links.json")
    assert saved[0]["code"] == "my-talk" and saved[0]["host"] == "example.com" and saved[0]["check"]["tracking"] == 4


def test_without_a_code_a_random_one_is_generated_and_the_same_address_is_reused(tmp_path):
    first = request(tmp_path, BODY.format(code="_No response_"))
    assert first["created"] and len(first["code"]) == 7
    again = request(tmp_path, BODY.format(code="_No response_"))
    assert not again["created"] and again["reason"] == "exists" and again["code"] == first["code"]
    assert len(load_links(tmp_path / "links.json")) == 1
    assert generate_code({"abc"}) not in {"abc"}


@pytest.mark.parametrize("kwargs, reason", [
    ({"limits": {**DEFAULTS, "links": {**DEFAULTS["links"], "enabled": False}}}, "limit"),
    ({"limits": {**DEFAULTS, "blocked_accounts": ["ana"]}}, "limit"),
    ({"history": [hist("ana", h, h) for h in (1, 2, 3, 4)]}, "limit"),
    ({"created": (NOW - timedelta(days=1)).isoformat()}, "limit"),
    ({"body": "### Target address\n\n_No response_"}, "no-url"),
    ({"body": BODY.format(code="ADMIN")}, "invalid"),
    ({"body": BODY.format(code="ok").replace("https://example.com/page", "https://bit.ly/abc")}, "invalid"),
    ({"body": BODY.format(code="ok").replace("https://example.com/page", "http://127.0.0.1/admin")}, "invalid"),
    ({"body": BODY.format(code="ok").replace("https://example.com/page", "https://user:pw@example.com/")}, "invalid"),
    ({"scan": lambda url: {"status": "error"}}, "unreachable"),
    ({"limits": {**DEFAULTS, "links": {**DEFAULTS["links"], "max_total": 0}}}, "full"),
])
def test_refused_requests_create_nothing(tmp_path, kwargs, reason):
    result = request(tmp_path, **kwargs)
    assert not result["created"] and result["reason"] == reason
    assert result["comment"].startswith("This request was not processed")
    assert load_links(tmp_path / "links.json") == []


def test_a_taken_code_is_refused_and_a_blocked_scan_still_creates_the_link(tmp_path):
    assert request(tmp_path)["created"]
    taken = request(tmp_path, BODY.format(code="my-talk").replace("example.com/page", "example.org/other"))
    assert taken["reason"] == "code-taken"
    blocked = request(tmp_path, BODY.format(code="second").replace("example.com/page", "example.net/x"),
                      scan=lambda url: {"status": "blocked"})
    assert blocked["created"]


def test_the_command_line_entry_used_by_the_workflow(tmp_path):
    (tmp_path / "body.txt").write_text(BODY.format(code="from-cli"), encoding="utf-8")
    (tmp_path / "hist.json").write_text(json.dumps([hist("ana", 1)]), encoding="utf-8")
    args = ["--file", str(tmp_path / "links.json"), "request", "--body-file", str(tmp_path / "body.txt"),
            "--author", "ana", "--account-created", OLD, "--history-file", str(tmp_path / "hist.json"),
            "--limits", str(tmp_path / "none.json"), "--site-url", "https://o.github.io/r/",
            "--out", str(tmp_path / "c.md"), "--result-file", str(tmp_path / "r.json"), "--dry-run"]
    import traceguard.links as links_module
    original = links_module.validate_target
    links_module.validate_target = lambda url, block, resolver=PUBLIC: original(url, block, PUBLIC)
    try:
        assert links_main(args) == 0
    finally:
        links_module.validate_target = original
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8")) == {"created": True, "code": "from-cli", "reason": "created"}
    (tmp_path / "limits.json").write_text("broken", encoding="utf-8")
    assert links_main(args[:-3] + ["--limits", str(tmp_path / "limits.json"), "--out", str(tmp_path / "c2.md"), "--dry-run"]) == 0
    assert "paused" in (tmp_path / "c2.md").read_text(encoding="utf-8")


# --- local admin server --------------------------------------------------------------------------------------------

@pytest.fixture
def admin(tmp_path):
    (tmp_path / "dash").mkdir()
    (tmp_path / "dash" / "index.html").write_text("<h1>dash</h1>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        label = cmd[cmd.index("--label") + 1]
        payload = [{"number": 1, "author": {"login": "ana"}, "createdAt": datetime.now(timezone.utc).isoformat(), "state": "CLOSED"},
                   {"number": 2, "author": {"login": "old"}, "createdAt": "2020-01-01T00:00:00Z", "state": "CLOSED"}]
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload if label == "scan-request" else []))

    server = make_server(tmp_path / "dash", tmp_path / "limits.json", "o/r", 0, runner)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield SimpleNamespace(base=f"http://127.0.0.1:{port}", port=port, limits=tmp_path / "limits.json", calls=calls)
    server.shutdown()
    server.server_close()


def call(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_server_serves_the_dashboard_and_the_current_limits(admin):
    assert call(admin.base + "/")[1] == "<h1>dash</h1>"
    status, body = call(admin.base + "/api/limits")
    assert status == 200 and json.loads(body)["limits"] == DEFAULTS
    assert call(admin.base + "/../secret.txt")[0] in (400, 404)
    assert call(admin.base + "/api/nothing")[0] == 404


def test_saving_needs_the_token_json_and_valid_values(admin):
    token = json.loads(call(admin.base + "/api/token")[1])["token"]
    good = {"scan": {"per_account": 1}}
    json_header = {"Content-Type": "application/json"}
    assert call(admin.base + "/api/limits", good, json_header)[0] == 403
    assert call(admin.base + "/api/limits", good, {**json_header, "X-Admin-Token": "wrong"})[0] == 403
    assert call(admin.base + "/api/limits", good, {"Content-Type": "text/plain", "X-Admin-Token": token})[0] == 415
    status, body = call(admin.base + "/api/limits", {"scan": {"per_account": -3}}, {**json_header, "X-Admin-Token": token})
    assert status == 400 and not admin.limits.exists()
    status, body = call(admin.base + "/api/limits", good, {**json_header, "X-Admin-Token": token})
    assert status == 200 and load_limits(admin.limits)["scan"]["per_account"] == 1


def test_requests_for_other_hosts_are_refused_against_dns_rebinding(admin):
    req = urllib.request.Request(admin.base + "/api/token", headers={"Host": "evil.example"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert err.value.code == 403
    assert "Access-Control-Allow-Origin" not in dict(urllib.request.urlopen(admin.base + "/api/limits").headers)


def test_usage_counts_only_the_last_24_hours_and_degrades_without_gh(admin):
    status, body = call(admin.base + "/api/usage")
    usage = json.loads(body)
    assert usage["available"] and [i["author"] for i in usage["scan"]] == ["ana"] and usage["links"] == []

    def missing(cmd, **kw):
        raise FileNotFoundError("gh")

    assert recent_requests("o/r", runner=missing)["available"] is False
    assert recent_requests("o/r", runner=lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=""))["available"] is False


# --- glossary and public pages ---------------------------------------------------------------------------------------

@pytest.fixture
def site(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report("News A", "https://a.example/", vantage="github-actions-us")})
    (tmp_path / "limits.json").write_text(json.dumps({"scan": {"per_account": 4}, "links": {"enabled": False}}), encoding="utf-8")
    ctx = build_site(tmp_path / "runs", tmp_path / "site", repo_url="https://github.com/o/r")
    return tmp_path / "site", ctx


def test_glossary_describes_every_category_without_scoring_anyone(site):
    out, ctx = site
    page = (out / "glossary.html").read_text(encoding="utf-8")
    for category in CATEGORIES.values():
        assert category["label"] in page
    assert "typically" in page and "privacy score" in page and "not counted in the tracking figure" in page
    assert {c["id"] for c in ctx["categories"]} == set(CATEGORIES)
    assert sum(c["entries"] for c in ctx["categories"]) == ctx["tracker_count"]


def test_categories_link_to_the_glossary_from_the_site_page_and_method(site):
    out, _ = site
    assert 'href="../glossary.html#advertising"' in (out / "sites" / "a.html").read_text(encoding="utf-8")
    assert 'href="glossary.html"' in (out / "method.html").read_text(encoding="utf-8")


def test_try_it_page_states_the_current_limits_and_switches(site):
    out, _ = site
    page = (out / "request.html").read_text(encoding="utf-8")
    assert "Limit: 4 per GitHub account every 24 hours" in page
    assert "Short links are switched off for now" in page and 'data-template="link-request.yml"' not in page
    assert 'data-template="scan-request.yml"' in page and "nobody approves" in page.replace("Nobody approves it by hand", "nobody approves")
    assert "public GitHub issue" in page


def test_home_and_navigation_offer_both_tools_together(site):
    out, _ = site
    home = (out / "index.html").read_text(encoding="utf-8")
    assert "Analyse an address" in home and "Create a short link" in home
    assert 'href="request.html"' in home and ">Try it</a>" in home


def test_site_script_prefills_issue_forms_for_both_tools():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "site_src" / "assets" / "site.js").read_text(encoding="utf-8")
    assert "scan-request.yml" in js and "Analyse it now" in js and "form[data-template]" in js
    assert "noopener" in js


def test_issue_forms_and_workflows_match_what_the_code_reads():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parent.parent / ".github"
    scan = yaml.safe_load((root / "ISSUE_TEMPLATE" / "scan-request.yml").read_text(encoding="utf-8"))
    link = yaml.safe_load((root / "ISSUE_TEMPLATE" / "link-request.yml").read_text(encoding="utf-8"))
    labels = lambda form: [b["attributes"]["label"] for b in form["body"] if b["type"] != "markdown"]
    assert "scan-request" in scan["labels"] and labels(scan) == ["Site address"]
    assert "link-request" in link["labels"] and labels(link)[:2] == ["Target address", "Preferred short code"]
    workflow = (root / "workflows" / "requests.yml").read_text(encoding="utf-8")
    assert "github.event.issue.body" in workflow and "${{ github.event.issue.body }}" not in workflow.split("run:")[1:][0]
    for run_block in workflow.split("run: ")[1:]:
        assert "${{ github.event.issue" not in run_block.split("\n      - ")[0], "issue text must not reach a shell script"
    assert "publish-site.yml" in workflow and "data/limits.json" in workflow


def test_limit_refusal_points_to_the_developer_and_the_reply_carries_a_hidden_machine_readable_result():
    from traceguard import ondemand
    contact = ondemand.contact_line({"CONTACT_EMAIL": "dev@example.org"})
    text = ondemand.refusal_comment("Limit reached: at most 2 requests per account every 24 hours.", contact)
    assert "Contact the developer: dev@example.org" in text and "project of your own" in text
    assert ondemand.contact_line({"SITE_URL": "https://x.test/tw/"}).endswith("https://x.test/tw/contact.html")
    assert ondemand.contact_line({}) == ""
    report = {"site": {"url": "https://www.example-news.com/a"}, "measurement": {"vantage": "github-actions-us", "passes": 2, "observe_seconds": 10.0},
              "summary": {"status": "ok", "confidence": "high", "passes_ok": 2, "passes_total": 2,
                          "metrics": {"tracking_services": 3, "third_party_domains": 5, "third_party_requests": 9,
                                      "third_party_cookies": 1, "cookies_total": 4},
                          "services": [{"service": "ads.example.com", "entity": "Ad <b>Co</b>", "category": "advertising",
                                        "tracking": True, "stable": True},
                                       {"service": "cdn.example.com", "entity": "CDN", "category": "cdn", "tracking": False, "stable": True}]}}
    block = ondemand.result_block(report, "2026-10-12 08:00 UTC")
    assert "-->" in block and block.count("-->") == 1
    decoded = ondemand.decode_result_block("Analysis...\n" + block)
    assert decoded["host"] == "www.example-news.com" and decoded["metrics"]["tracking_services"] == 3
    assert [s["service"] for s in decoded["services"]] == ["ads.example.com"]
    assert "<" not in decoded["services"][0]["company"] and ondemand.decode_result_block("no block here") is None


def test_the_try_it_page_shows_results_on_its_own_page_with_a_privacy_note_and_a_contact_for_more(site):
    out, _ = site
    page = (out / "request.html").read_text(encoding="utf-8")
    assert 'id="result-form"' in page and "assets/result.js" in page and "GitHub sees your IP address" in page
    js = (out / "assets" / "result.js").read_text(encoding="utf-8")
    assert "github-actions[bot]" in js and "tw-result" in js and "Download JSON" in js and "Download CSV" in js
    assert "innerHTML" not in js  # everything from GitHub is shown as text, never as HTML


SHORTLINK_LIMITS = {**DEFAULTS, "links": {**DEFAULTS["links"], "per_account": 1, "window_hours": 168, "global_per_day": 4}}


def test_shortlink_limits_one_per_account_per_week_and_four_a_day_for_everyone(tmp_path):
    first = request(tmp_path, limits=SHORTLINK_LIMITS, history=[hist("ana", 1)])
    assert first["created"]
    second = request(tmp_path, BODY.format(code="other"), limits=SHORTLINK_LIMITS, history=[hist("ana", 30), hist("ana", 1)])
    assert not second["created"] and second["reason"] == "limit"
    assert "at most 1 requests per account every 7 days" in second["comment"]
    other_user = request(tmp_path, BODY.format(code="third"), author="ben", limits=SHORTLINK_LIMITS,
                         history=[hist("ana", 30), hist("ben", 1)])
    assert other_user["created"]  # a different account is not held back by ana's request
    crowded = request(tmp_path, BODY.format(code="fifth"), author="eva", limits=SHORTLINK_LIMITS,
                      history=[hist(name, i + 1) for i, name in enumerate(("a1", "a2", "a3", "a4", "eva"))])
    assert not crowded["created"] and "daily capacity" in crowded["comment"]


def test_shortlink_refusals_for_limits_point_to_the_developer_for_custom_links(tmp_path):
    from traceguard import links as links_module
    from traceguard.limits import span
    assert (span(24), span(48), span(168), span(5)) == ("24 hours", "2 days", "7 days", "5 hours")
    refused = process_request(BODY.format(code="x"), "ana", OLD, [hist("ana", 1), hist("ana", 2)], SHORTLINK_LIMITS,
                              tmp_path / "links.json", "https://o.github.io/tw", now=NOW, blocklist=BLOCK, resolver=PUBLIC,
                              scan=None, env={"CONTACT_EMAIL": "dev@example.org"})
    assert "Want a custom short link" in refused["comment"] and "Contact the developer: dev@example.org" in refused["comment"]
    blocked = process_request(BODY.format(code="x"), "ana", OLD, [hist("ana", 1)], {**SHORTLINK_LIMITS, "blocked_accounts": ["ana"]},
                              tmp_path / "links.json", "https://o.github.io/tw", now=NOW, blocklist=BLOCK, resolver=PUBLIC,
                              scan=None, env={"CONTACT_EMAIL": "dev@example.org"})
    assert "Contact the developer" not in blocked["comment"]  # a blocked account is not invited to ask for more
    assert links_module.contact_line({}) == ""


def test_the_try_it_page_tells_people_to_contact_the_developer_for_a_custom_short_link(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report("News A", "https://a.example/", vantage="github-actions-us")})
    (tmp_path / "limits.json").write_text(json.dumps({"links": {"per_account": 1, "window_hours": 168, "global_per_day": 4}}),
                                          encoding="utf-8")
    build_site(tmp_path / "runs", tmp_path / "site", repo_url="https://github.com/o/r", contact_email="dev@example.org")
    page = (tmp_path / "site" / "request.html").read_text(encoding="utf-8")
    assert "1 per GitHub account every 7 days; 4 per day for the whole site, whoever asks" in page
    assert "Want a custom short link" in page and 'href="mailto:dev@example.org"' in page
