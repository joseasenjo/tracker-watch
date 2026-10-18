"""The email digest in the developer dashboard: the subscriber list, its sync to GitHub and the approval of each digest."""
import json
from datetime import date
from types import SimpleNamespace

import pytest

from traceguard import digest, mailing, ops

from test_ops import admin, call  # noqa: F401  (fixtures and helper of the dashboard server tests)

TODAY = date(2026, 10, 16)
VARIABLES = [{"name": "DIGEST_ENABLED", "value": "true"}, {"name": "CONTACT_EMAIL", "value": "contact@example.org"}]


def make_runner(variables=VARIABLES, secret_ok=True):
    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:3] == ["gh", "variable", "list"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(variables), stderr="")
        if cmd[:3] == ["gh", "secret", "set"]:
            return SimpleNamespace(returncode=0 if secret_ok else 1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    runner.calls = calls
    return runner


def draft(tmp_path, name="2026-10-12", day="2026-10-12", posted=True, sent=False):
    folder = tmp_path / "drafts" / name
    folder.mkdir(parents=True)
    (folder / "bluesky.json").write_text(json.dumps({"date": day, "posts": ["Result one.", "Result two."]}), encoding="utf-8")
    if posted:
        (folder / "bluesky.posted.json").write_text(json.dumps({"complete": True, "posts": [{"url": "https://bsky.app/p/1"}]}), encoding="utf-8")
    if sent:
        (folder / digest.MARKER).write_text("{}", encoding="utf-8")
    return folder


@pytest.fixture
def lists(tmp_path):
    return SimpleNamespace(path=tmp_path / "private" / "subscribers.json", mark=tmp_path / "private" / ".synced",
                           drafts=tmp_path / "drafts")


def seed(path, confirmed=("ana@example.org", "ben@example.org"), pending=("cai@example.org",)):
    subs = []
    for email in confirmed:
        subs = mailing.confirm(mailing.add(subs, email, source="mailto", note="asked", today=TODAY), email, today=TODAY)
    for email in pending:
        subs = mailing.add(subs, email, source="mailto", note="asked", today=TODAY)
    mailing.save_subscribers(subs, path)


# --- the list in the dashboard ----------------------------------------------------------------------------------------

def test_the_state_of_a_missing_list_and_of_a_list_with_pending_people_first(lists):
    empty = ops.subscribers_state(lists.path, lists.mark)
    assert empty["has_list"] is False and empty["entries"] == [] and empty["counts"]["confirmed"] == 0
    seed(lists.path)
    state = ops.subscribers_state(lists.path, lists.mark)
    assert [e["status"] for e in state["entries"]] == ["pending", "confirmed", "confirmed"]
    assert state["counts"] == {"pending": 1, "confirmed": 2, "unsubscribed": 0} and state["synced"] is False


def test_add_confirm_and_remove_change_the_private_list_only(lists):
    ops.change_subscriber("add", "Dan@Example.org", note="asked by email on 2026-10-16", interest=True, path=lists.path)
    state = ops.change_subscriber("confirm", "dan@example.org", path=lists.path)
    assert state["entries"][0]["email"] == "dan@example.org" and state["entries"][0]["status"] == "confirmed" and state["entries"][0]["interest"]
    assert ops.change_subscriber("remove", "dan@example.org", path=lists.path)["counts"]["unsubscribed"] == 1
    for bad in (("add", "not-an-address", "x"), ("add", "ok@example.org", ""), ("confirm", "nobody@example.org", ""), ("explode", "ok@example.org", "x")):
        with pytest.raises(ops.OpsError):
            ops.change_subscriber(bad[0], bad[1], note=bad[2], path=lists.path)


def test_syncing_sends_the_list_on_stdin_remembers_only_a_hash_and_notices_later_changes(lists):
    seed(lists.path)
    runner = make_runner()
    state = ops.sync_subscribers("o/r", runner, lists.path, lists.mark)
    command, kwargs = runner.calls[0]
    assert command == ["gh", "secret", "set", "SUBSCRIBERS_JSON", "-R", "o/r"] and "ana@example.org" in kwargs["input"]
    assert state["synced"] is True and "@" not in lists.mark.read_text(encoding="utf-8")
    ops.change_subscriber("confirm", "cai@example.org", path=lists.path)
    assert ops.subscribers_state(lists.path, lists.mark)["synced"] is False


def test_syncing_an_empty_install_creates_the_list_and_failures_are_reported(lists):
    assert ops.sync_subscribers("o/r", make_runner(), lists.path, lists.mark)["has_list"] is True
    with pytest.raises(ops.OpsError):
        ops.sync_subscribers("o/r", make_runner(secret_ok=False), lists.path, lists.mark)
    lists.path.write_text(json.dumps({"subscribers": []}) + " " * 41_000, encoding="utf-8")
    with pytest.raises(ops.OpsError):
        ops.sync_subscribers("o/r", make_runner(), lists.path, lists.mark)


# --- the digests waiting for approval ---------------------------------------------------------------------------------

def test_a_published_digest_that_has_not_gone_out_waits_with_its_text_and_audience(tmp_path, lists):
    draft(tmp_path)
    seed(lists.path)
    rows = ops.digest_candidates(lists.drafts, {"CONTACT_EMAIL": "contact@example.org"}, lists.path, TODAY)
    assert len(rows) == 1 and rows[0]["folder"] == "data/drafts/2026-10-12" and rows[0]["recipients"] == 2
    assert rows[0]["subject"] == "[Tracker Watch] Weekly check, 2026-10-12"
    assert "write to contact@example.org" in rows[0]["body"] and "Result one." in rows[0]["body"]


def test_what_is_not_ready_or_not_wanted_never_waits(tmp_path, lists):
    draft(tmp_path, "2026-10-12", posted=False)                       # not published yet
    draft(tmp_path, "2026-10-13-announce", day="2026-10-13")           # kind that is not emailed
    draft(tmp_path, "2026-10-14-midweek", day="2026-10-14", sent=True)  # already sent
    draft(tmp_path, "2026-09-01", day="2026-09-01")                    # too old
    seed(lists.path)
    assert ops.digest_candidates(lists.drafts, {}, lists.path, TODAY) == []


def test_approval_runs_the_workflow_with_only_the_folder_name(tmp_path, lists):
    draft(tmp_path)
    seed(lists.path)
    ops.sync_subscribers("o/r", make_runner(), lists.path, lists.mark)
    runner = make_runner()
    ops.approve_digest("data/drafts/2026-10-12", "o/r", runner, drafts_dir=lists.drafts, subs_path=lists.path, mark=lists.mark, today=TODAY)
    command = runner.calls[-1][0]
    assert command == ["gh", "workflow", "run", "send-digest.yml", "-R", "o/r", "-f", "folder=data/drafts/2026-10-12", "-f", "mode=send"]
    assert not any("@" in part for part in command)  # no address travels in the request


@pytest.mark.parametrize("change, message", [
    ({"folder": "../../etc"}, "no válida"),
    ({"folder": "data/drafts/2026-10-20"}, "no está en tu copia"),
])
def test_approval_refuses_a_bad_or_missing_folder(tmp_path, lists, change, message):
    draft(tmp_path)
    seed(lists.path)
    with pytest.raises(ops.OpsError, match=message):
        ops.approve_digest(change["folder"], "o/r", make_runner(), drafts_dir=lists.drafts, subs_path=lists.path, mark=lists.mark, today=TODAY)


def test_approval_needs_confirmed_people_a_synced_list_the_switch_on_and_a_pending_digest(tmp_path, lists):
    draft(tmp_path)
    kwargs = dict(drafts_dir=lists.drafts, subs_path=lists.path, mark=lists.mark, today=TODAY)
    seed(lists.path, confirmed=(), pending=("cai@example.org",))
    with pytest.raises(ops.OpsError, match="no hay suscriptores confirmados"):
        ops.approve_digest("data/drafts/2026-10-12", "o/r", make_runner(), **kwargs)
    seed(lists.path)
    with pytest.raises(ops.OpsError, match="no está sincronizada"):
        ops.approve_digest("data/drafts/2026-10-12", "o/r", make_runner(), **kwargs)
    ops.sync_subscribers("o/r", make_runner(), lists.path, lists.mark)
    with pytest.raises(ops.OpsError, match="DIGEST_ENABLED"):
        ops.approve_digest("data/drafts/2026-10-12", "o/r", make_runner([{"name": "DIGEST_ENABLED", "value": "false"}]), **kwargs)
    draft(tmp_path, "2026-10-14-midweek", day="2026-10-14", sent=True)
    with pytest.raises(ops.OpsError, match="no está pendiente"):
        ops.approve_digest("data/drafts/2026-10-14-midweek", "o/r", make_runner(), **kwargs)


def test_a_test_message_needs_neither_subscribers_nor_the_switch(tmp_path, lists):
    draft(tmp_path, posted=False)
    runner = make_runner()
    ops.approve_digest("data/drafts/2026-10-12", "o/r", runner, test=True, drafts_dir=lists.drafts, subs_path=lists.path, mark=lists.mark)
    assert runner.calls[-1][0][-2:] == ["-f", "mode=test"] and len(runner.calls) == 1


# --- alerts and the server --------------------------------------------------------------------------------------------

def test_the_dashboard_warns_about_waiting_digests_pending_people_and_an_unsynced_list():
    state = {"github": {"variables": {}, "secrets": [], "runs": [], "issues": []}, "scan": {"available": False}, "site": {"up": True},
             "bot": {}, "git": {}, "drafts": [], "digests": [{"recipients": 3, "folder": "x"}],
             "subscribers": {"counts": {"pending": 2, "confirmed": 3, "unsubscribed": 0}, "has_list": True, "synced": False}}
    texts = [a["text"] for a in ops.build_alerts(state)]
    assert any("1 resumen(es) por correo esperan tu aprobación (3 suscriptor" in t for t in texts)
    assert any("2 persona(s) pidieron suscribirse" in t for t in texts) and any("no está sincronizada" in t for t in texts)
    quiet = {**state, "digests": [{"recipients": 0, "folder": "x"}], "subscribers": {"counts": {}, "has_list": False, "synced": True}}
    assert not any("correo" in a["text"] or "suscri" in a["text"] for a in ops.build_alerts(quiet))


def test_the_new_routes_need_the_token_and_refuse_nonsense(admin):  # noqa: F811
    for path in ("/api/ops/subscriber", "/api/ops/subscriber/import", "/api/ops/subscribers/sync", "/api/ops/digest"):
        status, _ = call(admin.base + path, {"action": "add"}, {"Content-Type": "application/json"})
        assert status == 403
    token = json.loads(call(admin.base + "/api/token")[1])["token"]
    headers = {"Content-Type": "application/json", "X-Admin-Token": token}
    assert call(admin.base + "/api/ops/subscriber", {"action": "explode", "email": "ok@example.org"}, headers)[0] == 400
    assert call(admin.base + "/api/ops/digest", {"folder": "../../x"}, headers)[0] == 400
    assert call(admin.base + "/api/ops/subscriber/import", {"text": "nothing useful here"}, headers)[0] == 400  # writes nothing
    assert any(name for name in ops.SWITCHES if name == "DIGEST_ENABLED")
