# SPDX-License-Identifier: AGPL-3.0-only
"""The census-plus-sample merge sheet, the attachment frame, and the Splink comparison.

Each test writes a small fictitious ledger (``Example Person NN``,
``example.org`` sites) into ``tmp_path``. The Splink test is skipped when the
optional extra is not installed.
"""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

from giye.audit.__main__ import main
from giye.audit.linker import linker_records
from giye.audit.sample import sample_sheet
from giye.audit.score import score_sheet
from giye.audit.sheet import read_sheet, write_sheet
from giye.config import load
from giye.ledger.schemas import TABLES


def _write(path: Path, table: str, rows: list[dict[str, str]]) -> None:
    filename, columns = TABLES[table]
    target = path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})


def _ledger(tmp_path: Path, people: list[dict[str, str]], **tables: list[dict[str, str]]) -> Path:
    data = tmp_path / "data"
    _write(data / "ledger", "artists", people)
    for table, rows in tables.items():
        _write(data / "ledger", table, rows)
    config = tmp_path / "giye.toml"
    config.write_text(
        f'[archive]\nname = "fictitious"\n\n[paths]\ndata = "{data.resolve().as_posix()}"\n', encoding="utf-8"
    )
    return config


def _person(index: int, note: str = "", name_en: str | None = None) -> dict[str, str]:
    return {
        "ledger_id": f"LED-{index:04d}",
        "gy_id": f"GY-{index:06d}",
        "name_en": name_en if name_en is not None else f"Example Person {index:02d}",
        "reviewer_note": note,
    }


def _merge_people() -> list[dict[str, str]]:
    """Three coded merges and twelve uncoded ones, one per survivor."""
    people = []
    for index, rule in enumerate(["E1", "E2", "X1+E3"] + ["hand"] * 10 + [""] * 2, start=1):
        note = f"merged LED-9{index:03d}; merge_evidence=fictitious evidence {index}"
        if rule:
            note += f"; rule={rule}"
        people.append(_person(index, note))
    return people


def test_census_coded_takes_every_coded_merge_and_draws_uncoded(tmp_path: Path):
    config = load(_ledger(tmp_path, _merge_people()))
    rows = sample_sheet(config, "people", 4, 7, tmp_path / "a.csv", design="census-coded")
    coded = [row for row in rows if row["stratum"] != "uncoded"]
    assert sorted(row["stratum"] for row in coded) == ["E1", "E2", "X1+E3"]
    assert sum(row["stratum"] == "uncoded" for row in rows) == 4
    again = sample_sheet(config, "people", 4, 7, tmp_path / "b.csv", design="census-coded")
    assert [row["item_id"] for row in again] == [row["item_id"] for row in rows]
    other = sample_sheet(config, "people", 4, 8, tmp_path / "c.csv", design="census-coded")
    assert {row["item_id"] for row in other} != {row["item_id"] for row in rows}
    everything = sample_sheet(config, "people", 50, 7, tmp_path / "d.csv", design="census-coded")
    assert len(everything) == 15


def test_census_design_is_people_only(tmp_path: Path):
    config = load(_ledger(tmp_path, _merge_people()))
    with pytest.raises(ValueError):
        sample_sheet(config, "cv", 4, 7, tmp_path / "x.csv", design="census-coded")


def test_attach_frame_samples_one_rule_with_other_rosters(tmp_path: Path):
    people = [_person(index) for index in range(1, 9)]
    memberships = []
    for index in range(1, 9):
        memberships.append({"ledger_id": f"LED-{index:04d}", "frame_code": "EXAMPLE-LAB-2020", "attach_rule": "first"})
        memberships.append(
            {"ledger_id": f"LED-{index:04d}", "frame_code": "EXAMPLE-FORUM-2022", "attach_rule": "A2" if index % 2 else "A1"}
        )
    config_path = _ledger(tmp_path, people, frame_membership=memberships)
    config = load(config_path)
    rows = sample_sheet(config, "attach", 3, 11, tmp_path / "a2.csv")
    assert len(rows) == 3
    assert {row["attach_rule"] for row in rows} == {"A2"}
    assert all(row["other_rosters"] == "EXAMPLE-LAB-2020 (first)" for row in rows)
    census = sample_sheet(config, "attach", 100, 11, tmp_path / "a2-all.csv")
    assert len(census) == 4
    assert len(sample_sheet(config, "attach", 100, 11, tmp_path / "a1.csv", rule="A1")) == 4

    out = tmp_path / "cli.csv"
    assert main(["sample", "attach", "--config", str(config_path), "--n", "2", "--seed", "11", "--out", str(out)]) == 0
    fields, sheet = read_sheet(out)
    for row in sheet:
        row["label"] = "correct"
    write_sheet(out, fields, sheet)
    assert score_sheet(out, "attach")["overall"]["correct"] == 2


def _linker_ledger(tmp_path: Path) -> Path:
    """Sixty fictitious people. Pairs 1/31 … 5/35 share a name and a site; 6/36 … 8/38 share only a name."""
    people = []
    links = []
    memberships = []
    for index in range(1, 61):
        twin = index - 30 if index > 30 else index
        name = f"Example Person {twin:02d}" if twin <= 8 else f"Example Person {index:02d}"
        people.append(_person(index, name_en=name))
        memberships.append(
            {"ledger_id": f"LED-{index:04d}", "frame_code": f"EXAMPLE-LAB-{2000 + index % 7}", "attach_rule": "first"}
        )
        if twin <= 5 or index % 3 == 0:
            site = f"https://example.org/{twin if twin <= 5 else index}/"
            links.append({"link_id": str(index), "ledger_id": f"LED-{index:04d}", "url": site, "link_type": "website"})
    return _ledger(tmp_path, people, links=links, frame_membership=memberships)


def test_linker_records_are_built_without_splink(tmp_path: Path):
    records = linker_records(load(_linker_ledger(tmp_path)))
    assert len(records) == 60
    first = records[0]
    assert first["name_en"] == "example person 01"
    assert first["websites"] == ["example.org/1"]
    assert first["editions"] == ["EXAMPLE-LAB-2001"]


@pytest.mark.skipif(importlib.util.find_spec("splink") is None, reason="the splink extra is not installed")
def test_splink_sheet_lists_pairs_the_rules_did_not_merge(tmp_path: Path):
    config_path = _linker_ledger(tmp_path)
    out = tmp_path / "splink.csv"
    assert main(["splink", "--config", str(config_path), "--out", str(out)]) == 0
    _fields, rows = read_sheet(out)
    pairs = {row["item_id"] for row in rows}
    for twin in range(1, 6):
        assert f"LED-{twin:04d}/LED-{twin + 30:04d}" in pairs
    assert all(float(row["match_probability"]) >= 0.9 for row in rows)
    strata = {row["item_id"]: row["stratum"] for row in rows}
    assert strata["LED-0001/LED-0031"] == "shared"
    assert {row["stratum"] for row in rows} <= {"shared", "name_only"}
