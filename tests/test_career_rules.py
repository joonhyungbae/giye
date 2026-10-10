# SPDX-License-Identifier: AGPL-3.0-only
"""Unit tests for career rules C1, C2, C4, C5, C8, C9, C10, C12, D2 and PCT_MIN."""

from __future__ import annotations

import csv
from pathlib import Path

from giye.career.build import build, gate_share
from giye.career.measures import arttech_venue_ids, career_age_at_entry, country_group, mix_bucket
from giye.career.population import generation_label
from giye.career.rules import reached_age, window_open
from giye.config import load
from giye.export.release import quantile_allowed
from giye.ledger.io import read_csv, write_csv
from tests.career_synth import synthetic_archive


def test_missing_a1_is_excluded_and_counted(tmp_path: Path) -> None:
    config_path = synthetic_archive(tmp_path / "arch", n_people=30, seed=4, cv_rate=1, a1_rate=1)
    config = load(config_path)
    attributes = read_csv(config.processed / "artist_attributes.csv")
    kept = []
    for row in attributes:
        active = row.get("field") == "active_since" and row["ledger_id"].endswith(("1", "2", "3"))
        if not active:
            kept.append(row)
    write_csv(
        path=config.processed / "artist_attributes.csv",
        fields=list(attributes[0].keys()),
        rows=kept,
    )
    dropped = {
        row["ledger_id"]
        for row in attributes
        if row.get("field") == "active_since" and row["ledger_id"].endswith(("1", "2", "3"))
    }
    manifest = build(config, tmp_path / "bundle")
    assert manifest["counts"]["cv_no_first_year"] == len(dropped)
    assert manifest["counts"]["cv_no_first_year"] >= 3
    ready = manifest["counts"]["cv_layer"] - manifest["counts"]["cv_no_first_year"]
    assert ready == manifest["counts"]["cv_layer"] - len(dropped)


def test_generation_bins() -> None:
    assert generation_label(1995) == "GEN-1995"
    assert generation_label(1999) == "GEN-1995"
    assert generation_label(2000) == "GEN-2000"
    assert generation_label(2004) == "GEN-2000"
    assert generation_label(2005) == "GEN-2005"


def test_country_groups_and_empty_territory() -> None:
    assert country_group("KR", "KR") == "home"
    assert country_group("us", "KR") == "abroad"
    assert country_group("", "KR") == "unresolved"
    assert country_group("KR", "") == "abroad"
    assert country_group("US", "") == "abroad"
    assert country_group("", "") == "unresolved"


def test_arttech_set_includes_event_and_operator_only() -> None:
    rows = [
        {"venue_id": "V-0001", "event_link": "EXAMPLE-RESIDENCY-2019"},
        {"venue_id": "V-0002", "event_link": ""},
        {"venue_id": "V-0003", "event_link": ""},
    ]
    venues = [
        {"venue_id": "V-0001", "name": "Venue 0001", "aliases": ""},
        {"venue_id": "V-0002", "name": "Example Operator Hall", "aliases": "Hall Alias"},
        {"venue_id": "V-0003", "name": "Plain Hall", "aliases": "Other"},
        {"venue_id": "V-0004", "name": "Unused", "aliases": "Example Operator Hall"},
    ]
    found = arttech_venue_ids(rows, venues, {"Example Operator Hall"})
    assert "V-0001" in found
    assert "V-0002" in found
    assert "V-0004" in found
    assert "V-0003" not in found


def test_entry_age_clamps_and_is_counted(tmp_path: Path) -> None:
    assert career_age_at_entry(2010, 2012) == (0, True)
    assert career_age_at_entry(2012, 2010) == (2, False)
    config_path = synthetic_archive(tmp_path / "arch", n_people=20, seed=5, cv_rate=1, a1_rate=1)
    manifest = build(load(config_path), tmp_path / "bundle")
    assert manifest["counts"]["entry_before_first_year"] >= 1


def test_mix_bucket_tie_mixed_and_none() -> None:
    families = {
        "exhibition": 0.4,
        "screening": 0.4,
        "performance": 0.0,
        "practice_other": 0.0,
        "discourse": 0.2,
        "support": 0.0,
        "private": 0.0,
    }
    # Equal leaders: the earlier family in the fixed order wins only when it is at least half.
    # 0.4 is below half, so the bucket is mixed. A tie at 0.6 keeps exhibition.
    assert mix_bucket(families, n_rows=10) == "mixed"
    tied = dict(families)
    tied["exhibition"] = 0.6
    tied["screening"] = 0.6
    tied["discourse"] = 0.0
    assert mix_bucket(tied, n_rows=10) == "exhibition"
    assert mix_bucket({name: 0.0 for name in families}, n_rows=0) == "none"


def test_censoring_boundary() -> None:
    assert reached_age(2000, 10, 2010) is True
    assert reached_age(2000, 11, 2010) is False
    assert window_open(2000, 10, 2013) is True
    assert window_open(2000, 10, 2012) is False


def test_empty_window_is_left_out_of_the_cell(tmp_path: Path) -> None:
    """C12. A first CV row three years after A1 is absent from ages 0–2 and present at 3.

    Twenty people share that career so the age-3 cell can clear ``k``. One such
    person would round away inside ``n_people``.
    """
    config_path = synthetic_archive(
        tmp_path / "arch",
        n_people=20,
        seed=7,
        cv_rate=1,
        a1_rate=1,
        single_first_year=2010,
    )
    config = load(config_path)
    path = config.processed / "activities.csv"
    rows = read_csv(path)
    for row in rows:
        if (row.get("origin") or "").startswith("cv:"):
            row["year"] = "2013"
    write_csv(path=path, fields=list(rows[0].keys()), rows=rows)
    out = tmp_path / "bundle"
    manifest = build(config, out, current_year=2026)
    assert manifest["counts"]["no_rows_in_window"]["reference_position"] >= 1
    with (out / "reference_position.csv").open(encoding="utf-8", newline="") as handle:
        table = [
            row
            for row in csv.DictReader(handle)
            if row["generation"] == "GEN-2010" and row["measure"] == "funding_share"
        ]
    by_age = {row["career_age"]: row["n_people"] for row in table}
    assert by_age["0"] == "suppressed"
    assert by_age["1"] == "suppressed"
    assert by_age["2"] == "suppressed"
    assert by_age["3"] == "20"


def test_quantile_thresholds_and_pct_min() -> None:
    assert quantile_allowed(19, 0.50, 10) is False
    assert quantile_allowed(20, 0.50, 10) is True
    assert quantile_allowed(39, 0.25, 10) is False
    assert quantile_allowed(40, 0.25, 10) is True
    assert quantile_allowed(39, 0.75, 10) is False
    assert quantile_allowed(40, 0.75, 10) is True
    assert quantile_allowed(99, 0.10, 10) is False
    assert quantile_allowed(100, 0.10, 10) is True
    assert gate_share(19, 20, "0.5") == "suppressed"
    assert gate_share(20, 20, "0.5") == "0.5"
