"""Zip the built extension for a GitHub Release, one file per browser family, with SHA-256 sums.

    python extension/tools/build.py --browser all
    python extension/tools/package.py --out release

Output: <out>/tracker-watch-lens-chrome.zip (Chrome, Edge and other Chromium browsers),
<out>/tracker-watch-lens-firefox.zip and <out>/SHA256SUMS.txt. The file names carry no version on purpose, so the
stable addresses .../releases/latest/download/<name> always point at the newest release.

Each zip has the extension at its root (manifest.json first), the same files and bytes on every run, and nothing
the browser creates by itself when it loads the folder (the `_metadata` folder). Test builds are never packaged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

EXT = Path(__file__).resolve().parents[1]
BROWSERS = ("chrome", "firefox")
SKIP = {"_metadata"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # the same bytes on every run


def package(dist: Path, out: Path, browsers=BROWSERS) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for browser in browsers:
        source = dist / browser
        if not (source / "manifest.json").is_file():
            raise SystemExit(f"{source} has no manifest.json: run extension/tools/build.py --browser all first")
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        if "(test build)" in manifest.get("name", ""):
            raise SystemExit("refusing to package a test build")
        files = sorted(p for p in source.rglob("*") if p.is_file() and not (set(p.relative_to(source).parts) & SKIP))
        files.sort(key=lambda p: (p.name != "manifest.json" or p.parent != source, p.relative_to(source).as_posix()))
        target = out / f"tracker-watch-lens-{browser}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for path in files:
                info = zipfile.ZipInfo(path.relative_to(source).as_posix(), FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, path.read_bytes())
        written.append(target)
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in written)
    (out / "SHA256SUMS.txt").write_text(sums, encoding="utf-8", newline="\n")
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dist", default=str(EXT / "dist"))
    parser.add_argument("--out", default="release")
    args = parser.parse_args(argv)
    for path in package(Path(args.dist), Path(args.out)):
        print(f"{path} ({path.stat().st_size} bytes)")
    print(f"{Path(args.out) / 'SHA256SUMS.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
