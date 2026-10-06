# Third-party material in Tracker Watch Lens

The extension's own code is MIT (see `LICENSE` at the repository root). It ships the following material
from other sources, unmodified in substance:

## Public Suffix List — `data/psl.json`

- Source: the Public Suffix List (https://publicsuffix.org/), ICANN section only, taken from the snapshot
  bundled with the Python package `tldextract` so that the extension and the measurement engine use the
  same rules. Non-ASCII rules are also listed in their punycode form.
- Licence: Mozilla Public License 2.0 (https://mozilla.org/MPL/2.0/). The rules are reproduced as data;
  no other change was made.

## Tracker Watch data — `data/trackers.json`, `data/glossary.json`, `data/sites.json`

- Generated from this repository by `tools/build_data.py`. Licence: CC BY 4.0 (credit "Tracker Watch"),
  as the rest of the project's data (see `DATA_LICENSE.md` at the repository root).
