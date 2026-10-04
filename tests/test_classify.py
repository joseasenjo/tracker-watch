from traceguard.classify import Classifier, TrackerList, registrable_domain


def test_registrable_domain_uses_public_suffix_list_offline():
    assert registrable_domain("pixelcounter.marca.com") == "marca.com"
    assert registrable_domain("e00.uecdn.es") == "uecdn.es"
    assert registrable_domain("news.bbc.co.uk") == "bbc.co.uk"
    assert registrable_domain("www.theguardian.com") == "theguardian.com"
    assert registrable_domain("93.184.216.34") == "93.184.216.34"
    assert registrable_domain("") == ""


def test_tracker_lookup_longest_suffix_wins():
    trackers = TrackerList.load()
    assert trackers.lookup("stats.g.doubleclick.net")["entity"] == "Google"
    assert trackers.lookup("region1.analytics.google.com")["service"] == "analytics.google.com"
    assert trackers.lookup("connect.facebook.net")["category"] == "social"
    assert trackers.lookup("www.google.com") is None
    assert trackers.lookup("example.com") is None


def test_tracking_categories_exclude_tag_managers_and_consent_tools():
    trackers = TrackerList.load()
    assert trackers.is_tracking("advertising")
    assert trackers.is_tracking("session_replay")
    assert not trackers.is_tracking("tag_manager")
    assert not trackers.is_tracking("consent_management")


def test_first_party_includes_own_subdomains_and_cdn_domains():
    classifier = Classifier({"marca.com", "uecdn.es"}, TrackerList.load())
    assert classifier.party("pixelcounter.marca.com") == "first"
    assert classifier.party("e00-marca.uecdn.es") == "first"
    assert classifier.party("sb.scorecardresearch.com") == "third"
