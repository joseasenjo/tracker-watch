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


def test_the_alert_and_the_hook_post_on_their_own_only_when_the_switch_is_on_and_a_failure_warns():
    import yaml
    from traceguard import ops
    assert "ALERTS_AUTO" in ops.SWITCHES
    steps = yaml.safe_load((ROOT / ".github/workflows/weekly-scan.yml").read_text(encoding="utf-8"))["jobs"]["post"]["steps"]
    auto = next(s for s in steps if s.get("id") == "alerts")
    assert "vars.ALERTS_AUTO == 'true'" in auto["if"] and "vars.POST_REVIEW != 'true'" in auto["if"]
    assert auto.get("continue-on-error") is True  # never undoes or fails the weekly thread
    assert "--skip-if-published" in auto["run"] and "alert hook" in auto["run"]
    warn = next(s for s in steps if s.get("name", "").startswith("Warn by email about a step"))
    assert "steps.alerts.outcome == 'failure'" in warn["if"]
    assert steps.index(auto) > [s.get("id") for s in steps].index("mastodon")  # after the weekly thread


def test_every_github_action_is_pinned_to_a_commit_with_its_version_in_a_comment():
    import re
    pinned = re.compile(r"^\s*-?\s*uses:\s*[\w./-]+@[0-9a-f]{40}\s+#\s+v\d+\.\d+\.\d+\s*$")
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"^\s*-?\s*uses:", line):
                assert pinned.match(line), f"{path.name}:{number} is not pinned by commit: {line.strip()}"
    assert (ROOT / ".github/dependabot.yml").exists()  # something tells you when a pinned action has a new release


def test_a_test_run_posts_nothing_but_a_scheduled_run_still_posts():
    import yaml
    for name in ("weekly-scan", "post-midweek"):
        text = (ROOT / f".github/workflows/{name}.yml").read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        inputs = (data.get("on") or data.get(True))["workflow_dispatch"]["inputs"]
        assert inputs["publish"]["default"] == "true"  # the schedule, which has no inputs, treats an empty value as "post"
        for step in data["jobs"]["post"]["steps"]:
            condition = step.get("if", "")
            if step.get("run", "").startswith("python -m traceguard.publish") or " --send" in step.get("run", ""):
                assert "github.event.inputs.publish != 'false'" in condition, step.get("name")
