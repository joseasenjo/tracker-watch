import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from traceguard import links as links_module
from traceguard.links import (LinkError, add_link, load_blocklist, load_links, parse_request, pending_requests,
                              remove_link, summarize_check, validate_code, validate_target)
from traceguard.site import build_dashboard, build_site

from test_site import ok_report, write

PUBLIC = lambda host: ["93.184.216.34"]
PRIVATE = lambda host: ["10.0.0.5"]
BLOCK = {"bit.ly", "tinyurl.com"}


@pytest.mark.parametrize("code", ["abc", "my-link-1", "a" * 32, "2026-news"])
def test_valid_codes(code):
    assert validate_code(code) == code


@pytest.mark.parametrize("code", ["ab", "a" * 33, "-start", "UPPER_case", "has space", "admin", "go", "data", "sites", ""])
def test_invalid_or_reserved_codes(code):
    if code == "UPPER_case":
        with pytest.raises(LinkError):
            validate_code(code)
        return
    with pytest.raises(LinkError):
        validate_code(code)


def test_codes_are_lowercased():
    assert validate_code(" MyLink ") == "mylink"


@pytest.mark.parametrize("url", ["ftp://example.com/x", "javascript:alert(1)", "file:///etc/passwd",
                                 "http://user:pw@example.com/", "https://example.com:22/"])
def test_unsafe_targets_are_refused(url):
    with pytest.raises(LinkError):
        validate_target(url, BLOCK, PUBLIC)


def test_private_hosts_and_other_shorteners_are_refused():
    with pytest.raises(LinkError):
        validate_target("https://intranet.example/", BLOCK, PRIVATE)
    with pytest.raises(LinkError):
        validate_target("http://127.0.0.1/", BLOCK, PUBLIC)
    with pytest.raises(LinkError) as err:
        validate_target("https://bit.ly/abc", BLOCK, PUBLIC)
    assert "blocked domain" in str(err.value)
    with pytest.raises(LinkError):
        validate_target("https://sub.tinyurl.com/x", BLOCK, PUBLIC)
    assert validate_target("https://example.com/page", BLOCK, PUBLIC) == "https://example.com/page"


def test_overlong_addresses_are_refused():
    with pytest.raises(LinkError):
        validate_target("https://example.com/" + "a" * 2100, BLOCK, PUBLIC)


def test_add_list_remove_roundtrip_and_duplicates(tmp_path):
    path = tmp_path / "links.json"
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    record = add_link(path, "News-One", "https://example.com/a", note="  hello  ", blocklist=BLOCK, resolver=PUBLIC, now=now)
    assert record["code"] == "news-one" and record["host"] == "example.com" and record["created"] == "2026-10-05"
    assert record["note"] == "hello" and record["check"] is None
    add_link(path, "alpha", "https://example.org/", blocklist=BLOCK, resolver=PUBLIC)
    assert [l["code"] for l in load_links(path)] == ["alpha", "news-one"]
    with pytest.raises(LinkError):
        add_link(path, "alpha", "https://example.net/", blocklist=BLOCK, resolver=PUBLIC)
    assert remove_link(path, "ALPHA") is True and remove_link(path, "alpha") is False
    assert [l["code"] for l in load_links(path)] == ["news-one"]
    assert load_links(tmp_path / "missing.json") == []


def test_failed_additions_leave_the_file_untouched(tmp_path):
    path = tmp_path / "links.json"
    with pytest.raises(LinkError):
        add_link(path, "admin", "https://example.com/", blocklist=BLOCK, resolver=PUBLIC)
    with pytest.raises(LinkError):
        add_link(path, "okcode", "https://bit.ly/x", blocklist=BLOCK, resolver=PUBLIC)
    assert not path.exists()


def test_blocklist_file_ignores_comments_and_blank_lines(tmp_path):
    path = tmp_path / "block.txt"
    path.write_text("# comment\nBit.LY  # trailing\n\nexample.org\n", encoding="utf-8")
    assert load_blocklist(path) == {"bit.ly", "example.org"}
    assert load_blocklist(tmp_path / "none.txt") == set()


def test_shipped_blocklist_covers_common_shorteners():
    blocked = load_blocklist()
    assert {"bit.ly", "tinyurl.com", "t.co", "goo.gl"} <= blocked


def test_summarize_check_for_ok_and_blocked_reports():
    report = ok_report("News A", "https://a.example/", tracking=4)
    report["generated_at"] = "2026-10-05T10:00:00+00:00"
    summary = summarize_check(report)
    assert summary["status"] == "ok" and summary["tracking"] == 4 and summary["band"] == "B"
    assert summary["domains"] == [{"domain": "doubleclick.net", "entity": "Google", "category": "advertising"}]
    blocked = {"generated_at": "2026-10-05T10:00:00+00:00", "measurement": {"vantage": "v"},
               "summary": {"status": "blocked"}}
    assert summarize_check(blocked)["tracking"] is None


def test_parse_request_reads_the_issue_form_fields():
    issue = {"number": 7, "author": {"login": "ana"}, "createdAt": "2026-10-05T10:00:00Z",
             "body": "### Target address\n\nhttps://example.com/page\n\n### Preferred short code\n\nMy-Code extra\n\n### Why do you need it?\n\nFor my talk"}
    parsed = parse_request(issue)
    assert parsed == {"number": 7, "author": "ana", "created": "2026-10-05", "url": "https://example.com/page",
                      "code": "my-code", "reason": "For my talk"}
    empty = parse_request({"number": 8, "body": "### Target address\n\n_No response_"})
    assert empty["url"] == "" and empty["code"] == ""


