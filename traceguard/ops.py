"""Developer dashboard logic: what is going on, what needs attention, and the few actions allowed.

Used by traceguard.admin (page /ops). It reads the GitHub CLI, the local data folders and Bluesky's public API (no
login), and writes only through a short list: three repository variables, two workflows to start and the approval
of a draft. Nothing here is ever reachable from the public site. Every external call goes through an injectable
function so it can be tested without a network.
"""
from __future__ import annotations

import json
import os
import re
import uuid
import statistics
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Callable

DEFAULT_REPO = "joseasenjo/tracker-watch"
BOT_ACTOR = "trackerwatch.bsky.social"
SWITCHES = {
    "POST_ENABLED": "Publicar en Bluesky (apagado: no se publica nada)",
    "POST_REVIEW": "Pedir mi aprobación antes de publicar (encendido: solo se genera el borrador)",
    "PUBLISH_SITE": "Publicar la web en GitHub Pages tras cada escaneo",
}
WORKFLOWS = {"weekly-scan.yml": "Escaneo semanal + hilo", "post-midweek.yml": "Post de media semana"}
SECRETS = ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD")
SITE_URL = "https://joseasenjo.github.io/tracker-watch/"
DRAFT_RE = re.compile(r"^data/drafts/\d{4}-\d{2}-\d{2}(-midweek)?$")
SWING = 30            # a change of this many tracking services between two scans is worth a look
MIN_SHARE = 0.6       # below this share of measured sites the weekly thread is not drafted
STALE_DAYS = 9        # a weekly scan older than this means the schedule did not run
USABLE = ("high", "medium")
NOTES_FILE = Path("notas_desarrollador/pizarra.json")  # private: the folder is in .gitignore
MAX_NOTE = 4000
MAX_NOTES = 500


class OpsError(ValueError):
    pass


