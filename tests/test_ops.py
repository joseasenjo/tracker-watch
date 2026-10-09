"""Developer dashboard (traceguard.ops and the /ops routes of traceguard.admin): data, alerts and the few actions."""
import json
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from traceguard import ops
from traceguard.admin import make_server

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def report(name, tracking, *, status="ok", confidence="high"):
    summary = {"status": status, "confidence": confidence}
    if status == "ok":
        summary["metrics"] = {"tracking_services": tracking}
    return {"site": {"name": name, "url": f"https://{name.lower().replace(' ', '')}.example/"}, "summary": summary}


def write_day(runs, day, reports):
    folder = runs / day
    folder.mkdir(parents=True)
    for r in reports:
        (folder / (r["site"]["name"].replace(" ", "-") + ".json")).write_text(json.dumps(r), encoding="utf-8")


@pytest.fixture
def data(tmp_path):
    runs = tmp_path / "runs"
    write_day(runs, "2026-10-04", [report("News A", 50), report("News B", 5), report("News C", 9)])
    write_day(runs, "2026-10-09", [report("News A", 10), report("News B", 7), report("News C", 9, confidence="low"),
                                   report("News D", 0, status="blocked")])
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": [{"url": "https://newsa.example/", "group": "UK"},
                                           {"url": "https://newsb.example/", "group": "UK"},
                                           {"url": "https://newsc.example/", "group": "ES"},
                                           {"url": "https://newsd.example/", "group": "ES"}]}), encoding="utf-8")
    return SimpleNamespace(runs=runs, sites=sites, tmp=tmp_path)


def test_scan_summary_counts_unmeasured_low_confidence_swings_and_medians(data):
    s = ops.scan_summary(data.runs, data.sites)
    assert (s["date"], s["previous"]) == ("2026-10-09", "2026-10-04")
    assert (s["total"], s["measured"], s["usable"]) == (4, 3, 2)
    assert s["unmeasured"] == ["News D"] and s["low_confidence"] == ["News C"]
    assert s["medians"] == {"UK": {"median": 8, "sites": 2}}
    assert s["swings"] == [{"name": "News A", "before": 50, "after": 10, "delta": -40}]
    assert s["top"][0] == {"name": "News A", "n": 10}


def test_scan_summary_without_data_says_so(tmp_path):
    assert ops.scan_summary(tmp_path / "none") == {"available": False}


def state(data, **github):
    base = {"variables": {"POST_ENABLED": "true"}, "secrets": list(ops.SECRETS), "runs": [], "issues": []}
    base.update(github)
    return {"github": base, "scan": ops.scan_summary(data.runs, data.sites), "site": {"up": True, "status": 200},
            "bot": {"available": True, "bot_label": True}, "git": {"available": True, "behind": 0, "ahead": 0}, "drafts": []}


def test_alerts_flag_failed_runs_missing_secrets_swings_and_pending_review(data):
    ok = ops.build_alerts(state(data), NOW)
    assert not any("Falló" in a["text"] or "secretos" in a["text"] for a in ok)
    assert any("Solo 2 de 4" in a["text"] for a in ok)  # 50% measured well is under the 60% needed for the thread
    assert any("News A" in a["text"] and a["level"] == "media" for a in ok)
    bad = state(data, runs=[{"workflow": "weekly-scan", "conclusion": "failure", "created": "2026-10-09T10:00:00Z", "url": "u"}],
                secrets=["BLUESKY_HANDLE"], variables={"POST_ENABLED": "true", "POST_REVIEW": "true"})
    bad["drafts"] = [{"posted": False, "stale": False}]
    bad["site"] = {"up": False, "status": 404}
    texts = " | ".join(a["text"] for a in ops.build_alerts(bad, NOW) if a["level"] == "alta")
    assert "Falló" in texts and "BLUESKY_APP_PASSWORD" in texts and "no responde" in texts and "esperan tu aprobación" in texts
    assert ops.build_alerts(bad, NOW)[0]["level"] == "alta"


def test_only_the_latest_run_of_each_workflow_can_raise_a_failure_alert(data):
    old_failure = {"workflow": "weekly-scan", "conclusion": "failure", "created": "2026-10-09T10:00:00Z", "url": "u"}
    newer_success = {"workflow": "weekly-scan", "conclusion": "success", "created": "2026-10-09T11:00:00Z", "url": "u"}
    assert not any("Falló" in a["text"] for a in ops.build_alerts(state(data, runs=[newer_success, old_failure]), NOW))
    assert any("Falló" in a["text"] for a in ops.build_alerts(state(data, runs=[old_failure]), NOW))


def test_alert_when_too_few_sites_were_measured_well_or_the_schedule_did_not_run(tmp_path):
    runs = tmp_path / "runs"
    write_day(runs, "2026-09-20", [report("A", 1, status="blocked"), report("B", 1, status="blocked"), report("C", 1)])
    s = {"github": {"variables": {}, "secrets": [], "runs": [], "issues": []}, "scan": ops.scan_summary(runs, ""),
         "site": {"up": True}, "bot": {}, "git": {}, "drafts": []}
    texts = [a["text"] for a in ops.build_alerts(s, NOW)]
    assert any("Solo 1 de 3" in t for t in texts) and any("22 días" in t for t in texts)


