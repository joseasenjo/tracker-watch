"""The split of the tests between "stops the scan", "stops the posting" and "only warns" is real and cannot rot."""
import pathlib
import subprocess
import sys

import conftest

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent


def test_every_name_in_the_lists_is_a_real_test_file():
    names = {p.stem for p in HERE.glob("test_*.py")}
    assert conftest.ENGINE <= names and conftest.PUBLISHING <= names
    assert not conftest.ENGINE & conftest.PUBLISHING


def collected(marker_expression):
    done = subprocess.run([sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-m", marker_expression],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    return {line.split("::")[0] for line in done.stdout.splitlines() if "::" in line}


def test_the_markers_select_exactly_the_listed_files():
    assert {pathlib.Path(f).stem for f in collected("engine")} == conftest.ENGINE
    assert {pathlib.Path(f).stem for f in collected("publishing")} == conftest.PUBLISHING
    rest = {pathlib.Path(f).stem for f in collected("not engine")}
    assert not rest & conftest.ENGINE and "test_ops" in rest and "test_site" in rest


def test_the_weekly_scan_stops_only_for_the_engine_and_warns_for_the_rest():
    import yaml
    steps = yaml.safe_load((ROOT / ".github/workflows/weekly-scan.yml").read_text(encoding="utf-8"))["jobs"]["scan"]["steps"]
    runs = [s.get("run", "") for s in steps]
    assert any("pytest tests -q -m engine" in r for r in runs)
    other = next(s for s in steps if "-m \"not engine\"" in s.get("run", ""))
    assert other.get("continue-on-error") is True and other.get("id") == "othertests"
    warn = next(s for s in steps if "notify failure" in s.get("run", ""))
    assert "steps.othertests.outcome == 'failure'" in warn["if"]
    assert not any(r.strip() == "python -m pytest tests -q" for r in runs)  # no step left that stops on everything
    post = yaml.safe_load((ROOT / ".github/workflows/weekly-scan.yml").read_text(encoding="utf-8"))["jobs"]["post"]["steps"]
    assert any("pytest tests -q -m publishing" in s.get("run", "") for s in post)


def test_a_post_that_does_not_go_out_always_emails_why():
    import yaml
    for name in ("weekly-scan", "post-midweek"):
        jobs = yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text(encoding="utf-8"))["jobs"]
        steps = jobs["post"]["steps"]
        notices = [s for s in steps if s.get("run", "").strip() == "python -m traceguard.notify not-published"]
        conditions = " ".join(s["if"] for s in notices)
        assert "POST_REVIEW == 'true'" in conditions and "MASTODON_ENABLED != 'true'" in conditions  # review mode, Mastodon off
        off = jobs["post-off"]
        assert "POST_ENABLED" in off["if"] and any(s.get("run", "").strip() == "python -m traceguard.notify not-published" for s in off["steps"])
