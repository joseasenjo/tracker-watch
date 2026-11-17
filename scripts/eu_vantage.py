"""Measures the German and Spanish sites from a machine in the EU and writes them as an EXTRA measurement point.

Run on the VPS (not on a GitHub runner), from a clone of the repository, e.g. from cron:   python scripts/eu_vantage.py
It does not touch data/runs (the official weekly scan). It writes data/extra/<vantage>/<date>/ and commits nothing;
see docs/EU_VANTAGE.md for the cron line and how the results get back to the repository.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

VANTAGE = "eu-vps-valencia"
GROUPS = ("DE", "ES")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    sites = json.loads((root / "data" / "sites.json").read_text(encoding="utf-8"))
    chosen = [s for s in sites["sites"] if s.get("group") in GROUPS]
    out = root / "data" / "extra" / VANTAGE / date.today().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump({**sites, "sites": chosen}, handle)
    print(f"{len(chosen)} sites -> {out}")
    return subprocess.call([sys.executable, "-m", "traceguard", "--sites-file", handle.name, "--out", str(out),
                            "--vantage", VANTAGE, "--locale", "es-ES", "--timezone", "Europe/Madrid"], cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
