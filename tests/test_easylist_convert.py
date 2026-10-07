"""EasyList conversion for the extension's ads mode (extension/tools/easylist.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extension" / "tools"))
from easylist import cosmetic, network_rule, network_rules  # noqa: E402


def test_path_rule_with_options():
    r = network_rule("/ads/banner.$script,third-party,domain=a.com|~b.a.com")
    assert r == {"action": {"type": "block"}, "condition": {
        "domainType": "thirdParty", "resourceTypes": ["script"], "initiatorDomains": ["a.com"],
        "excludedInitiatorDomains": ["b.a.com"], "urlFilter": "/ads/banner."}}


def test_exception_and_negated_types():
    r = network_rule("@@||cdn.example.com/ads.js$~image")
    assert r["action"]["type"] == "allow"
    assert r["condition"]["excludedResourceTypes"] == ["image", "main_frame"]


def test_skipped_rules():
    for line in ["/banner\\d+/", "||ads.example.com^$popup", "||x.com^$document", "||x.com^$csp=script-src",
                 "||x.com^$domain=google.*", "||ádx.com^", "$popup,domain=a.com"]:
        assert network_rule(line) is None, line


def test_host_rules_are_grouped_and_prioritised():
    rules, stats = network_rules("! comment\n||ads.one.com^\n||ads.two.com^\n||t.com^$third-party\n"
                                 "@@||ok.com^\n/adframe.\n##.ad\n")
    assert stats == {"lines": 5, "skipped": 0, "rules": 4, "host_domains": 4}
    block = [r for r in rules if r["condition"].get("requestDomains") == ["ads.one.com", "ads.two.com"]]
    assert block and block[0]["priority"] == 3
    assert [r["priority"] for r in rules if r["action"]["type"] == "allow"] == [4]
    assert len({r["id"] for r in rules}) == 4


def test_cosmetic_rules():
    text = """##.ad-banner
example.com,~shop.example.com##.promo
~quiet.org##.side-ad
shop.example.com#@#.ad-banner
example.org#?#.x:-abp-has(.y)
news.com##div:has-text(Sponsored)
@@||calm.net^$generichide
@@||none.net^$elemhide,document
google.*##.g-ad
"""
    c = cosmetic(text)
    assert c["generic"] == [".ad-banner", ".side-ad"]
    assert c["sites"] == {"example.com": [".promo"]}
    assert c["exceptions"] == {"quiet.org": [".side-ad"], "shop.example.com": [".ad-banner", ".promo"]}
    assert c["generichide"] == ["calm.net"] and c["elemhide"] == ["none.net"]
