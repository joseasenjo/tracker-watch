"""Which tests may stop the weekly scan.

The scan used to stop on ANY failing test, even one about the dashboard or the extension, and a week of data was lost
(2026-10-19). Now the tests are split by what they protect:

  engine      measuring, classifying and reporting: a failure here stops the scan (wrong numbers would be published);
  publishing  writing and sending the posts: a failure here stops the posting job (a bug could duplicate or misplace a post);
  the rest    the dashboard, the e-mail tools, the extension, the site pages ...: a failure warns by e-mail, nothing stops.

A file is marked by its name below; every file not listed here is in "the rest". tests/test_ci_split.py makes sure the
names are real, so a typo cannot silently drop a protection.
"""
import pytest

ENGINE = {"test_bands", "test_behaviour", "test_classify", "test_consent_blocker", "test_diff", "test_origins",
          "test_reclassify", "test_report", "test_safety", "test_sites_list"}
PUBLISHING = {"test_publish", "test_posts", "test_notify"}


def pytest_configure(config):
    config.addinivalue_line("markers", "engine: measurement, classification and reporting; a failure stops the weekly scan")
    config.addinivalue_line("markers", "publishing: posting code; a failure stops the posting job")


def pytest_collection_modifyitems(items):
    for item in items:
        module = item.module.__name__.rpartition(".")[2]
        if module in ENGINE:
            item.add_marker(pytest.mark.engine)
        if module in PUBLISHING:
            item.add_marker(pytest.mark.publishing)
