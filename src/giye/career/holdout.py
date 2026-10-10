# SPDX-License-Identifier: AGPL-3.0-only
"""Holdout check of the next-window base rates.

What: builds ``next_window`` twice from one population and writes
``holdout.json`` and ``holdout.md``. The prediction censors every career as
of year T. The realisation uses ``current_year = T + 3`` and only the windows
that were still closed at T.

Why: calibration is a property of the base rates, not a forecast for one
person. The holdout shows whether the base rates seen at T describe what
followed for careers that reached the window after T.

How to run (the venv interpreter):

  .venv/bin/python -m giye.career holdout --config <giye.toml> --split-year T --out <directory>

Method, the one the paper reports:

1. Load the population once. The prediction is ``next_window`` with
   ``current_year = T``. A person contributes to a band only when
   ``first_year + band start + 3 <= T``.
2. The realisation is the same table with ``current_year = T + 3``, restricted
   to windows that were censored at T and not at T + 3. Those windows,
   ``(first_year + band start + 1 .. first_year + band start + 3)``, end in
   ``(T, T + 3]``. Rows after the archive's current year do not exist, so
   ``T + 3 <=`` that year is required; otherwise the command exits 2.
3. For every cell ``(age_band, generation, mix_bucket, outcome)`` published in
   both tables (a numeric share, not suppressed, published ``n`` at least
   ``pct_min``), compare the predicted share with the realised share. Report
   ``n_cells``, the mean absolute difference, the median absolute difference,
   and ``coverage`` (the share of those cells whose realised value lies inside
   the predicted Wilson interval). The same three numbers are reported per age
   band and per outcome family. ``n_cells_prediction_only`` counts cells
   published at T and suppressed in the realisation; ``n_cells_realisation_only``
   is the converse.
4. The output holds only cell-level aggregates that already passed D1–D4 in
   both tables. It carries no person count below ``k`` and no identifier. The
   phase 1 scan runs on both tables and on the written files.

An outcome family is the C3f family of a ``kind:`` or ``family:`` outcome
(``kind:solo_exhibition`` and ``family:exhibition`` are both ``exhibition``).
``country:`` outcomes are the family ``country``. ``arttech`` and ``funding``
are their own families. The compared share is the unweighted share the Wilson
interval belongs to. A weights file still fills ``w_share`` inside the tables
the scan sees; this check does not read that column. The seed is recorded and
is not used, because the check does not resample.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from giye.career.build import (
    NEXT_COLUMNS,
    CareerBuildError,
    _bad_value,
    _next_window,
    _read_weights,
    _scan_json,
    _scan_table,
)
from giye.career.population import default_current_year, load_population
from giye.career.rules import AGE_BANDS, FAMILY_ORDER, KIND_FAMILY, PCT_MIN, K
from giye.config import Config
from giye.export.release import BOUND, SUPPRESSED

_NOT_A_SHARE = frozenset({SUPPRESSED, BOUND, ""})


class HoldoutError(Exception):
    """The split cannot be checked. The message is one line."""


def too_late_message(split_year: int, current_year: int) -> str:
    """One line. ``T + 3`` has to be a year the archive already contains."""
    return f"holdout: T + 3 = {split_year + 3} is after the archive current year {current_year}"


def _outcome_family(outcome: str) -> str:
    """The family a compared cell is counted under. See the module docstring."""
    if outcome.startswith("kind:"):
        return KIND_FAMILY.get(outcome.split(":", 1)[1], "other")
    if outcome.startswith("family:"):
        return outcome.split(":", 1)[1]
    if outcome.startswith("country:"):
        return "country"
    return outcome


def _published(row: dict | None, pct_min: int) -> bool:
    """True when the cell publishes a numeric share for at least ``pct_min`` people.

    The count is the rounded ``n_people`` already written by D4. A bound or a
    suppressed share is not a number the interval can be checked against.
    """
    if row is None:
        return False
    n_text = row.get("n_people") or ""
    share = row.get("share") or ""
    low = row.get("share_lo") or ""
    high = row.get("share_hi") or ""
    if row.get("bound") == "yes":
        return False
    if n_text in _NOT_A_SHARE or share in _NOT_A_SHARE or low in _NOT_A_SHARE or high in _NOT_A_SHARE:
        return False
    try:
        n_people = int(n_text)
        float(share)
        float(low)
        float(high)
    except ValueError:
        return False
    return n_people >= pct_min


def _four(value: float) -> float:
    """Four decimal places, as a float JSON can dump without a binary tail."""
    return float(f"{value:.4f}")


def _metrics(pairs: list[tuple[float, bool]]) -> dict[str, float | int]:
    """``n_cells``, mean and median absolute difference, and Wilson coverage.

    With no cells the coverage is 1: there is no realised value outside the
    predicted interval. The differences are 0.
    """
    n_cells = len(pairs)
    if n_cells == 0:
        return {
            "n_cells": 0,
            "mean_absolute_difference": 0.0,
            "median_absolute_difference": 0.0,
            "coverage": 1.0,
        }
    diffs = [diff for diff, _inside in pairs]
    covered = sum(1 for _diff, inside in pairs if inside)
    return {
        "n_cells": n_cells,
        "mean_absolute_difference": _four(statistics.mean(diffs)),
        "median_absolute_difference": _four(statistics.median(diffs)),
        "coverage": _four(covered / n_cells),
    }


def _index(rows: list[dict]) -> dict[tuple[str, str, str, str], dict]:
    return {(row["age_band"], row["generation"], row["mix_bucket"], row["outcome"]): row for row in rows}


def _compare(
    prediction: list[dict],
    realisation: list[dict],
    pct_min: int,
) -> tuple[dict[str, float | int], dict[str, dict], dict[str, dict], int, int, list[dict]]:
    """Cell-level comparison. Returns overall metrics, the two breakdowns, the only-counts, and cells."""
    predicted = _index(prediction)
    realised = _index(realisation)
    band_order = {label: index for index, (_start, _end, label) in enumerate(AGE_BANDS)}
    family_rank = {name: index for index, name in enumerate(FAMILY_ORDER)}
    by_band: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    by_family: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    cells: list[dict] = []
    prediction_only = 0
    realisation_only = 0
    for key in sorted(set(predicted) | set(realised), key=lambda item: (band_order.get(item[0], 99), item)):
        pred = predicted.get(key)
        real = realised.get(key)
        pred_ok = _published(pred, pct_min)
        real_ok = _published(real, pct_min)
        if pred_ok and not real_ok:
            prediction_only += 1
            continue
        if real_ok and not pred_ok:
            realisation_only += 1
            continue
        if not pred_ok or not real_ok or pred is None or real is None:
            continue
        share = float(pred["share"])
        low = float(pred["share_lo"])
        high = float(pred["share_hi"])
        observed = float(real["share"])
        difference = abs(share - observed)
        inside = low <= observed <= high
        age_band, generation, mix_bucket, outcome = key
        family = _outcome_family(outcome)
        pair = (difference, inside)
        by_band[age_band].append(pair)
        by_family[family].append(pair)
        cells.append(
            {
                "age_band": age_band,
                "generation": generation,
                "mix_bucket": mix_bucket,
                "outcome": outcome,
                "outcome_family": family,
                "n_people_prediction": int(pred["n_people"]),
                "n_people_realisation": int(real["n_people"]),
                "share_prediction": share,
                "share_realisation": observed,
                "share_lo": low,
                "share_hi": high,
                "absolute_difference": _four(difference),
                "inside_interval": inside,
            }
        )
    ordered_bands = sorted(by_band, key=lambda name: band_order.get(name, 99))
    band_metrics = {label: _metrics(by_band[label]) for label in ordered_bands}
    family_metrics = {
        name: _metrics(by_family[name])
        for name in sorted(by_family, key=lambda name: (name not in family_rank, family_rank.get(name, 0), name))
    }
    pairs = [pair for label in by_band for pair in by_band[label]]
    return _metrics(pairs), band_metrics, family_metrics, prediction_only, realisation_only, cells


def _num(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text not in {"", "-0"} else "0"


def _render(document: dict) -> str:
    """The same numbers as the JSON, in a short table."""

    def line(label: str, block: dict) -> str:
        return (
            f"| {label} | {block['n_cells']} | {_num(block['mean_absolute_difference'])} "
            f"| {_num(block['median_absolute_difference'])} | {_num(block['coverage'])} |"
        )

    lines = [
        "# Holdout of next_window",
        "",
        (
            f"Split year T = {document['split_year']}. "
            f"Realisation year = {document['realisation_year']}. "
            f"Archive current year = {document['current_year']}."
        ),
        "",
        (
            "Calibration is a property of the base rates, not a forecast for one person. "
            "The holdout shows whether the base rates seen at T describe what followed "
            "for careers that reached the window after T."
        ),
        "",
        "| group | n_cells | mean absolute difference | median absolute difference | coverage |",
        "|---|---:|---:|---:|---:|",
        line("all", document),
    ]
    if document["n_cells"] == 0:
        lines.append("")
        lines.append("No cell was published in both tables. Coverage is 1 because nothing fell outside an interval.")
    lines.extend(["", "## By age band", ""])
    if document["by_age_band"]:
        lines.append("| age band | n_cells | mean absolute difference | median absolute difference | coverage |")
        lines.append("|---|---:|---:|---:|---:|")
        for label, block in document["by_age_band"].items():
            lines.append(line(label, block))
    else:
        lines.append("No age band had a cell published in both tables.")
    lines.extend(["", "## By outcome family", ""])
    if document["by_outcome_family"]:
        lines.append("| outcome family | n_cells | mean absolute difference | median absolute difference | coverage |")
        lines.append("|---|---:|---:|---:|---:|")
        for label, block in document["by_outcome_family"].items():
            lines.append(line(label, block))
    else:
        lines.append("No outcome family had a cell published in both tables.")
    lines.extend(
        [
            "",
            f"Cells published only in the prediction: {document['n_cells_prediction_only']}.",
            f"Cells published only in the realisation: {document['n_cells_realisation_only']}.",
            "",
        ]
    )
    return "\n".join(lines)


def _scan_prose(path: Path) -> None:
    """Identifier scan for the markdown. Same tokens the table scan refuses."""
    for token in path.read_text(encoding="utf-8").split():
        cleaned = token.strip("|*_`[]()<>.,;:\"'")
        if cleaned and _bad_value(cleaned):
            raise CareerBuildError(f"D5: an aggregate value could hold an identifier in {path.name}")


def holdout(
    config: Config,
    out: str | Path,
    split_year: int,
    *,
    weights: str | Path | None = None,
    k: int = K,
    pct_min: int = PCT_MIN,
    seed: int = 20261010,
) -> dict:
    """Write the holdout report and return it.

    Raises ``HoldoutError`` when ``T + 3`` is after the archive's current year.
    Raises ``CareerBuildError`` when a table or the report fails the phase 1 scan.
    """
    weight_path = Path(weights) if weights else None
    weight_map = _read_weights(weight_path) if weight_path else None
    population = load_population(config, weight_map, k=k)
    current_year = default_current_year(config, datetime.now(timezone.utc).year)
    if split_year + 3 > current_year:
        raise HoldoutError(too_late_message(split_year, current_year))
    weighted = weight_map is not None
    prediction = _next_window(population, k, pct_min, split_year, weighted)[0]
    realisation = _next_window(population, k, pct_min, split_year + 3, weighted, opened_after=split_year)[0]
    _scan_table("next_window_prediction", NEXT_COLUMNS, prediction, k)
    _scan_table("next_window_realisation", NEXT_COLUMNS, realisation, k)
    overall, by_band, by_family, prediction_only, realisation_only, cells = _compare(prediction, realisation, pct_min)
    document: dict = {
        "split_year": split_year,
        "current_year": current_year,
        "realisation_year": split_year + 3,
        "k": k,
        "pct_min": pct_min,
        "seed": seed,
        **overall,
        "by_age_band": by_band,
        "by_outcome_family": by_family,
        "n_cells_prediction_only": prediction_only,
        "n_cells_realisation_only": realisation_only,
        "cells": cells,
    }
    destination = Path(out)
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "holdout.json"
    md_path = destination / "holdout.md"
    json_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_render(document), encoding="utf-8")
    _scan_json(json_path)
    _scan_prose(md_path)
    return document
