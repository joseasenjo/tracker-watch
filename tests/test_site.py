import json
import re
import xml.etree.ElementTree as ET

import pytest

from traceguard.site import build_context, build_dashboard, build_site


def request(host, domain, party, tracker=None):
    return {"t_ms": 5, "method": "GET", "resource_type": "script", "url": f"https://{host}/x", "host": host,
            "domain": domain, "party": party, "tracker": tracker}


ADS = {"service": "doubleclick.net", "entity": "Google", "category": "advertising"}


def ok_report(name="News A", url="https://a.example/", tracking=1, vantage="local-windows-spain"):
    reqs = [request("a.example", "a.example", "first"), request("stats.g.doubleclick.net", "doubleclick.net", "third", ADS),
            request("cdn.unknown.example", "unknown.example", "third")]
    run = {"pass": 1, "status": "ok", "http_status": 200, "error": None, "requests": reqs, "cookies": [],
           "observations": [], "internal_requests_blocked": [], "navigation_redirects": [],
           "security_headers": {}, "storage_items": {"local": 0, "session": 0}, "final_url": url, "tls_protocol": "TLS 1.3"}
    return {
        "schema_version": "0.1", "tool": {"name": "traceguard", "version": "0.1.0"}, "generated_at": "2026-10-04T10:00:00+00:00",
        "site": {"name": name, "url": url, "first_party_domains": ["a.example"]},
        "measurement": {"vantage": vantage, "observe_seconds": 12.0, "passes": 3},
        "summary": {"status": "ok", "passes_ok": 3, "passes_total": 3, "confidence": "high", "failed_passes": {},
                    "metrics": {"third_party_requests": 2, "third_party_domains": 2, "tracking_services": tracking,
                                "third_party_cookies": 0, "cookies_total": 1, "storage_items": 0},
                    "third_party_domains": [{"domain": "doubleclick.net", "passes_seen": 3, "stable": True},
                                            {"domain": "unknown.example", "passes_seen": 3, "stable": True},
                                            {"domain": "other.example", "passes_seen": 2, "stable": True}],
                    "services": [{"service": "doubleclick.net", "entity": "Google", "category": "advertising",
                                  "tracking": True, "passes_seen": 3, "stable": True}],
                    "observations": [{"kind": "canvas_read", "script_domain": "doubleclick.net", "party": "third",
                                      "passes_seen": 3, "stable": True}],
                    "navigation_redirects": [], "security_headers": {}, "internal_requests_blocked": 0},
        "findings": [{"code": "THIRD_PARTY_REQUESTS", "severity": "info", "text": "The page made 2 requests.", "evidence": {}}],
        "runs": [run],
    }


def blocked_report(name="News B", url="https://b.example/"):
    r = ok_report(name, url)
    r["summary"] = {"status": "blocked", "passes_ok": 0, "passes_total": 3, "http_status": 403, "error": None,
                    "final_url": url, "internal_requests_blocked": 0}
    r["findings"] = []
    r["runs"] = []
    return r


def write(tmp_path, day, reports):
    folder = tmp_path / "runs" / day
    folder.mkdir(parents=True, exist_ok=True)
    for stem, report in reports.items():
        (folder / f"{stem}.json").write_text(json.dumps(report), encoding="utf-8")


@pytest.fixture
def built(tmp_path):
    write(tmp_path, "2026-09-27", {"a": ok_report(tracking=1, vantage="github-actions-us")})
    write(tmp_path, "2026-10-04", {"a": ok_report(tracking=3, vantage="github-actions-us"), "b": blocked_report()})
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": [{"name": "News A", "url": "https://a.example/", "group": "US", "kind": "news"},
                                           {"name": "News B", "url": "https://b.example/", "group": "UK", "kind": "sports"}]}))
    out = tmp_path / "site"
    ctx = build_site(tmp_path / "runs", out, sites_file=str(sites), base_url="https://example.org/tw/")
    return tmp_path, out, ctx


