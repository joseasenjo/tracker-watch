"""The monthly data release: a zip of one month, with the trends, the lists and a README; citable metadata is valid."""
import json
import zipfile
from pathlib import Path

import pytest

from test_trends import report, write
from traceguard import datarelease

ROOT = Path(__file__).resolve().parent.parent


def test_the_zip_holds_the_months_reports_the_trends_and_a_readme_and_not_other_months(tmp_path):
    write(tmp_path, "2026-09-28", [report("https://a/", 4, ["X"])])
    write(tmp_path, "2026-10-05", [report("https://a/", 8, ["X"])])
    write(tmp_path, "2026-10-12", [report("https://a/", 10, ["X"])])
    done = datarelease.build("2026-10", tmp_path / "runs", tmp_path / "dist", sites_file=str(tmp_path / "none.json"), root=ROOT)
    names = zipfile.ZipFile(done["zip"]).namelist()
    assert "data/runs/2026-10-05/s0.json" in names and "data/runs/2026-10-12/s0.json" in names
    assert not any("2026-09-28" in n for n in names)
    assert {"data/trends/series.csv", "data/trends/monthly.json", "README.txt", "DATA_LICENSE.md", "CITATION.cff"} <= set(names)
    readme = zipfile.ZipFile(done["zip"]).read("README.txt").decode()
    assert "2 scan(s): 2026-10-05, 2026-10-12" in readme and "counts, not legal conclusions" in readme.lower()
    assert (tmp_path / "dist" / "SHA256SUMS.txt").read_text().split()[0] == done["sha256"]
    assert not (tmp_path / "dist" / "series.csv").exists()  # the temporary files are gone


def test_a_month_without_scans_or_a_bad_month_is_refused(tmp_path, capsys):
    write(tmp_path, "2026-10-05", [report("https://a/", 8, ["X"])])
    assert datarelease.main(["2026-11", "--runs", str(tmp_path / "runs"), "--out", str(tmp_path / "d")]) == 2
    assert datarelease.main(["November", "--runs", str(tmp_path / "runs"), "--out", str(tmp_path / "d")]) == 2
    with pytest.raises(ValueError):
        datarelease.month_folders(tmp_path / "runs", "2026-13")


def test_the_citation_and_zenodo_files_are_valid_and_agree():
    import yaml
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    zenodo = json.loads((ROOT / ".zenodo.json").read_text(encoding="utf-8"))
    assert citation["cff-version"] == "1.2.0" and citation["authors"][0]["family-names"] == "Asenjo Tornero"
    assert zenodo["creators"][0]["name"] == "Asenjo Tornero, Jose Luis" and zenodo["upload_type"] == "dataset"
    assert zenodo["license"] == "cc-by-4.0" and citation["license"] == "CC-BY-4.0"
    assert zenodo["title"] == citation["title"]
