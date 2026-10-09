"""Local administration server: serves the internal dashboard and lets you edit the request limits.

Binds to 127.0.0.1 only. It edits data/limits.json (the file the workflows and the site read) and reads
recent requests with the GitHub CLI; it never commits, pushes or publishes anything: after changing the limits,
commit and push data/limits.json yourself (the dashboard shows the exact commands).

  python -m traceguard.site data/runs --out site --dashboard dashboard_local
  python -m traceguard.admin            # then open the address it prints

Protections against other web pages in your browser: only requests whose Host is 127.0.0.1/localhost are
answered (blocks DNS rebinding); changes need a token that only this server's own pages can read; and the
server sends no CORS headers.
"""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from . import ops
from .limits import BOUNDS, DEFAULTS, LimitsError, load_limits, save_limits

DEFAULT_REPO = "joseasenjo/tracker-watch"
LABELS = {"scan": "scan-request", "links": "link-request"}
MAX_BODY = 20_000
OPS_PAGE = Path(__file__).with_name("ops_page.html")
OPS_POST = ("/api/ops/switch", "/api/ops/run", "/api/ops/approve", "/api/ops/pull", "/api/ops/note",
            "/api/ops/scan", "/api/ops/link", "/api/ops/link/remove", "/api/ops/links/publish")


def recent_requests(repo: str, runner: Callable = subprocess.run, now: datetime | None = None) -> dict:
    """Requests of the last 24 h per feature, read with the GitHub CLI. {'available': False} without it."""
    now = now or datetime.now(timezone.utc)
    usage: dict = {"available": True, "scan": [], "links": []}
    for kind, label in LABELS.items():
        try:
            result = runner(["gh", "issue", "list", "--repo", repo, "--label", label, "--state", "all", "--limit",
                             "100", "--json", "number,author,createdAt,state"],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
            if result.returncode != 0:
                return {"available": False, "scan": [], "links": []}
            items = json.loads(result.stdout or "[]")
        except (OSError, ValueError, subprocess.SubprocessError):
            return {"available": False, "scan": [], "links": []}
        usage[kind] = [{"number": i["number"], "author": (i.get("author") or {}).get("login", "?"),
                        "created": i["createdAt"], "state": i["state"].lower()}
                       for i in items if now - datetime.fromisoformat(i["createdAt"].replace("Z", "+00:00"))
                       < timedelta(hours=24)]
    return usage


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, limits_path: Path, repo: str, token: str, runner: Callable, **kwargs):
        self.limits_path, self.repo, self.token, self.runner = limits_path, repo, token, runner
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):  # quiet
        pass

    def _allowed_host(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _json(self, status: int, payload) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._allowed_host():
            return self._json(403, {"error": "forbidden host"})
        if self.path == "/api/token":
            return self._json(200, {"token": self.token})
        if self.path == "/api/limits":
            try:
                return self._json(200, {"limits": load_limits(self.limits_path), "defaults": DEFAULTS,
                                        "bounds": BOUNDS, "file": str(self.limits_path)})
            except LimitsError as exc:
                return self._json(500, {"error": str(exc)})
        if self.path == "/ops":
            data = OPS_PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/api/ops/status":
            return self._json(200, ops.get_status(self.repo, runner=self.runner))
        if self.path == "/api/ops/links":
            return self._json(200, ops.list_links(runner=self.runner))
        if self.path.startswith("/api/ops/scan?"):
            try:
                return self._json(200, ops.scan_status(parse_qs(urlsplit(self.path).query).get("id", [""])[0]))
            except ops.OpsError as exc:
                return self._json(404, {"error": str(exc)})
        if self.path == "/api/usage":
            return self._json(200, recent_requests(self.repo, self.runner))
        if self.path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        return super().do_GET()

    def do_POST(self):
        if not self._allowed_host():
            return self._json(403, {"error": "forbidden host"})
        if self.path not in ("/api/limits", *OPS_POST):
            return self._json(404, {"error": "not found"})
        if not secrets.compare_digest(self.headers.get("X-Admin-Token", ""), self.token):
            return self._json(403, {"error": "bad token"})
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._json(415, {"error": "send JSON"})
        if self.path in OPS_POST:
            return self._ops_post()
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= MAX_BODY:
                return self._json(413, {"error": "bad size"})
            saved = save_limits(json.loads(self.rfile.read(size).decode("utf-8")), self.limits_path)
        except (ValueError, LimitsError) as exc:
            return self._json(400, {"error": str(exc)})
        return self._json(200, {"limits": saved, "saved_to": str(self.limits_path)})


    def _ops_post(self):
        """The few write actions of the developer dashboard; each one is checked again in traceguard.ops."""
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= MAX_BODY:
                return self._json(413, {"error": "bad size"})
            body = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(body, dict):
                raise ops.OpsError("se esperaba un objeto JSON")
            if self.path == "/api/ops/switch":
                value = ops.set_switch(str(body.get("name", "")), body.get("value"), self.repo, self.runner)
                return self._json(200, {"ok": True, "name": body["name"], "value": value})
            if self.path == "/api/ops/run":
                ops.run_workflow(str(body.get("workflow", "")), self.repo, self.runner)
                return self._json(200, {"ok": True})
            if self.path == "/api/ops/note":
                notes = ops.change_note(str(body.get("action", "")), note_id=str(body.get("id", "")),
                                        text=str(body.get("text", "")))
                return self._json(200, {"ok": True, "notes": notes})
            if self.path == "/api/ops/scan":
                return self._json(200, {"ok": True, "id": ops.start_scan(str(body.get("url", "")))})
            if self.path == "/api/ops/link":
                done = ops.create_link(str(body.get("url", "")), str(body.get("code", "")), str(body.get("note", "")),
                                       measure=bool(body.get("measure", True)))
                return self._json(200, {"ok": True, **done})
            if self.path == "/api/ops/link/remove":
                ops.remove_link(str(body.get("code", "")))
                return self._json(200, {"ok": True})
            if self.path == "/api/ops/links/publish":
                return self._json(200, {"ok": True, "steps": ops.publish_links(self.repo, self.runner)})
            if self.path == "/api/ops/approve":
                ops.approve_draft(str(body.get("folder", "")), self.repo, self.runner)
                return self._json(200, {"ok": True})
            return self._json(200, {"ok": True, "output": ops.pull_data(self.runner)})  # /api/ops/pull
        except (ValueError, ops.OpsError) as exc:
            return self._json(400, {"error": str(exc)})


def make_server(dashboard_dir: Path | str, limits_path: Path | str, repo: str = DEFAULT_REPO, port: int = 8765,
                runner: Callable = subprocess.run) -> ThreadingHTTPServer:
    handler = partial(Handler, directory=str(dashboard_dir), limits_path=Path(limits_path), repo=repo,
                      token=secrets.token_urlsafe(24), runner=runner)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.admin", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dashboard", default="dashboard_local")
    parser.add_argument("--limits", default="data/limits.json")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not (Path(args.dashboard) / "index.html").exists():
        print(f"no dashboard in {args.dashboard}: run python -m traceguard.site data/runs --dashboard {args.dashboard}",
              file=sys.stderr)
        return 2
    server = make_server(args.dashboard, args.limits, args.repo, args.port)
    print(f"Dashboard: http://127.0.0.1:{server.server_address[1]}/   Developer panel: http://127.0.0.1:{server.server_address[1]}/ops   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
