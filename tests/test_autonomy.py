"""The autonomy log: notes are written once, and each week combines the scan, the posts and what was held back."""
import json

from traceguard import autonomy


def test_note_is_idempotent_and_survives_a_broken_file(tmp_path):
    autonomy.note("2026-11-16", "changes", "left out  two\nreadings", tmp_path)
    autonomy.note("2026-11-16", "changes", "left out two readings", tmp_path)
    assert autonomy.load_notes(tmp_path) == {"2026-11-16": [{"kind": "changes", "text": "left out two readings"}]}
    (tmp_path / "2026-11-23.json").write_text("{not json", encoding="utf-8")
    autonomy.note("2026-11-23", "hook", "no steady leader", tmp_path)
    assert autonomy.load_notes(tmp_path)["2026-11-23"][0]["kind"] == "hook"


def test_weeks_combines_scan_posts_and_notes(tmp_path):
    for day, status in (("2026-11-09", "ok"), ("2026-11-16", "ok")):
        folder = tmp_path / "runs" / day
        folder.mkdir(parents=True)
        for stem, st in (("a", status), ("b", "blocked")):
            (folder / f"{stem}.json").write_text(json.dumps({
                "site": {"name": stem.upper()}, "generated_at": f"{day}T07:00:00+00:00",
                "summary": {"status": st, "confidence": "high"}}), encoding="utf-8")
    draft = tmp_path / "drafts" / "2026-11-16-hook"
    draft.mkdir(parents=True)
    (draft / "mastodon.posted.json").write_text(json.dumps({"complete": False, "posts": [1]}), encoding="utf-8")
    autonomy.note("2026-11-16", "alert", "held back", tmp_path / "autonomy")
    rows = autonomy.weeks(tmp_path / "runs")
    assert [r["date"] for r in rows] == ["2026-11-16", "2026-11-09"]
    first = rows[0]
    assert first["measured"] == 1 and first["sites"] == 2 and first["unmeasured"] == ["B"]
    assert first["posts"][0]["label"] == "Hook post"
    assert first["posts"][0]["networks"] == {"bluesky": None, "mastodon": {"complete": False, "posts": 1, "at": ""}}
    assert first["held_back"][0]["text"] == "held back" and rows[1]["held_back"] == []
    assert len(autonomy.weeks(tmp_path / "runs", limit=1)) == 1