def test_pending_requests_uses_the_cli_and_survives_its_absence():
    payload = json.dumps([{"number": 1, "author": {"login": "x"}, "createdAt": "2026-10-05T00:00:00Z",
                           "body": "### Target address\n\nhttps://example.com/"}])
    ok = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=payload)
    assert pending_requests("o/r", runner=ok)[0]["url"] == "https://example.com/"

    def missing(cmd, **kw):
        raise FileNotFoundError("gh")

    assert pending_requests("o/r", runner=missing) == []
    assert pending_requests("o/r", runner=lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="")) == []


@pytest.fixture
def site_with_links(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report("News A", "https://a.example/", vantage="github-actions-us")})
    links = tmp_path / "links.json"
    check = {"status": "ok", "tracking": 4, "band": "B", "confidence": "high", "vantage": "github-actions-us",
             "date": "2026-10-05", "domains": [{"domain": "doubleclick.net", "entity": "Google", "category": "advertising"}]}
    add_link(links, "good", "https://example.com/page?x=1&y=<2>", note="<b>note</b>", check=check, blocklist=BLOCK, resolver=PUBLIC)
    add_link(links, "plain", "https://example.org/", blocklist=BLOCK, resolver=PUBLIC)
    add_link(links, "failed", "https://example.net/", check={"status": "blocked", "tracking": None, "band": None, "domains": []},
             blocklist=BLOCK, resolver=PUBLIC)
    out = tmp_path / "site"
    build_site(tmp_path / "runs", out, links_file=links, repo_url="https://github.com/x/y")
    return tmp_path, out, links


def test_each_link_gets_a_static_interstitial_that_never_redirects_by_itself(site_with_links):
    _, out, _ = site_with_links
    page = (out / "go" / "good" / "index.html").read_text(encoding="utf-8")
    assert "example.com" in page and "Continue to example.com" in page and "We never redirect automatically" in page
    assert 'name="robots" content="noindex,nofollow"' in page
    assert 'rel="noopener noreferrer nofollow"' in page
    assert "http-equiv" not in page.lower() and "window.location" not in page and "location.href" not in page
    assert "contacted <b>4</b> third-party tracking services (band B)" in page and "doubleclick.net" in page


def test_interstitial_is_escaped_and_covers_unmeasured_and_failed_checks(site_with_links):
    _, out, _ = site_with_links
    page = (out / "go" / "good" / "index.html").read_text(encoding="utf-8")
    assert "<b>note</b>" not in page and "&lt;b&gt;note&lt;/b&gt;" in page
    assert "&lt;2&gt;" in page and "<2>" not in page
    assert "This address has not been analysed" in (out / "go" / "plain" / "index.html").read_text(encoding="utf-8")
    assert "We could not measure that page (blocked)" in (out / "go" / "failed" / "index.html").read_text(encoding="utf-8")


def test_short_links_page_lists_active_links_and_the_request_button(site_with_links):
    _, out, _ = site_with_links
    page = (out / "shortlinks.html").read_text(encoding="utf-8")
    assert "Request a short link" in page and "template=link-request.yml" in page
    assert 'href="go/good/"' in page and "3 active" in page
    assert "Short links" in (out / "index.html").read_text(encoding="utf-8")


def test_no_links_means_no_go_folder_and_no_listing(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report("News A", "https://a.example/", vantage="github-actions-us")})
    out = tmp_path / "site"
    build_site(tmp_path / "runs", out)
    assert not (out / "go").exists()
    assert "Approved links" not in (out / "shortlinks.html").read_text(encoding="utf-8")


def test_dashboard_shows_pending_requests_with_the_approval_command(site_with_links):
    tmp_path, out, links = site_with_links
    from traceguard.site import build_context
    ctx = build_context(tmp_path / "runs")
    ctx["links"] = load_links(links)
    pending = [{"number": 7, "author": "ana", "created": "2026-10-05", "url": "https://example.com/p", "code": "talk", "reason": "slides"},
               {"number": 8, "author": "bob", "created": "2026-10-05", "url": "", "code": "", "reason": ""}]
    build_dashboard(ctx, tmp_path / "dash", drafts_dir=tmp_path / "none", pending_links=pending)
    html = (tmp_path / "dash" / "index.html").read_text(encoding="utf-8")
    assert "python -m traceguard.links add talk https://example.com/p --scan" in html
    assert "Falta destino o código" in html and "Enlaces aprobados" in html
    build_dashboard(ctx, tmp_path / "dash2", drafts_dir=tmp_path / "none")
    assert "No hay peticiones pendientes" in (tmp_path / "dash2" / "index.html").read_text(encoding="utf-8")


def test_cli_add_list_remove_and_error_codes(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "l.json")
    monkeypatch.setattr(links_module, "system_resolver", PUBLIC)
    monkeypatch.setattr(links_module, "validate_target", lambda url, block, resolver=PUBLIC: url)
    assert links_module.main(["--file", path, "add", "mytalk", "https://example.com/t", "--note", "n"]) == 0
    assert links_module.main(["--file", path, "list"]) == 0
    assert "mytalk" in capsys.readouterr().out
    assert links_module.main(["--file", path, "add", "mytalk", "https://example.com/t"]) == 2
    assert links_module.main(["--file", path, "add", "admin", "https://example.com/t"]) == 2
    assert links_module.main(["--file", path, "remove", "mytalk"]) == 0
