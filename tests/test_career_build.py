# SPDX-License-Identifier: AGPL-3.0-only
"""The career bundle builds on a synthetic archive and the columns match the spec."""

from __future__ import annotations

import csv
from pathlib import Path

from giye.career.build import build
from giye.config import load
from tests.career_synth import synthetic_archive

REFERENCE = [
    "career_age",
    "generation",
    "measure",
    "n_people",
    "q10",
    "q25",
    "q50",
    "q75",
    "q90",
    "w_q10",
    "w_q25",
    "w_q50",
    "w_q75",
    "w_q90",
    "bound",
    "bound_value",
]
NEXT = [
    "age_band",
    "generation",
    "mix_bucket",
    "outcome",
    "n_people",
    "n_censored",
    "share",
    "share_lo",
    "share_hi",
    "w_share",
    "bound",
    "bound_value",
]
PROFILE = [
    "programme",
    "name_en",
    "name_ko",
    "access_mode",
    "first_year",
    "last_year",
    "n_editions",
    "n_people",
    "edition_size_q25",
    "edition_size_q50",
    "edition_size_q75",
    "returners_share",
    "team_share",
    "n_people_cv_layer",
]
ENTRY = [
    "programme",
    "measure",
    "n_people",
    "q10",
    "q25",
    "q50",
    "q75",
    "q90",
    "w_q10",
    "w_q25",
    "w_q50",
    "w_q75",
    "w_q90",
    "bound",
    "bound_value",
]
TRANSITION = [
    "programme_from",
    "programme_to",
    "n_at_risk",
    "n_movers",
    "gap_q25",
    "gap_q50",
    "gap_q75",
    "n_censored",
]
TREND = [
    "generation",
    "measure",
    "n_people",
    "mean",
    "mean_lo",
    "mean_hi",
    "w_mean",
    "w_mean_lo",
    "w_mean_hi",
    "bound",
    "bound_value",
]
FILES = {
    "reference_position.csv": REFERENCE,
    "next_window.csv": NEXT,
    "programme_profile.csv": PROFILE,
    "programme_entry.csv": ENTRY,
    "programme_transitions.csv": TRANSITION,
    "field_trend.csv": TREND,
    "vocab.json": None,
    "manifest.json": None,
}


def _rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _numeric(value: str) -> bool:
    return value not in {"", "suppressed", ">=90"}


def _places(value: str) -> int:
    """Decimal places in a formatted number. ``0`` and ``0.37`` are 0 and 2."""
    if "." not in value:
        return 0
    return len(value.split(".", 1)[1])