def test_pages_are_written(built):
    _, out, _ = built
    for name in ("index.html", "changes.html", "unmeasured.html", "method.html", "feed.xml",
                 "sites/a.html", "sites/b.html", "assets/style.css", "assets/site.js", "data/2026-10-04/a.json"):
        assert (out / name).exists(), name


def test_index_lists_measured_sites_and_counts(built):
    _, out, ctx = built
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "News A" in html and "News B" not in html.split("<tbody>")[1].split("</tbody>")[0]
    assert ctx["with_tracking"] == 1 and len(ctx["unmeasured"]) == 1
    assert 'data-group="US"' in html


def test_unmeasured_page_explains_why(built):
    _, out, _ = built
    html = (out / "unmeasured.html").read_text(encoding="utf-8")
    assert "News B" in html and "refused the automated browser (HTTP 403)" in html


def test_site_page_shows_comparison_domains_and_history(built):
    _, out, _ = built
    html = (out / "sites" / "a.html").read_text(encoding="utf-8")
    assert "Comparison with last week" in html
    assert "Unclassified" in html and "Google" in html
    assert "2026-09-27" in html and "2026-10-04" in html


def test_delta_vs_last_week_is_computed_only_for_comparable_weeks(built):
    _, _, ctx = built
    entry = next(e for e in ctx["entries"] if e["stem"] == "a")
    assert entry["delta"] == 0 or entry["delta"] is not None


def test_bands_are_shown_and_explained_as_ranges_not_verdicts(built):
    _, out, ctx = built
    entry = next(e for e in ctx["entries"] if e["stem"] == "a")
    assert entry["band"] == "B" and next(e for e in ctx["entries"] if e["stem"] == "b")["band"] is None
    assert 'data-band="B"' in (out / "index.html").read_text(encoding="utf-8")
    method = (out / "method.html").read_text(encoding="utf-8")
    assert 'id="bands"' in method and "50+" in method and "not</b> a grade" in method
    assert "band B" in (out / "sites" / "a.html").read_text(encoding="utf-8")


def test_last_scan_time_and_measurement_details_are_shown(built):
    _, out, ctx = built
    assert ctx["scan_finished"] == "2026-10-04 10:00 UTC"
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Last scan: 2026-10-04 10:00 UTC" in html
    assert "How this measurement was made" in html and "measured from" in html
    assert "Last scan: <b>2026-10-04 10:00 UTC</b>" in (out / "method.html").read_text(encoding="utf-8")


def without_credit(html: str) -> str:
    """The page without the author's credit block, which is always published (the user's decision, 2026-10-07)."""
    return re.sub(r'<div class="credit">.*?</div>', "", html, flags=re.S)


def test_no_personal_contact_is_published_unless_given(built):
    _, out, _ = built
    footer = without_credit((out / "method.html").read_text(encoding="utf-8")).split("<footer>")[1]
    assert "mailto:" not in footer and "linkedin" not in footer.lower() and "Corrections, notes" not in footer


def test_contact_details_appear_in_footer_and_about_when_given(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report()})
    out = tmp_path / "site"
    build_site(tmp_path / "runs", out, contact_email="corrections@example.org",
               linkedin_url="https://www.linkedin.com/in/someone", author="Jane Doe",
               repo_url="https://github.com/x/y", next_scan="Mondays 05:00 UTC")
    footer = (out / "index.html").read_text(encoding="utf-8").split("<footer>")[1]
    assert 'mailto:corrections@example.org' in footer and "linkedin.com/in/someone" in footer
    assert "https://github.com/x/y/issues" in footer and "next scan: Mondays 05:00 UTC" in footer
    about = (out / "about.html").read_text(encoding="utf-8")
    assert "Jane Doe" in about and "Corrections and right of reply" in about


