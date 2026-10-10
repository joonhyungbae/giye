# SPDX-License-Identifier: AGPL-3.0-only
"""Holdout of next_window: the command, the split-year guard, and who the realisation counts."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from giye.career.__main__ import main
from giye.career.build import _next_window, _scan_json
from giye.career.holdout import too_late_message
from giye.career.measures import CvRow
from giye.career.population import Person, default_current_year, generation_label, load_population
from giye.career.rules import PCT_MIN, K
from giye.config import load
from tests.career_synth import synthetic_archive

_GY = re.compile(r"^GY-\d{6}$")
_CAND = re.compile(r"^CAND-")
_CV = re.compile(r"^cv:")
_URL = re.compile(r"https?://", re.IGNORECASE)
_PS = re.compile(r"^ps[0-9a-f]{32}$")
_KEYS = (
    "n_cells",
    "mean_absolute_difference",
    "median_absolute_difference",
    "coverage",
    "by_age_band",
    "by_outcome_family",
    "n_cells_prediction_only",
    "n_cells_realisation_only",
)
_THREE = ("mean_absolute_difference", "median_absolute_difference", "coverage")


def _bad(value: str) -> bool:
    if _GY.fullmatch(value) or _PS.fullmatch(value):
        return True
    if _CAND.match(value) or _CV.match(value):
        return True
    return bool(_URL.search(value))


def _person_counts(value: object) -> list[int]:
    """Published person counts. ``n_cells`` counts cells, not people."""
    found: list[int] = []

    def walk(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key.startswith("n_people"):
                    found.append(int(child))
                else:
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def _assert_clean(path: Path) -> None:
    for token in path.read_text(encoding="utf-8").split():
        cleaned = token.strip("|*_`[]()<>.,;:\"'")
        assert not _bad(cleaned), cleaned


def test_holdout_command_on_synthetic_archive(tmp_path: Path) -> None:
    config_path = synthetic_archive(tmp_path / "arch")
    out = tmp_path / "holdout"
    code = main(["holdout", "--config", str(config_path), "--split-year", "2020", "--out", str(out)])
    assert code == 0
    document = json.loads((out / "holdout.json").read_text(encoding="utf-8"))
    for key in _KEYS:
        assert key in document
    assert 0 <= document["coverage"] <= 1
    assert document["mean_absolute_difference"] >= 0
    assert document["median_absolute_difference"] >= 0
    groups = list(document["by_age_band"].values()) + list(document["by_outcome_family"].values())
    for group in groups:
        for key in _THREE:
            assert key in group
        assert 0 <= group["coverage"] <= 1
    assert sum(group["n_cells"] for group in document["by_age_band"].values()) == document["n_cells"]
    assert sum(group["n_cells"] for group in document["by_outcome_family"].values()) == document["n_cells"]
    k = document["k"]
    for number in _person_counts(document):
        assert number >= k
        assert number % 5 == 0
    _scan_json(out / "holdout.json")
    _assert_clean(out / "holdout.json")
    _assert_clean(out / "holdout.md")


def test_split_year_too_late_exits_2(tmp_path: Path, capsys) -> None:
    config_path = synthetic_archive(tmp_path / "arch", n_people=30, seed=1)
    current = default_current_year(load(config_path), datetime.now(timezone.utc).year)
    split_year = current - 2
    out = tmp_path / "holdout"
    code = main(["holdout", "--config", str(config_path), "--split-year", str(split_year), "--out", str(out)])
    assert code == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert err == [too_late_message(split_year, current)]


def test_realisation_ignores_a_window_closed_before_t(tmp_path: Path) -> None:
    config = load(synthetic_archive(tmp_path / "arch"))
    population = load_population(config, None, k=K)
    split_year = 2020
    before = _next_window(population, K, PCT_MIN, split_year + 3, False, opened_after=split_year)[0]
    # Every band's window ends by 2014, which is before T. This career was
    # already in the prediction and must not move a realised cell.
    early = Person(
        ledger_id="CAND-9000",
        first_year=1990,
        generation=generation_label(1990),
        weight=1.0,
        has_weight=False,
        team=False,
        editions=(),
        rows=(
            CvRow(year=1990, kind="funding", country="US", region="", venue_id="V-0001", venue_kind="funder"),
            CvRow(year=1991, kind="funding", country="US", region="", venue_id="V-0001", venue_kind="funder"),
            CvRow(year=2010, kind="funding", country="US", region="", venue_id="V-0001", venue_kind="funder"),
        ),
        cv=True,
    )
    assert early.generation not in {row["generation"] for row in before}
    population.cv_ready = [*population.cv_ready, early]
    after = _next_window(population, K, PCT_MIN, split_year + 3, False, opened_after=split_year)[0]
    assert after == before
