import json

import pytest

from traceguard.cards import origins_alt_text, origins_card_html, origins_rows
from traceguard.origins import compare, load_extra, paired_bars
from traceguard.site import build_context, build_site

from test_site import blocked_report, ok_report, write

LABELS = {"github-actions-us": "GitHub servers in the US", "local-windows-spain": "a PC in Spain"}


def with_consent(report, seen):
    services = report["summary"]["services"]
    if seen:
        services.append({"service": "cmp.example", "entity": "CMP", "category": "consent_management",
                         "tracking": False, "passes_seen": 3, "stable": True})
    return report


def extra_write(tmp_path, vantage, day, reports):
    folder = tmp_path / "extra" / vantage / day
    folder.mkdir(parents=True, exist_ok=True)
    for stem, report in reports.items():
        (folder / f"{stem}.json").write_text(json.dumps(report), encoding="utf-8")


@pytest.fixture
def two_origins(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report("News A", "https://a.example/", tracking=19, vantage="github-actions-us"),
                                   "b": ok_report("News B", "https://b.example/", tracking=2, vantage="github-actions-us"),
                                   "c": blocked_report("News C", "https://c.example/")})
    extra_write(tmp_path, "local-windows-spain", "2026-10-04",
                {"a": with_consent(ok_report("News A", "https://a.example/", tracking=0, vantage="local-windows-spain"), True),
                 "b": ok_report("News B", "https://b.example/", tracking=2, vantage="local-windows-spain")})
    return tmp_path


def test_load_extra_reads_vantage_date_stem_layout(two_origins):
    extra = load_extra(two_origins / "extra")
    assert list(extra) == ["local-windows-spain"] and list(extra["local-windows-spain"]) == ["2026-10-04"]
    assert set(extra["local-windows-spain"]["2026-10-04"]) == {"a", "b"}
    assert load_extra(two_origins / "missing") == {}


def test_compare_gaps_are_sorted_and_consent_tools_are_reported(two_origins):
    ctx = build_context(two_origins / "runs")
    o = ctx["origins"]
    assert o["has_data"] and o["origins"] == ["GitHub servers in the US", "a PC in Spain"]
    assert [r["name"] for r in o["rows"]][:2] == ["News A", "News B"] and o["rows"][0]["gap"] == 19
    a = o["rows"][0]["cells"]
    assert a["a PC in Spain"]["consent"] is True and a["GitHub servers in the US"]["consent"] is False
    assert o["compared"] == 2 and o["differ"] == 1 and o["max_gap"] == 19 and o["higher_in_first"] == 1
    c = next(r for r in o["rows"] if r["name"] == "News C")
    assert c["gap"] is None and c["cells"]["GitHub servers in the US"]["status"] == "blocked"
    assert c["cells"]["a PC in Spain"]["status"] == "none"


def test_extra_dates_too_far_from_the_main_one_are_ignored():
    primary = {"a": ok_report("News A", "https://a.example/", tracking=5, vantage="github-actions-us")}
    extra = {"local-windows-spain": {"2026-08-01": {"a": ok_report("News A", "https://a.example/", tracking=1)}}}
    result = compare("2026-10-04", primary, "US", extra, LABELS)
    assert result["rows"][0]["gap"] is None and not result["has_data"]


def test_closest_extra_date_within_a_week_is_used():
    primary = {"a": ok_report("News A", "https://a.example/", tracking=5, vantage="github-actions-us")}
    extra = {"local-windows-spain": {"2026-09-30": {"a": ok_report("News A", "https://a.example/", tracking=1)},
                                     "2026-10-03": {"a": ok_report("News A", "https://a.example/", tracking=3)}}}
    result = compare("2026-10-04", primary, "US", extra, LABELS)
    assert result["rows"][0]["cells"]["a PC in Spain"]["date"] == "2026-10-03" and result["rows"][0]["gap"] == 2


def test_the_primary_origin_is_not_repeated_as_an_extra_one(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report("News A", "https://a.example/", tracking=5, vantage="github-actions-us")})
    extra_write(tmp_path, "github-actions-us", "2026-10-04", {"a": ok_report("News A", "https://a.example/", tracking=9, vantage="github-actions-us")})
    assert build_context(tmp_path / "runs")["origins"]["has_data"] is False


def test_paired_bars_svg_is_accessible_and_empty_when_there_is_nothing_to_compare(two_origins):
    ctx = build_context(two_origins / "runs")
    svg = str(ctx["origins_chart"])
    assert 'role="img"' in svg and "News A" in svg and svg.count("<rect") == 4
    assert str(paired_bars([], ["x", "y"])) == ""


def test_origins_page_and_navigation(two_origins):
    out = two_origins / "site"
    build_site(two_origins / "runs", out)
    page = (out / "origins.html").read_text(encoding="utf-8")
    assert "Same site, different countries" in page and "Consent tool" in page
    assert "does not prove that is the cause" in page and "A gap is not a verdict" in page
    assert "“not seen” does not mean “absent”" in page
    assert 'href="origins.html"' in (out / "index.html").read_text(encoding="utf-8")
    assert "See the same sites measured from GitHub servers in the US and a PC in Spain" in (out / "index.html").read_text(encoding="utf-8")


def test_origins_page_without_a_second_origin_says_so(tmp_path):
    write(tmp_path, "2026-10-04", {"a": ok_report("News A", "https://a.example/", vantage="github-actions-us")})
    out = tmp_path / "site"
    build_site(tmp_path / "runs", out)
    assert "nothing to compare yet" in (out / "origins.html").read_text(encoding="utf-8")


def test_origins_card_has_scaled_pairs_and_no_external_requests(two_origins):
    ctx = build_context(two_origins / "runs")
    rows = origins_rows(ctx)
    assert rows[0]["a"] == 19 and rows[0]["b"] == 0 and rows[0]["wa"] == 100
    html = origins_card_html(ctx)
    assert "News A" in html and "counts, not verdicts" in html and "http://" not in html and "https://" not in html
    assert "News A: GitHub servers in the US 19, a PC in Spain 0" in origins_alt_text(ctx)


def test_origins_og_image_only_when_the_card_was_rendered(two_origins, monkeypatch):
    from traceguard import site as site_module

    def fake(html, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return True

    monkeypatch.setattr(site_module, "render_png", fake)
    out = two_origins / "site"
    build_site(two_origins / "runs", out, base_url="https://example.org/tw", share_cards=True)
    assert 'content="https://example.org/tw/share/origins.png"' in (out / "origins.html").read_text(encoding="utf-8")
    assert (out / "share" / "origins.png").exists()
