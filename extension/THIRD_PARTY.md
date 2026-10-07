# Third-party material in Tracker Watch Lens

The extension's own code is MIT (see `LICENSE` at the repository root). It ships the following material
from other sources:

## Public Suffix List — `data/psl.json`

- Source: the Public Suffix List (https://publicsuffix.org/), ICANN section only, taken from the snapshot
  bundled with the Python package `tldextract` so that the extension and the measurement engine use the
  same rules. Non-ASCII rules are also listed in their punycode form.
- Licence: Mozilla Public License 2.0 (https://mozilla.org/MPL/2.0/). The rules are reproduced as data;
  no other change was made.

## Tracker Watch data — `data/trackers.json`, `data/glossary.json`, `data/sites.json`

- Generated from this repository by `tools/build_data.py`. Licence: CC BY 4.0 (credit "Tracker Watch"),
  as the rest of the project's data (see `DATA_LICENSE.md` at the repository root).

## EasyPrivacy (domain rules) — `rules/easyprivacy.json`

- Source: EasyPrivacy by The EasyList authors (https://easylist.to/), fetched unmodified into `vendor/easyprivacy.txt`
  by `tools/fetch_lists.py` (date and SHA-256 in `vendor/lists.json`).
- What ships: only its domain rules (`||host^` with party and resource-type options, and their `@@` exceptions),
  converted by `tools/vendor_rules.py` into declarativeNetRequest rules. Path rules and rules limited to some
  sites are left out. Used by the optional "extended" clean mode, off by default.
- Licence: EasyList offers GPL-3.0-or-later or CC BY-SA 3.0; this conversion is used and shared under
  **CC BY-SA 3.0** (https://creativecommons.org/licenses/by-sa/3.0/), credit "The EasyList authors". The converted
  file is an adaptation and keeps that licence; the extension's own code remains MIT.

## AdGuard cname-trackers — `data/cname_trackers.json`

- Source: https://github.com/AdguardTeam/cname-trackers (`combined_original_trackers.txt`), fetched into
  `vendor/cname_original_trackers.txt`; turned into a target -> company table by `tools/vendor_rules.py`.
  Used in Firefox to spot trackers disguised as the site itself (CNAME cloaking).
- Licence: MIT, Copyright 2021 Adguard Software Ltd; full text in `vendor/LICENSE-cname-trackers.txt`, also
  shipped in the package as `LICENSE-cname-trackers.txt`.
