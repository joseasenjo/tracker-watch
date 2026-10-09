"""Packaging of the extension for a GitHub Release (extension/tools/package.py)."""
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("package_tool", ROOT / "extension" / "tools" / "package.py")
package_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_tool)


def fake_dist(tmp_path, name="Tracker Watch Lens"):
    for browser in ("chrome", "firefox"):
        folder = tmp_path / "dist" / browser
        (folder / "core").mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps({"name": name, "version": "0.1.0"}), encoding="utf-8")
        (folder / "background.js").write_text("// " + browser, encoding="utf-8")
        (folder / "core" / "a.js").write_text("export const a = 1;", encoding="utf-8")
        (folder / "_metadata").mkdir()
        (folder / "_metadata" / "verified_contents.json").write_text("{}", encoding="utf-8")
    return tmp_path / "dist"


def test_zips_hold_the_extension_at_the_root_without_browser_made_folders_and_sums_match(tmp_path):
    dist = fake_dist(tmp_path)
    written = package_tool.package(dist, tmp_path / "out")
    assert [p.name for p in written] == ["tracker-watch-lens-chrome.zip", "tracker-watch-lens-firefox.zip"]
    with zipfile.ZipFile(written[0]) as z:
        names = z.namelist()
        assert names[0] == "manifest.json" and "core/a.js" in names and "background.js" in names
        assert not any(n.startswith("_metadata") for n in names)
        assert z.read("background.js") == b"// chrome"
    sums = (tmp_path / "out" / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    for line in sums:
        digest, name = line.split("  ")
        assert hashlib.sha256((tmp_path / "out" / name).read_bytes()).hexdigest() == digest
    assert len(sums) == 2


def test_the_same_input_gives_the_same_bytes_and_test_builds_are_refused(tmp_path):
    dist = fake_dist(tmp_path)
    first = [p.read_bytes() for p in package_tool.package(dist, tmp_path / "one")]
    second = [p.read_bytes() for p in package_tool.package(dist, tmp_path / "two")]
    assert first == second
    bad = fake_dist(tmp_path / "bad", "Tracker Watch Lens (test build)")
    with pytest.raises(SystemExit):
        package_tool.package(bad, tmp_path / "bad_out")
    with pytest.raises(SystemExit):
        package_tool.package(tmp_path / "missing", tmp_path / "x")


def test_the_site_offers_the_release_files_install_steps_and_the_extension_privacy_policy(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    from test_site import ok_report, write
    from traceguard.site import build_site
    write(tmp_path, "2026-10-04", {"a": ok_report("News A", "https://a.example/", vantage="github-actions-us")})
    build_site(tmp_path / "runs", tmp_path / "site", repo_url="https://github.com/o/r", contact_email="dev@example.org")
    page = (tmp_path / "site" / "extension.html").read_text(encoding="utf-8")
    assert "https://github.com/o/r/releases/latest/download/tracker-watch-lens-chrome.zip" in page
    assert "https://github.com/o/r/releases/latest/download/tracker-watch-lens-firefox.zip" in page
    assert "Load unpacked" in page and "Load Temporary Add-on" in page and "Not in the browser stores yet" in page and "Chrome Web Store version will be available shortly" in page
    assert "extension-privacy.html" in page and "SHA-256" in page and "Not complete protection" in page
    policy = (tmp_path / "site" / "extension-privacy.html").read_text(encoding="utf-8")
    assert "sends nothing anywhere" in policy and "<script" not in policy.split("</nav>")[1].split("<footer")[0]
    home = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'href="extension.html"' in home
    bare = tmp_path / "bare"
    write(bare, "2026-10-04", {"a": ok_report("News A", "https://a.example/", vantage="github-actions-us")})
    build_site(bare / "runs", bare / "site")
    assert "releases/latest" not in (bare / "site" / "extension.html").read_text(encoding="utf-8")
