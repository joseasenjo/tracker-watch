# Monthly data releases and the DOI

Every month the `data-release` workflow (day 4, 10:00 UTC) packs the weekly reports of the previous month with the trends
and the lists they were made with, and publishes them as a GitHub release tagged `data-YYYY-MM`. It never takes the
"latest" mark: the extension page links to `releases/latest/download/...`, which must keep pointing at the extension.

## What a release contains

`tracker-watch-data-YYYY-MM.zip` with the raw report of every site and scan of the month (`data/runs/<date>/<site>.json`),
the trends (`data/trends/series.csv`, `monthly.json`), the site list, the classification list, the data licence, the
citation file and a README that says how to read the numbers. `SHA256SUMS.txt` carries the checksum of the zip. The zip is a
few MB. Nothing private is in it.

Run it by hand: Actions, `data-release`, Run workflow, with a month (`2026-10`) or empty for the previous one. Locally:
`python -m traceguard.datarelease 2026-10 --out dist`.

## The DOI (one step by the maintainer)

A DOI comes from Zenodo, which archives every GitHub release of a repository once it is linked. Linking needs the owner's
own account, so it is done once by hand:

1. Sign in at <https://zenodo.org> with the GitHub account (the Zenodo login with GitHub).
2. Open Account, GitHub, and switch on the repository `joseasenjo/tracker-watch`.
3. From then on each new GitHub release is archived and gets its own DOI plus a "concept" DOI that always points at the
   newest version. The metadata (title, author, licence, keywords) comes from `.zenodo.json`.
4. Put the concept DOI in `CITATION.cff` (`doi:`) and in the README badge when you have it.

What Zenodo archives is the snapshot of the repository at the release tag (the whole repository, with the code under
PolyForm Noncommercial and the data under CC BY 4.0), not only the zip. The record says so in its description. It also
archives the extension's releases (`lens-vX.Y.Z`), which is harmless. If you prefer a record with only the zip, upload the
zip to Zenodo by hand or through its API with a token kept as a repository secret.

## Why it matters

A DOI turns a month of measurements into something citable and permanent, outside GitHub: a journalist, a researcher or a
public body can cite exactly the data they used.