def _run(runner: Callable, args: list[str], timeout: int = 60) -> str:
    try:
        result = runner(args, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpsError(f"{args[0]} no responde: {exc}") from exc
    if result.returncode != 0:
        raise OpsError((result.stderr or result.stdout or f"{args[0]} falló").strip()[:300])
    return result.stdout or ""


def _gh_json(runner: Callable, args: list[str]):
    out = _run(runner, ["gh", *args])
    try:
        return json.loads(out or "null")
    except ValueError as exc:
        raise OpsError("respuesta de gh no válida") from exc


def urllib_get(url: str, timeout: float = 15) -> tuple[int, dict | None]:
    """(status, JSON or None). A network failure returns (0, None)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "tracker-watch-ops"}),
                                    timeout=timeout) as response:
            body = response.read()
            try:
                return response.status, json.loads(body)
            except ValueError:
                return response.status, None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (OSError, ValueError):
        return 0, None


# --- the last measurements -----------------------------------------------------------------------------------------

def _load_day(folder: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def _usable(report: dict) -> bool:
    s = report["summary"]
    return s["status"] == "ok" and s.get("confidence", "high") in USABLE


def scan_summary(runs_dir: Path | str, sites_file: Path | str = "data/sites.json", spain_dir: Path | str = "") -> dict:
    """What the latest scan measured, what it could not, and the biggest moves against the previous one."""
    runs = Path(runs_dir)
    days = sorted(p for p in runs.iterdir() if p.is_dir()) if runs.is_dir() else []
    if not days:
        return {"available": False}
    latest, previous = days[-1], (days[-2] if len(days) > 1 else None)
    reports = _load_day(latest)
    groups: dict[str, str] = {}
    if sites_file and Path(sites_file).exists():
        groups = {s["url"].rstrip("/"): s.get("group", "") for s in
                  json.loads(Path(sites_file).read_text(encoding="utf-8")).get("sites", [])}
    measured = [r for r in reports if r["summary"]["status"] == "ok"]
    usable = [r for r in reports if _usable(r)]
    by_group: dict[str, list[int]] = {}
    for r in usable:
        by_group.setdefault(groups.get(r["site"]["url"].rstrip("/"), "?"), []).append(
            r["summary"]["metrics"]["tracking_services"])
    swings = []
    if previous:
        before = {r["site"]["url"]: r["summary"]["metrics"]["tracking_services"] for r in _load_day(previous) if _usable(r)}
        for r in usable:
            old = before.get(r["site"]["url"])
            now = r["summary"]["metrics"]["tracking_services"]
            if old is not None and abs(now - old) >= SWING:
                swings.append({"name": r["site"]["name"], "before": old, "after": now, "delta": now - old})
        swings.sort(key=lambda x: -abs(x["delta"]))
    top = sorted(({"name": r["site"]["name"], "n": r["summary"]["metrics"]["tracking_services"]} for r in usable),
                 key=lambda x: (-x["n"], x["name"]))[:5]
    spain_age = None
    if spain_dir and Path(spain_dir).is_dir():
        spain_days = sorted(p.name for p in Path(spain_dir).iterdir() if p.is_dir())
        if spain_days:
            try:
                spain_age = (_date.fromisoformat(latest.name) - _date.fromisoformat(spain_days[-1])).days
            except ValueError:
                pass
    return {
        "available": True, "date": latest.name, "previous": previous.name if previous else None,
        "total": len(reports), "measured": len(measured), "usable": len(usable),
        "unmeasured": sorted(r["site"]["name"] for r in reports if r["summary"]["status"] != "ok"),
        "low_confidence": sorted(r["site"]["name"] for r in measured if not _usable(r)),
        "medians": {g: {"median": round(statistics.median(v)), "sites": len(v)} for g, v in sorted(by_group.items())},
        "swings": swings, "top": top, "spain_age_days": spain_age,
    }


def pending_drafts(drafts_dir: Path | str, today: _date | None = None) -> list[dict]:
    """Drafts on disk that were not published, newest first (those waiting for approval in review mode)."""
    base, today, rows = Path(drafts_dir), today or _date.today(), []
    if not base.is_dir():
        return rows
    for folder in sorted((p for p in base.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}(-midweek)?", p.name)),
                         reverse=True):
        draft_file = folder / "bluesky.json"
        if not draft_file.exists():
            continue
        try:
            draft = json.loads(draft_file.read_text(encoding="utf-8"))
        except ValueError:
            continue
        state_file = folder / "bluesky.posted.json"
        posted = state_file.exists() and bool(json.loads(state_file.read_text(encoding="utf-8")).get("complete"))
        try:
            age = (today - _date.fromisoformat(str(draft.get("date")))).days
        except ValueError:
            age = None
        rows.append({"folder": f"data/drafts/{folder.name}", "kind": "media semana" if folder.name.endswith("midweek") else "hilo semanal",
                     "date": draft.get("date"), "posts": draft.get("posts", []), "image": bool(draft.get("image")),
                     "posted": posted, "age_days": age, "stale": age is not None and age > 14})
    return rows[:12]


# --- GitHub and Bluesky -------------------------------------------------------------------------------------------

def github_state(repo: str, runner: Callable) -> dict:
    out: dict = {"available": True, "errors": []}

    def safe(key, fn):
        try:
            out[key] = fn()
        except OpsError as exc:
            out[key] = None
            out["errors"].append(f"{key}: {exc}")

    safe("variables", lambda: {v["name"]: v["value"] for v in _gh_json(runner, ["variable", "list", "-R", repo, "--json", "name,value"])})
    safe("secrets", lambda: sorted(s["name"] for s in _gh_json(runner, ["secret", "list", "-R", repo, "--json", "name"])))
    safe("runs", lambda: [
        {"id": r["databaseId"], "workflow": r["workflowName"], "status": r["status"], "conclusion": r.get("conclusion") or "",
         "created": r["createdAt"], "updated": r["updatedAt"], "url": r["url"], "event": r.get("event", "")}
        for r in _gh_json(runner, ["run", "list", "-R", repo, "-L", "12", "--json",
                                   "databaseId,workflowName,status,conclusion,createdAt,updatedAt,url,event"])])
    safe("issues", lambda: [{"number": i["number"], "title": i["title"], "labels": [l["name"] for l in i.get("labels", [])],
                             "url": i["url"], "created": i["createdAt"]}
                            for i in _gh_json(runner, ["issue", "list", "-R", repo, "--state", "open", "--limit", "30",
                                                       "--json", "number,title,labels,url,createdAt"])])
    if out["variables"] is None and out["runs"] is None:
        out["available"] = False
    return out


def bot_state(http_get: Callable, actor: str = BOT_ACTOR) -> dict:
    status, profile = http_get(f"https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={actor}")
    if status != 200 or not profile:
        return {"available": False}
    status, feed = http_get(f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={actor}&limit=8&filter=posts_no_replies")
    posts = []
    for item in (feed or {}).get("feed", []):
        p = item["post"]
        posts.append({"text": p["record"].get("text", ""), "at": p["indexedAt"], "likes": p.get("likeCount", 0),
                      "reposts": p.get("repostCount", 0), "replies": p.get("replyCount", 0), "quotes": p.get("quoteCount", 0)})
    return {"available": True, "followers": profile.get("followersCount"), "follows": profile.get("followsCount"),
            "posts_count": profile.get("postsCount"), "bot_label": any(l.get("val") == "bot" for l in profile.get("labels", [])),
            "recent": posts}


def site_state(http_get: Callable) -> dict:
    status, _ = http_get(SITE_URL)
    return {"url": SITE_URL, "status": status, "up": status == 200}


def git_state(runner: Callable, cwd: str = ".") -> dict:
    try:
        _run(runner, ["git", "-C", cwd, "fetch", "-q"], timeout=60)
        behind = int(_run(runner, ["git", "-C", cwd, "rev-list", "--count", "HEAD..origin/main"]).strip() or 0)
        ahead = int(_run(runner, ["git", "-C", cwd, "rev-list", "--count", "origin/main..HEAD"]).strip() or 0)
        return {"available": True, "behind": behind, "ahead": ahead}
    except (OpsError, ValueError):
        return {"available": False}


# --- alerts ---------------------------------------------------------------------------------------------------------

def build_alerts(state: dict, now: datetime | None = None) -> list[dict]:
    """What deserves a look, worst first: {level: 'alta'|'media'|'info', text}."""
    now, alerts = now or datetime.now(timezone.utc), []

    def add(level, text):
        alerts.append({"level": level, "text": text})

    gh, scan, site, bot, git = (state.get(k) or {} for k in ("github", "scan", "site", "bot", "git"))
    variables, secrets = gh.get("variables") or {}, gh.get("secrets") or []
    seen: set[str] = set()
    for run in gh.get("runs") or []:  # newest first: only the latest run of each workflow counts
        if run["workflow"] in seen:
            continue
        seen.add(run["workflow"])
        if run["conclusion"] == "failure":
            add("alta", f"Falló «{run['workflow']}» el {run['created'][:16].replace('T', ' ')} UTC. {run['url']}")
    if site and not site.get("up"):
        add("alta", f"La web no responde (estado {site.get('status')}).")
    if variables.get("POST_ENABLED") == "true":
        missing = [s for s in SECRETS if s not in secrets]
        if missing:
            add("alta", "La publicación está encendida pero faltan secretos: " + ", ".join(missing))
    if scan.get("available"):
        share = scan["usable"] / scan["total"] if scan["total"] else 0
        if share < MIN_SHARE:
            add("alta", f"Solo {scan['usable']} de {scan['total']} webs se midieron bien: el hilo semanal no se generará (mínimo {MIN_SHARE:.0%}).")
        try:
            age = (now.date() - _date.fromisoformat(scan["date"])).days
            if age > STALE_DAYS:
                add("media", f"El último escaneo tiene {age} días: el calendario no se ha ejecutado.")
        except ValueError:
            pass
        for s in scan["swings"][:5]:
            add("media", f"{s['name']}: de {s['before']} a {s['after']} servicios ({s['delta']:+d}) entre escaneos. Revisar antes de destacarlo.")
        if scan["unmeasured"]:
            add("info", f"{len(scan['unmeasured'])} webs sin medir: " + ", ".join(scan["unmeasured"]))
        if scan["low_confidence"]:
            add("info", "Confianza baja (fuera de los rankings): " + ", ".join(scan["low_confidence"]))
        age_es = scan.get("spain_age_days")
        if age_es is not None and age_es > 7:
            add("media", f"La medición de España desde tu PC tiene {age_es} días: el post de España no saldrá en el hilo.")
    if variables.get("POST_REVIEW") == "true":
        waiting = [d for d in state.get("drafts", []) if not d["posted"] and not d["stale"]]
        if waiting:
            add("alta", f"{len(waiting)} borrador(es) esperan tu aprobación.")
    if git.get("available") and git["behind"]:
        add("info", f"Tu copia local va {git['behind']} commit(s) por detrás de GitHub: pulsa «Actualizar datos».")
    if git.get("available") and git["ahead"]:
        add("info", f"Tienes {git['ahead']} commit(s) locales sin subir.")
    if gh.get("issues"):
        add("media", f"{len(gh['issues'])} incidencia(s) abiertas en GitHub (correcciones y réplicas de medios entran por aquí).")
    if bot.get("available") and not bot.get("bot_label"):
        add("media", "La cuenta de Bluesky no lleva la etiqueta «bot».")
    order = {"alta": 0, "media": 1, "info": 2}
    return sorted(alerts, key=lambda a: order[a["level"]])


def get_status(repo: str = DEFAULT_REPO, *, runner: Callable = subprocess.run, http_get: Callable | None = None,
               runs_dir="data/runs", sites_file="data/sites.json", spain_dir="data/extra/local-windows-spain",
               drafts_dir="data/drafts", now: datetime | None = None) -> dict:
    http_get = http_get or urllib_get  # looked up at call time
    state = {"github": github_state(repo, runner), "scan": scan_summary(runs_dir, sites_file, spain_dir),
             "drafts": pending_drafts(drafts_dir), "bot": bot_state(http_get), "site": site_state(http_get),
             "git": git_state(runner), "notes": load_notes(), "switches": SWITCHES, "workflows": WORKFLOWS, "secrets_needed": list(SECRETS),
             "schedule": "Escaneo e hilo: lunes 07:00 UTC · Post corto: miércoles 08:00 UTC"}
    state["alerts"] = build_alerts(state, now)
    return state


# --- the whiteboard -------------------------------------------------------------------------------------------------

def load_notes(path: Path | str = NOTES_FILE) -> list[dict]:
    """Notes newest first. 'archived' notes are only hidden and can be restored; 'delete' removes one for good (the
    previous state is kept once, in pizarra.bak.json)."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise OpsError(f"la pizarra ({path}) no se puede leer; no la toco: {exc}") from exc
    return data.get("notes", []) if isinstance(data, dict) else []


def _save_notes(notes: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():  # one copy of the previous state, in case of a mistake
        path.with_suffix(".bak.json").write_bytes(path.read_bytes())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"notes": notes}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def change_note(action: str, *, note_id: str = "", text: str = "", path: Path | str = NOTES_FILE,
                now: datetime | None = None) -> list[dict]:
    """action: add | edit | toggle (done / not done) | archive | restore | delete (removed for good)."""
    path, stamp = Path(path), (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")
    notes = load_notes(path)
    if action == "add":
        text = text.strip()
        if not text:
            raise OpsError("la nota está vacía")
        if len(text) > MAX_NOTE:
            raise OpsError(f"la nota supera {MAX_NOTE} caracteres")
        if len(notes) >= MAX_NOTES:
            raise OpsError("demasiadas notas: archiva las antiguas")
        notes.insert(0, {"id": uuid.uuid4().hex[:10], "text": text, "created": stamp, "done": False, "archived": False})
    else:
        note = next((n for n in notes if n.get("id") == note_id), None)
        if note is None:
            raise OpsError("esa nota no existe")
        if action == "edit":
            text = text.strip()
            if not text or len(text) > MAX_NOTE:
                raise OpsError("texto no válido")
            note["text"], note["edited"] = text, stamp
        elif action == "toggle":
            note["done"] = not note.get("done", False)
        elif action in ("archive", "restore"):
            note["archived"] = action == "archive"
        elif action == "delete":
            notes = [n for n in notes if n.get("id") != note_id]
        else:
            raise OpsError("acción desconocida")
    _save_notes(notes, path)
    return notes


# --- the allowed actions ------------------------------------------------------------------------------------------------

def set_switch(name: str, value, repo: str = DEFAULT_REPO, runner: Callable = subprocess.run) -> str:
    if name not in SWITCHES:
        raise OpsError("interruptor desconocido")
    if value not in (True, False, "true", "false"):
        raise OpsError("el valor debe ser true o false")
    text = "true" if value in (True, "true") else "false"
    _run(runner, ["gh", "variable", "set", name, "-b", text, "-R", repo])
    return text


def run_workflow(workflow: str, repo: str = DEFAULT_REPO, runner: Callable = subprocess.run) -> None:
    if workflow not in WORKFLOWS:
        raise OpsError("workflow no permitido")
    _run(runner, ["gh", "workflow", "run", workflow, "-R", repo])


def approve_draft(folder: str, repo: str = DEFAULT_REPO, runner: Callable = subprocess.run,
                  drafts_dir: Path | str = "data/drafts") -> None:
    if not DRAFT_RE.fullmatch(folder or ""):
        raise OpsError("carpeta de borrador no válida")
    rows = {d["folder"]: d for d in pending_drafts(drafts_dir)}
    draft = rows.get(folder)
    if not draft:
        raise OpsError("ese borrador no está en tu copia local: pulsa «Actualizar datos»")
    if draft["posted"]:
        raise OpsError("ese borrador ya se publicó")
    if draft["stale"]:
        raise OpsError("ese borrador tiene más de 14 días: no se publica")
    _run(runner, ["gh", "workflow", "run", "publish-draft.yml", "-R", repo, "-f", f"folder={folder}"])


def pull_data(runner: Callable = subprocess.run, cwd: str = ".") -> str:
    """git pull --ff-only: brings in what the workflows committed; refuses anything that is not a plain catch-up."""
    return _run(runner, ["git", "-C", cwd, "pull", "--ff-only"], timeout=120).strip()


# --- administrator tools: the same features as the public ones, without the limits ----------------------------------------
# Only the rate limits are skipped (per account, account age, daily and total caps). The safety checks stay: public
# addresses only, no credentials in the address, and the blocklist (other shorteners, blocked domains) for links.

LINKS_FILE = Path("data/links.json")
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _local_scan(url: str) -> dict:
    from .scanner import scan_site  # imported here so the module loads without a browser
    return scan_site(url, passes=3, observe_seconds=12.0, vantage="local-windows-spain", locale="es-ES",
                     timezone="Europe/Madrid")


def start_scan(url: str, *, scan: Callable = _local_scan, resolver=None, threaded: bool = True) -> str:
    """Measure an address from this PC, with no limits. Returns a job id; poll scan_status(id)."""
    from .safety import UnsafeURL, check_target, system_resolver
    try:
        url = check_target(url, resolver or system_resolver)
    except UnsafeURL as exc:
        raise OpsError(f"esa dirección no se puede analizar: {exc}") from exc
    job_id = uuid.uuid4().hex[:10]
    with _jobs_lock:
        while len(_jobs) >= 8:  # keep the last few only
            _jobs.pop(next(iter(_jobs)))
        _jobs[job_id] = {"status": "running", "url": url}

    def work():
        try:
            from .ondemand import result_payload
            report = scan(url)
            done = {"status": "done", "url": url, "report": report,
                    "payload": result_payload(report, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))}
        except Exception as exc:  # shown to the administrator, never raised into the server
            done = {"status": "error", "url": url, "error": f"{type(exc).__name__}: {exc}"[:300]}
        with _jobs_lock:
            _jobs[job_id] = done

    if threaded:
        threading.Thread(target=work, daemon=True).start()
    else:
        work()
    return job_id


def scan_status(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise OpsError("análisis desconocido (¿se reinició el panel?)")
    return job


def list_links(links_path: Path | str = LINKS_FILE, runner: Callable = subprocess.run, cwd: str = ".") -> dict:
    from .links import load_links
    links = load_links(links_path)
    try:
        dirty = bool(_run(runner, ["git", "-C", cwd, "status", "--porcelain", "--", str(links_path)]).strip())
    except OpsError:
        dirty = None
    return {"links": sorted(links, key=lambda x: x.get("created", ""), reverse=True), "unpublished": dirty,
            "site": SITE_URL}


def create_link(url: str, code: str = "", note: str = "", *, links_path: Path | str = LINKS_FILE, measure: bool = True,
                scan: Callable | None = None, blocklist=None, resolver=None) -> dict:
    """Create a short link now, with no per-account, daily or total limit. It goes live after publish_links()."""
    from . import links as L
    from .safety import system_resolver
    resolver = resolver or system_resolver
    blocklist = L.load_blocklist() if blocklist is None else blocklist
    try:
        url = L.validate_target(url, blocklist, resolver)
        existing = L.load_links(links_path)
        same = next((x for x in existing if x["url"] == url), None)
        if same and not code.strip():
            return {"created": False, "code": same["code"], "url": SITE_URL + f"go/{same['code']}/",
                    "message": "Esa dirección ya tenía un enlace."}
        code = L.validate_code(code) if code.strip() else L.generate_code({x["code"] for x in existing})
        check = (scan or L._scan_target)(url) if measure else None
        L.add_link(links_path, code, url, note=note, check=check, blocklist=blocklist, resolver=resolver)
    except L.LinkError as exc:
        raise OpsError(str(exc)) from exc
    return {"created": True, "code": code, "url": SITE_URL + f"go/{code}/", "message": "Enlace creado, pero TODAVÍA NO ESTÁ EN LA WEB: pulsa «Publicar enlaces» para que funcione."}


def remove_link(code: str, links_path: Path | str = LINKS_FILE) -> None:
    from . import links as L
    if not L.remove_link(links_path, code):
        raise OpsError("ese enlace no existe")


def publish_links(repo: str = DEFAULT_REPO, runner: Callable = subprocess.run, cwd: str = ".",
                  links_path: Path | str = LINKS_FILE) -> list[str]:
    """Commit data/links.json, push it and republish the website, so created or removed links go live."""
    steps = []
    path = str(links_path).replace(chr(92), "/")
    _run(runner, ["git", "-C", cwd, "add", "--", path])
    staged = _run(runner, ["git", "-C", cwd, "diff", "--cached", "--name-only"]).split()
    if staged and set(staged) - {path}:
        raise OpsError("hay otros archivos preparados en git: no publico enlaces para no mezclarlos")
    if staged:
        _run(runner, ["git", "-C", cwd, "commit", "-m", "Short links: update (from the developer panel)"])
        steps.append("commit hecho")
    _run(runner, ["git", "-C", cwd, "pull", "--rebase", "--autostash"], timeout=120)
    _run(runner, ["git", "-C", cwd, "push"], timeout=120)
    steps.append("subido a GitHub")
    _run(runner, ["gh", "workflow", "run", "publish-site.yml", "-R", repo])
    steps.append("web en proceso de republicación (unos 2 minutos)")
    return steps
