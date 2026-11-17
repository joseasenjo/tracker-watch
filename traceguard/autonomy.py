"""The public autonomy log: for each weekly scan, what was measured, what was published, and what was left out on purpose.

Nothing here is written by hand. It is read from three places the pipeline already leaves in the repository:
  - data/runs/<date>/        the scan itself (how many sites were measured well, which ones could not be)
  - data/drafts/<date>*/     the posts of that scan and whether each network got them (<platform>.posted.json)
  - data/autonomy/<date>.json  what the pipeline decided NOT to publish, and why (written by note())

The log exists so that "it runs by itself" can be checked: a reader sees every week, including the weeks where a post was
held back because the measurement was not reliable enough to name an outlet.
"""
from __future__ import annotations

import json
from pathlib import Path

AUTONOMY_DIR = Path("data/autonomy")
KINDS = (("", "Weekly thread"), ("-midweek", "Mid-week post"), ("-hook", "Hook post"), ("-alert", "Alert"), ("-monthly", "Monthly trends"))
PLATFORMS = ("bluesky", "mastodon")


def note(date: str, kind: str, text: str, directory: Path | str = AUTONOMY_DIR) -> None:
    """Record that something was left out on purpose (kind: alert, hook, changes ...). The same note is never written twice."""
    path = Path(directory) / f"{date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        notes = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except ValueError:
        notes = []
    entry = {"kind": kind, "text": " ".join(str(text).split())[:400]}
    if entry not in notes:
        path.write_text(json.dumps(notes + [entry], indent=1, ensure_ascii=False), encoding="utf-8")


def load_notes(directory: Path | str = AUTONOMY_DIR) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    folder = Path(directory)
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(data, list):
            found[path.stem] = [n for n in data if isinstance(n, dict) and "text" in n]
    return found


def _published(folder: Path, platform: str) -> dict | None:
    path = folder / f"{platform}.posted.json"
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return {"complete": bool(state.get("complete")), "posts": len(state.get("posts", [])),
            "at": str(state.get("finished_at", ""))[:16].replace("T", " ")}


def _scan(folder: Path) -> dict:
    reports = []
    for path in sorted(folder.glob("*.json")):
        try:
            reports.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            continue
    usable = [r for r in reports if r["summary"]["status"] == "ok" and r["summary"].get("confidence", "high") in ("high", "medium")]
    unmeasured = sorted(r["site"]["name"] for r in reports if r not in usable)
    stamps = sorted(str(r.get("generated_at", "")) for r in reports if r.get("generated_at"))
    return {"sites": len(reports), "measured": len(usable), "unmeasured": unmeasured,
            "finished_at": stamps[-1][:16].replace("T", " ") if stamps else ""}


def weeks(runs_dir: Path | str, drafts_dir: Path | str | None = None, autonomy_dir: Path | str | None = None,
          limit: int = 26) -> list[dict]:
    """One row per scan, newest first: the scan, each post kind with where it was published, and what was held back."""
    runs = Path(runs_dir)
    drafts = Path(drafts_dir) if drafts_dir is not None else runs.parent / "drafts"
    held = load_notes(autonomy_dir if autonomy_dir is not None else runs.parent / "autonomy")
    rows = []
    for folder in sorted((p for p in runs.iterdir() if p.is_dir()), reverse=True)[:limit]:
        date = folder.name
        posts = []
        for suffix, label in KINDS:
            draft = drafts / f"{date}{suffix}"
            if not draft.is_dir():
                continue
            posts.append({"label": label, "networks": {p: _published(draft, p) for p in PLATFORMS}})
        rows.append({"date": date, **_scan(folder), "posts": posts, "held_back": held.get(date, [])})
    return rows
