"""The monthly data release: one zip with the reports of a month, the trends and the lists they were made with.

It is what the data-release workflow attaches to a GitHub release (tag data-YYYY-MM). Zenodo, once the repository is linked
to it, archives every GitHub release and gives it a DOI, so each month's data becomes citable. The zip holds only public
measurement data (the weekly reports, the site list, the classification list, the trends) and the licences; nothing private.

Usage:  python -m traceguard.datarelease 2026-10 --out dist
        (writes tracker-watch-data-2026-10.zip and SHA256SUMS.txt in dist/)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

from . import trends as trends_module
from .posts import load_groups

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
FIXED = ("data/sites.json", "data/trackers.seed.json", "DATA_LICENSE.md", "CITATION.cff")


def month_folders(runs_dir: Path | str, month: str) -> list[Path]:
    if not MONTH_RE.fullmatch(month):
        raise ValueError("the month must look like 2026-10")
    return sorted(p for p in Path(runs_dir).iterdir() if p.is_dir() and p.name.startswith(month + "-"))


def readme(month: str, folders: list[Path], summary: dict) -> str:
    row = next((m for m in summary["monthly"] if m["month"] == month), None)
    scans = ", ".join(f.name for f in folders)
    figures = (f"Median tracking services per site (mean of the weekly medians): {row['median_tracking']}. "
               f"Concentration index: {row['concentration_index']}.") if row else "No scan of this month is comparable with the latest one."
    return f"""Tracker Watch: data of {month}

What this is: the weekly measurements of the third-party tracking services that 48 news websites contact before any
interaction, taken in {month} ({len(folders)} scan(s): {scans}), plus the trends compiled from the whole series.

Contents
  data/runs/<date>/<site>.json   the raw report of each site and scan (conditions, every pass, the request log)
  data/trends/series.csv         one row per scan under comparable conditions
  data/trends/monthly.json       one row per month, and the change against the previous one
  data/sites.json               the 48 sites with their country and type
  data/trackers.seed.json       the classification list used to count tracking services
  DATA_LICENSE.md, CITATION.cff the licence (CC BY 4.0) and how to cite

{figures}

Read this before using the numbers: counts are minimums (the classification list is limited), they come from one origin
(GitHub servers in the US) and describe what happens with no interaction. They are counts, not legal conclusions, and they
say nothing about intent. Method: https://joseasenjo.github.io/tracker-watch/method.html
Code (PolyForm Noncommercial 1.0.0): https://github.com/joseasenjo/tracker-watch
"""


def build(month: str, runs_dir: Path | str, out_dir: Path | str, sites_file: str = "data/sites.json", root: Path | str = ".") -> dict:
    """Write the zip and its checksum file; returns {zip, sha256, files}. Raises when the month has no scan."""
    folders = month_folders(runs_dir, month)
    if not folders:
        raise FileNotFoundError(f"no scan of {month} in {runs_dir}")
    root, out = Path(root), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = trends_module.summary(runs_dir, load_groups(sites_file))
    archive = out / f"tracker-watch-data-{month}.zip"
    names: list[str] = []
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        def add(path: Path, name: str) -> None:
            bundle.write(path, name)
            names.append(name)
        for folder in folders:
            for report in sorted(folder.glob("*.json")):
                add(report, f"data/runs/{folder.name}/{report.name}")
        series_csv, monthly_json = out / "series.csv", out / "monthly.json"
        trends_module.write_series_csv(summary["series"], series_csv)
        monthly_json.write_text(json.dumps({"monthly": summary["monthly"], "change": summary["change"], "scans": len(summary["series"]),
                                            "excluded_scans": summary["excluded"]}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        add(series_csv, "data/trends/series.csv")
        add(monthly_json, "data/trends/monthly.json")
        for fixed in FIXED:
            path = root / fixed
            if path.exists():
                add(path, fixed)
        bundle.writestr("README.txt", readme(month, folders, summary))
        names.append("README.txt")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    for temp in (out / "series.csv", out / "monthly.json"):
        temp.unlink(missing_ok=True)
    return {"zip": archive, "sha256": digest, "files": names}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.datarelease", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("month", help="YYYY-MM")
    parser.add_argument("--runs", default="data/runs")
    parser.add_argument("--sites-file", default="data/sites.json")
    parser.add_argument("--out", default="dist")
    args = parser.parse_args(argv)
    try:
        done = build(args.month, args.runs, args.out, args.sites_file)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}")
        return 2
    print(f"{done['zip']}  {len(done['files'])} files  sha256 {done['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
