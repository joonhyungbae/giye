# SPDX-License-Identifier: AGPL-3.0-only
"""Disclosure scans: no identifiers, no cell under k, D3, D5 against roster_facts, hidden people."""

from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

import pytest

from giye.career.build import CareerBuildError, build
from giye.config import load
from giye.ledger.io import read_csv, write_csv
from giye.ledger.schemas import ACTIVITIES_FIELDS, ARTISTS_FIELDS, MEMBERSHIP_FIELDS, empty_row
from tests.career_synth import synthetic_archive

_GY = re.compile(r"^GY-\d{6}$")
_CAND = re.compile(r"^CAND-")
_CV = re.compile(r"^cv:")
_URL = re.compile(r"https?://", re.IGNORECASE)
_PS = re.compile(r"^ps[0-9a-f]{32}$")
_TABLES = (
    "reference_position.csv",
    "next_window.csv",
    "programme_profile.csv",
    "programme_entry.csv",
    "programme_transitions.csv",
    "field_trend.csv",
)
_BUNDLE = _TABLES + ("vocab.json", "manifest.json")


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _bad(value: str) -> bool:
    if _GY.fullmatch(value) or _PS.fullmatch(value):
        return True
    if _CAND.match(value) or _CV.match(value):
        return True
    return bool(_URL.search(value))


def test_identifiers_counts_and_share_gate(tmp_path: Path) -> None:
    config_path = synthetic_archive(tmp_path / "arch")
    out = tmp_path / "bundle"
    build(load(config_path), out)
    assert sorted(path.name for path in out.iterdir()) == sorted(_BUNDLE)
    for name in _TABLES:
        for row in _read(out / name):
            for column, value in row.items():
                assert not _bad(value), (name, column, value)
                if column == "n_people" and value not in {"suppressed", ">=90", ""}:
                    number = int(value)
                    assert number >= 10
                    assert number % 5 == 0
                count_columns = {"n_censored", "n_at_risk", "n_movers", "n_editions", "n_people_cv_layer"}
                if column in count_columns and value not in {"suppressed", ">=90", ""}:
                    assert int(value) % 5 == 0
            if "share" in row and row["share"] not in {"", "suppressed", ">=90"}:
                assert row["n_people"] not in {"suppressed", ""}
                assert int(row["n_people"]) >= 20
    vocab = json.loads((out / "vocab.json").read_text(encoding="utf-8"))
    assert "contract clauses" in vocab["not_covered"]


def test_concentration_bound_blanks_quantiles(tmp_path: Path) -> None:
    config_path = synthetic_archive(
        tmp_path / "arch",
        n_people=40,
        cv_rate=1,
        a1_rate=1,
        force_kind="group_exhibition",
        single_first_year=2010,
    )
    out = tmp_path / "bundle"
    build(load(config_path), out)
    rows = [
        row
        for row in _read(out / "reference_position.csv")
        if row["measure"] == "funding_share" and row["generation"] == "GEN-2010" and row["career_age"] == "0"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["bound"] == "yes"
    assert row["bound_value"] == "0"
    assert row["n_people"] != "suppressed"
    assert int(row["n_people"]) >= 20
    for name in ("q10", "q25", "q50", "q75", "q90"):
        assert row[name] == ""


def test_roster_facts_match_raises_d5(tmp_path: Path) -> None:
    config_path = synthetic_archive(tmp_path / "arch", n_people=6, seed=2)
    config = load(config_path)
    artists = {row["ledger_id"]: row["gy_id"] for row in read_csv(config.ledger / "artists.csv")}
    staff: set[tuple[str, str]] = set()
    for row in read_csv(config.ledger / "activities.csv"):
        if row.get("role"):
            staff.add((row["ledger_id"], row["origin"]))
    by_edition: dict[tuple[str, str], list[str]] = {}
    for row in read_csv(config.ledger / "frame_membership.csv"):
        if (row["ledger_id"], row["frame_code"]) in staff:
            continue
        programme, _dash, year = row["frame_code"].rpartition("-")
        by_edition.setdefault((programme, year), []).append(artists[row["ledger_id"]])
    programme, year = next(key for key, ids in by_edition.items() if 0 < len(ids) < 10)
    facts = tmp_path / "roster_facts.csv"
    with facts.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["gy_id", "programme", "year"])
        writer.writeheader()
        for gy_id in by_edition[(programme, year)]:
            writer.writerow({"gy_id": gy_id, "programme": programme, "year": year})
    with pytest.raises(CareerBuildError, match="D5"):
        build(load(config_path), tmp_path / "bundle", roster_facts=facts)