def test_bundle_shape_and_ranges(tmp_path: Path) -> None:
    config_path = synthetic_archive(tmp_path / "arch")
    out = tmp_path / "bundle"
    manifest = build(load(config_path), out)
    assert set(FILES) == {path.name for path in out.iterdir()}
    tables = {name: _rows(out / name) for name in FILES if name.endswith(".csv")}
    for name, columns in FILES.items():
        if columns is None:
            continue
        header, rows = tables[name]
        assert header == columns
        assert rows

    header, reference = tables["reference_position.csv"]
    del header
    ages = {row["career_age"] for row in reference}
    generations = {row["generation"] for row in reference}
    measures = {row["measure"] for row in reference}
    assert ages == {str(age) for age in range(31)}
    assert len(reference) == 31 * len(generations) * len(measures)

    large = [
        row
        for row in reference
        if _numeric(row["n_people"]) and int(row["n_people"]) >= 20 and row["bound"] != "yes"
    ]
    small = [
        row
        for row in reference
        if _numeric(row["n_people"]) and 10 <= int(row["n_people"]) < 20 and row["bound"] != "yes"
    ]
    assert large and any(row["q50"] for row in large)
    assert small and all(row["q50"] == "" for row in small)
    for row in reference:
        if row["measure"] != "institutions_per_year":
            continue
        for name in ("q10", "q25", "q50", "q75", "q90"):
            if row[name]:
                assert float(row[name]) <= 120

    _header, window = tables["next_window.csv"]
    for row in window:
        if not _numeric(row["share"]):
            continue
        share, low, high = float(row["share"]), float(row["share_lo"]), float(row["share_hi"])
        assert 0 <= share <= 1
        assert low <= share <= high

    _header, transitions = tables["programme_transitions.csv"]
    pairs = {(row["programme_from"], row["programme_to"]) for row in transitions}
    assert ("EXAMPLE-RESIDENCY", "EXAMPLE-WORKSHOP") in pairs
    assert ("EXAMPLE-WORKSHOP", "EXAMPLE-RESIDENCY") in pairs

    _header, trend = tables["field_trend.csv"]
    seen_interval = False
    for row in trend:
        if not _numeric(row["mean"]):
            continue
        mean, low, high = float(row["mean"]), float(row["mean_lo"]), float(row["mean_hi"])
        assert low <= mean <= high
        seen_interval = True
    assert seen_interval
    assert manifest["weights"] == "none"
    for name in ("reference_position.csv", "next_window.csv", "programme_entry.csv", "field_trend.csv"):
        columns = manifest["tables"][name]["columns"]
        assert columns[columns.index("bound") + 1] == "bound_value"
    _header, entry = tables["programme_entry.csv"]
    for row in reference:
        for name in ("q10", "q25", "q50", "q75", "q90"):
            if _numeric(row[name]):
                assert _places(row[name]) <= 2
        if row["bound"] == "yes":
            assert row["bound_value"] != ""
            assert _places(row["bound_value"]) <= 2
        else:
            assert row["bound_value"] == ""
    for row in window:
        for name in ("share", "share_lo", "share_hi"):
            if _numeric(row[name]):
                assert _places(row[name]) <= 2
    for row in trend:
        for name in ("mean", "mean_lo", "mean_hi"):
            if _numeric(row[name]):
                assert _places(row[name]) <= 2
    for row in entry:
        limit = 1 if row["measure"] == "career_age_at_entry" else 2
        for name in ("q10", "q25", "q50", "q75", "q90"):
            if _numeric(row[name]):
                assert _places(row[name]) <= limit
    for row in transitions:
        for name in ("gap_q25", "gap_q50", "gap_q75"):
            if _numeric(row[name]):
                assert _places(row[name]) <= 1
    _header, profile = tables["programme_profile.csv"]
    for row in profile:
        for name in ("returners_share", "team_share"):
            if _numeric(row[name]):
                assert _places(row[name]) <= 2
    for _header, rows in tables.values():
        for row in rows:
            for key, value in row.items():
                if key.startswith("w_"):
                    assert value == ""


def test_weights_fill_and_shift_a_median(tmp_path: Path) -> None:
    config_path = synthetic_archive(tmp_path / "arch")
    config = load(config_path)
    plain = tmp_path / "plain"
    build(config, plain)
    weights = tmp_path / "weights.csv"
    with weights.open("w", encoding="utf-8", newline="") as handle:
        handle.write("ledger_id,weight\n")
        # Even CAND numbers are the overseas-funding careers. Upweighting them
        # moves a median off the unweighted one.
        for number in range(1, 401):
            weight = 100 if number % 2 == 0 else 0.001
            handle.write(f"CAND-{number:04d},{weight}\n")
    weighted_out = tmp_path / "weighted"
    manifest = build(config, weighted_out, weights=weights)
    assert manifest["weights"] != "none"
    _header, rows = _rows(weighted_out / "reference_position.csv")
    assert any(row["w_q50"] for row in rows)
    assert any(row["w_q50"] and row["q50"] and row["w_q50"] != row["q50"] for row in rows)


def test_smaller_current_year_increases_censoring(tmp_path: Path) -> None:
    config_path = synthetic_archive(tmp_path / "arch")
    config = load(config_path)
    later = tmp_path / "later"
    earlier = tmp_path / "earlier"
    build(config, later, current_year=2026)
    build(config, earlier, current_year=2005)

    def censored(path: Path) -> int:
        _header, rows = _rows(path / "next_window.csv")
        seen: dict[tuple[str, str, str], int] = {}
        for row in rows:
            if not _numeric(row["n_censored"]):
                continue
            seen[(row["age_band"], row["generation"], row["mix_bucket"])] = int(row["n_censored"])
        return sum(seen.values())

    assert censored(earlier) > censored(later)
