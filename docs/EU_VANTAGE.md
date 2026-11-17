# Measuring the German and Spanish sites from the EU

**Why.** The weekly scan runs on GitHub servers in the US. European outlets show their consent banner only to European
visitors, so from the US the German sites (median 1 tracking service) and Spanish sites (10) look very different from the UK
(70) for a reason that is the vantage point, not the sites. A machine in Valencia sees what a European reader sees.

**What.** `scripts/eu_vantage.py` measures only the sites of group `DE` and `ES` (24) and writes them to
`data/extra/eu-vps-valencia/<date>/`, the same place and format as the earlier `local-windows-spain` extra measurement.
It never touches `data/runs`, so the official series and the posts do not change.

## Steps (when the VPS exists; nothing here has been run)
1. On the VPS: clone the repository, `pip install -e .` (or the project's install step) and `playwright install --with-deps chromium`.
2. Try it once by hand: `python scripts/eu_vantage.py`. About 15–20 minutes for 24 sites.
3. Weekly cron, **after** the Monday scan, at an odd minute: `37 9 * * 1  cd ~/tracker-watch && git pull --ff-only && python scripts/eu_vantage.py >> ~/eu_vantage.log 2>&1`
4. Getting results back: commit `data/extra/eu-vps-valencia` and push with a **deploy key limited to this repository** (never your
   personal password or token in the cron). Until you set that up, copy the folder over by hand.
5. The site reads `data/extra/*` already; showing this vantage next to the US one (and replacing the permanent warning in
   post 2) is a separate change, to do once there are 3–4 weeks of European readings.

## Cautions
- Same identification and no bypass of blocks as the main scan.
- Credentials stay on the VPS; none should be pasted in a chat.
- Add `eu-vps-valencia` to `VANTAGE_LABELS` in `traceguard/site.py` when the site starts showing it.