def test_hidden_person_is_not_read(tmp_path: Path) -> None:
    config_path = synthetic_archive(tmp_path / "arch", n_people=40, seed=3)
    config = load(config_path)
    plain = tmp_path / "plain"
    build(config, plain)

    hidden_root = tmp_path / "hidden"
    shutil.copytree(config_path.parent, hidden_root)
    hidden_config = load(hidden_root / "giye.toml")
    artists = read_csv(hidden_config.ledger / "artists.csv")
    artists.append(
        empty_row(
            ARTISTS_FIELDS,
            ledger_id="CAND-9999",
            gy_id="GY-999999",
            name_ko="Person 9999",
            name_en="Person 9999",
            status="HIDDEN_BY_REQUEST",
            source_url="https://example.org/p/9999",
            collected_at="2026-01-01",
        )
    )
    write_csv(path=hidden_config.ledger / "artists.csv", fields=ARTISTS_FIELDS, rows=artists)
    memberships = read_csv(hidden_config.ledger / "frame_membership.csv")
    memberships.append(
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id="CAND-9999",
            frame_code="EXAMPLE-RESIDENCY-2016",
            source_url="https://example.org/roster",
            collected_at="2026-01-01",
        )
    )
    write_csv(path=hidden_config.ledger / "frame_membership.csv", fields=MEMBERSHIP_FIELDS, rows=memberships)
    activities = read_csv(hidden_config.ledger / "activities.csv")
    activities.append(
        empty_row(
            ACTIVITIES_FIELDS,
            activity_id="act-hidden",
            ledger_id="CAND-9999",
            year="2016",
            role="",
            origin="EXAMPLE-RESIDENCY-2016",
            source_url="https://example.org/roster",
            collected_at="2026-01-01",
        )
    )
    write_csv(path=hidden_config.ledger / "activities.csv", fields=ACTIVITIES_FIELDS, rows=activities)
    processed = read_csv(hidden_config.processed / "activities.csv")
    fields = list(processed[0].keys()) if processed else []
    for year in range(1990, 2000):
        processed.append(
            {
                "activity_id": f"cv-hidden-{year}",
                "ledger_id": "CAND-9999",
                "year": str(year),
                "activity_type": "award",
                "publishable": "yes",
                "origin": "cv:hidden",
                "venue_country": "US",
                "venue_region": "",
                "venue_id": "V-0099",
                "funder_id": "V-0099",
                "venue_kind": "funder",
                "event_link": "",
                "activity_kind": "funding",
                "activity_channel": "activity",
            }
        )
    write_csv(path=hidden_config.processed / "activities.csv", fields=fields, rows=processed)
    attributes = read_csv(hidden_config.processed / "artist_attributes.csv")
    attr_fields = list(attributes[0].keys())
    attributes.append(
        {
            "ledger_id": "CAND-9999",
            "field": "active_since",
            "value": "1990",
            "rule": "A1",
            "evidence": "",
            "evidence_url": "",
        }
    )
    write_csv(path=hidden_config.processed / "artist_attributes.csv", fields=attr_fields, rows=attributes)

    hidden_out = tmp_path / "hidden-bundle"
    build(hidden_config, hidden_out)
    for name in _TABLES:
        assert (plain / name).read_bytes() == (hidden_out / name).read_bytes()
    assert (plain / "vocab.json").read_bytes() == (hidden_out / "vocab.json").read_bytes()
    assert json.loads((hidden_out / "manifest.json").read_text(encoding="utf-8"))["counts"]["published"] == json.loads(
        (plain / "manifest.json").read_text(encoding="utf-8")
    )["counts"]["published"]