@pytest.mark.parametrize("kwargs", [{"contact_email": "not-an-email"},
                                    {"linkedin_url": "https://evil.example/in/x"},
                                    {"linkedin_url": "http://www.linkedin.com/in/x"}])
def test_invalid_contact_details_are_rejected(tmp_path, kwargs):
    write(tmp_path, "2026-10-04", {"a": ok_report()})
    with pytest.raises(ValueError):
        build_site(tmp_path / "runs", tmp_path / "site", **kwargs)


def test_contact_values_are_html_escaped(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report()})
    out = tmp_path / "site"
    build_site(tmp_path / "runs", out, author="<b>x</b>")
    assert "<b>x</b>" not in (out / "about.html").read_text(encoding="utf-8").split("Maintained by")[1][:40]


def test_csv_export_has_header_rows_and_blank_figures_for_unmeasured_sites(built):
    import csv as csvmod
    _, out, _ = built
    rows = list(csvmod.DictReader((out / "data" / "latest.csv").open(encoding="utf-8")))
    by_name = {r["site"]: r for r in rows}
    assert by_name["News A"]["tracking_services"] == "3" and by_name["News A"]["band"] == "B"
    assert by_name["News B"]["status"] == "blocked" and by_name["News B"]["tracking_services"] == ""


def test_contact_page_lists_only_the_channels_that_are_configured(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report()})
    bare, full = tmp_path / "bare", tmp_path / "full"
    build_site(tmp_path / "runs", bare)
    build_site(tmp_path / "runs", full, repo_url="https://github.com/x/y", contact_email="c@example.org",
               linkedin_url="https://www.linkedin.com/in/someone")
    assert "Contact details will be added soon" in (bare / "contact.html").read_text(encoding="utf-8")
    page = (full / "contact.html").read_text(encoding="utf-8")
    assert "https://github.com/x/y/issues/new/choose" in page and "mailto:c@example.org" in page
    assert "linkedin.com/in/someone" in page and "Contact details will be added soon" not in page
    assert 'href="contact.html"' in (full / "index.html").read_text(encoding="utf-8")


def test_linkedin_only_is_enough_to_show_a_contact_card(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report()})
    out = tmp_path / "site"
    build_site(tmp_path / "runs", out, linkedin_url="https://www.linkedin.com/in/someone")
    page = without_credit((out / "contact.html").read_text(encoding="utf-8"))
    assert "linkedin.com/in/someone" in page and "mailto:" not in page


def test_every_page_ends_with_the_author_credit(built):
    _, out, _ = built
    pages = list(out.glob("*.html")) + list((out / "sites").glob("*.html"))
    assert len(pages) > 15
    for page in pages:
        footer = page.read_text(encoding="utf-8").split("<footer>")[1]
        credit = footer.split('<div class="credit">')[1]
        assert "Designed by jlasenjo" in credit and 'href="mailto:asenjo.jose@hotmail.com"' in credit, page.name


def test_issue_forms_live_in_the_repository_not_in_the_generated_site(built):
    from pathlib import Path
    _, out, _ = built
    assert not (out / ".github").exists()
    folder = Path(__file__).resolve().parent.parent / ".github" / "ISSUE_TEMPLATE"
    assert {p.name for p in folder.iterdir()} >= {"correction.yml", "add-site.yml", "link-request.yml", "config.yml"}
    assert "Issues are public" in (folder / "correction.yml").read_text(encoding="utf-8")


def test_about_page_states_independence_and_corrections(built):
    _, out, _ = built
    about = (out / "about.html").read_text(encoding="utf-8")
    assert "Independence" in about and "no affiliate links" in about and "Changes to the method" in about