def test_only_allowed_switches_workflows_and_draft_folders_reach_gh():
    calls = []
    runner = lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0, stdout="", stderr="")
    assert ops.set_switch("POST_REVIEW", True, "o/r", runner) == "true"
    assert calls[-1] == ["gh", "variable", "set", "POST_REVIEW", "-b", "true", "-R", "o/r"]
    for bad in (("SITE_DEPLOY_KEY", True), ("POST_ENABLED", "yes"), ("PUBLISH_SITE; rm", False)):
        with pytest.raises(ops.OpsError):
            ops.set_switch(*bad, "o/r", runner)
    ops.run_workflow("post-midweek.yml", "o/r", runner)
    assert calls[-1] == ["gh", "workflow", "run", "post-midweek.yml", "-R", "o/r"]
    with pytest.raises(ops.OpsError):
        ops.run_workflow("publish-draft.yml", "o/r", runner)
    assert len(calls) == 2


def test_approving_checks_the_folder_the_state_and_the_age(tmp_path):
    drafts = tmp_path / "drafts"
    today = date.today().isoformat()
    for name, posted in ((today, False), (today + "-midweek", True), ("2020-01-01", False)):
        folder = drafts / name
        folder.mkdir(parents=True)
        (folder / "bluesky.json").write_text(json.dumps({"date": name[:10], "posts": ["hello"]}), encoding="utf-8")
        if posted:
            (folder / "bluesky.posted.json").write_text(json.dumps({"complete": True}), encoding="utf-8")
    calls = []
    runner = lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0, stdout="", stderr="")
    for bad in ("../etc", "data/drafts/../x", f"data/drafts/{today}-midweek", "data/drafts/2020-01-01", "data/drafts/2027-01-01"):
        with pytest.raises(ops.OpsError):
            ops.approve_draft(bad, "o/r", runner, drafts)
    assert calls == []
    ops.approve_draft(f"data/drafts/{today}", "o/r", runner, drafts)
    assert calls == [["gh", "workflow", "run", "publish-draft.yml", "-R", "o/r", "-f", f"folder=data/drafts/{today}"]]


def test_bot_state_reads_the_public_profile_and_recent_posts():
    def fake(url):
        if "getProfile" in url:
            return 200, {"followersCount": 8, "followsCount": 3, "postsCount": 2, "labels": [{"val": "bot"}]}
        return 200, {"feed": [{"post": {"record": {"text": "hi"}, "indexedAt": "2026-10-09T10:00:00Z", "likeCount": 4}}]}
    b = ops.bot_state(fake)
    assert b["followers"] == 8 and b["bot_label"] and b["recent"][0]["likes"] == 4
    assert ops.bot_state(lambda url: (0, None)) == {"available": False}


@pytest.fixture
def admin(tmp_path, monkeypatch):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        out = {"variable": json.dumps([{"name": "POST_ENABLED", "value": "true"}]), "secret": "[]", "run": "[]", "issue": "[]"}.get(cmd[1], "")
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(ops, "urllib_get", lambda url, timeout=15: (200, {"followersCount": 1, "feed": []}))
    (tmp_path / "dash").mkdir()
    (tmp_path / "dash" / "index.html").write_text("<h1>dash</h1>", encoding="utf-8")
    server = make_server(tmp_path / "dash", tmp_path / "limits.json", "o/r", 0, runner)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SimpleNamespace(base=f"http://127.0.0.1:{server.server_address[1]}", calls=calls)
    server.shutdown()
    server.server_close()


def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_ops_page_and_status_are_served_and_writes_need_the_token(admin):
    status, body = call(admin.base + "/ops")
    assert status == 200 and "Panel de desarrollador" in body
    status, body = call(admin.base + "/api/ops/status")
    assert status == 200 and json.loads(body)["github"]["variables"] == {"POST_ENABLED": "true"}
    json_header = {"Content-Type": "application/json"}
    payload = {"name": "POST_ENABLED", "value": False}
    admin.calls.clear()
    assert call(admin.base + "/api/ops/switch", payload, json_header)[0] == 403
    assert call(admin.base + "/api/ops/switch", payload, {**json_header, "X-Admin-Token": "wrong"})[0] == 403
    assert admin.calls == []
    token = json.loads(call(admin.base + "/api/token")[1])["token"]
    good = {**json_header, "X-Admin-Token": token}
    assert call(admin.base + "/api/ops/switch", {"name": "SITE_DEPLOY_KEY", "value": True}, good)[0] == 400
    assert call(admin.base + "/api/ops/switch", payload, good)[0] == 200
    assert admin.calls[-1] == ["gh", "variable", "set", "POST_ENABLED", "-b", "false", "-R", "o/r"]
    assert call(admin.base + "/api/ops/run", {"workflow": "evil.yml"}, good)[0] == 400
    assert call(admin.base + "/api/ops/approve", {"folder": "data/drafts/../x"}, good)[0] == 400
