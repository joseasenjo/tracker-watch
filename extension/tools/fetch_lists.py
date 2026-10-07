"""Download the third-party lists the extension converts at build time into extension/vendor/.

    python extension/tools/fetch_lists.py

Run it only when you decide to update them: the build never downloads anything. Each file is stored unmodified,
next to vendor/lists.json (source, date, SHA-256, licence), and credited in THIRD_PARTY.md.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "vendor"
LISTS = {
    "easyprivacy.txt": {
        "url": "https://easylist.to/easylist/easyprivacy.txt",
        "licence": "GPL-3.0-or-later OR CC-BY-SA-3.0 (used under CC BY-SA 3.0); credit: The EasyList authors (https://easylist.to/)",
    },
    "cname_original_trackers.txt": {
        "url": "https://raw.githubusercontent.com/AdguardTeam/cname-trackers/master/data/combined_original_trackers.txt",
        "licence": "MIT; credit: AdGuard (https://github.com/AdguardTeam/cname-trackers)",
    },
}


def main() -> int:
    VENDOR.mkdir(exist_ok=True)
    meta = {}
    for name, info in LISTS.items():
        req = urllib.request.Request(info["url"], headers={"User-Agent": "tracker-watch-lens-build"})
        body = urllib.request.urlopen(req, timeout=60).read()
        (VENDOR / name).write_bytes(body)
        meta[name] = {**info, "fetched": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        print(f"{name}: {len(body):,} bytes")
    (VENDOR / "lists.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