def test_vantage_caveat_is_shown_on_the_index_and_on_non_us_site_pages(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report("News A", "https://a.example/"), "u": ok_report("US News", "https://u.example/")})
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": [{"name": "News A", "url": "https://a.example/", "group": "UK", "kind": "news"},
                                           {"name": "US News", "url": "https://u.example/", "group": "US", "kind": "news"}]}))
    out = tmp_path / "site"
    build_site(tmp_path / "runs", out, sites_file=str(sites))
    assert "Where the page is loaded from matters" in (out / "index.html").read_text(encoding="utf-8")
    assert "mainly serves readers in the UK" in (out / "sites" / "a.html").read_text(encoding="utf-8")
    assert "mainly serves readers" not in (out / "sites" / "u.html").read_text(encoding="utf-8")


def test_site_makes_no_external_requests(built):
    _, out, _ = built
    for page in list(out.rglob("*.html")):
        html = page.read_text(encoding="utf-8")
        assert not re.search(r'<(script|link|img|iframe)[^>]+(src|href)="https?://', html), page
        assert "fonts.googleapis" not in html
    assert "https://" not in (out / "assets" / "style.css").read_text(encoding="utf-8")


def test_html_in_data_is_escaped(tmp_path):
    report = ok_report(name="<script>alert(1)</script>")
    write(tmp_path, "2026-10-04", {"x": report})
    out = tmp_path / "site"
    build_site(tmp_path / "runs", out)
    html = (out / "index.html").read_text(encoding="utf-8") + (out / "sites" / "x.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_feed_is_valid_xml_with_the_latest_week(built):
    _, out, _ = built
    root = ET.fromstring((out / "feed.xml").read_text(encoding="utf-8"))
    titles = [t.text for t in root.iter("title")]
    assert "Weekly measurement 2026-10-04" in titles and "Weekly measurement 2026-09-27" in titles


def test_method_page_lists_the_classification_list_and_limits(built):
    _, out, _ = built
    html = (out / "method.html").read_text(encoding="utf-8")
    assert "doubleclick.net" in html and "before any interaction" in html and "Limits" in html


def test_dashboard_is_spanish_local_and_separate(built):
    tmp_path, out, ctx = built
    dash = tmp_path / "dash"
    build_dashboard(ctx, dash, drafts_dir=tmp_path / "nodrafts")
    html = (dash / "index.html").read_text(encoding="utf-8")
    assert "Panel interno" in html and "Estado por web" in html and 'name="robots" content="noindex"' in html
    assert "La web rechazó al navegador automático" in html
    assert not (out / "index.html").read_text(encoding="utf-8").count("Panel interno")


def test_subscribe_button_is_disabled_without_a_url_and_active_with_one(built, tmp_path):
    _, out, _ = built
    assert "Coming soon" in (out / "index.html").read_text(encoding="utf-8")
    out2 = tmp_path / "site2"
    build_site(tmp_path / "runs", out2, subscribe_url="https://newsletter.example/signup", repo_url="https://github.com/x/y")
    html = (out2 / "index.html").read_text(encoding="utf-8")
    assert 'href="https://newsletter.example/signup"' in html and "Coming soon" not in html
    assert "newsletter.example" in (out2 / "privacy.html").read_text(encoding="utf-8")
    assert "https://github.com/x/y" in (out2 / "privacy.html").read_text(encoding="utf-8")


def test_subscribe_url_must_be_https(built, tmp_path):
    with pytest.raises(ValueError):
        build_site(tmp_path / "runs", tmp_path / "site3", subscribe_url="http://newsletter.example/signup")
    with pytest.raises(ValueError):
        build_site(tmp_path / "runs", tmp_path / "site3", subscribe_url="javascript:alert(1)")


def test_privacy_page_exists_and_makes_no_external_requests(built):
    _, out, _ = built
    html = (out / "privacy.html").read_text(encoding="utf-8")
    assert "sets no cookies" in html
    assert not re.search(r'<(script|link|img|iframe)[^>]+(src|href)="https?://', html)


def test_empty_runs_dir_is_an_error(tmp_path):
    (tmp_path / "runs").mkdir()
    with pytest.raises(FileNotFoundError):
        build_context(tmp_path / "runs")
