# SPDX-License-Identifier: AGPL-3.0-only
"""Precision of an audit sheet, with a Wilson 95% score interval.

``correct`` is a success. ``incorrect`` is a failure. ``cannot tell`` is
counted and left out of the primary denominator. The conservative interval
puts those rows back in as failures. A blank label is not judged yet and is
in neither denominator. Any other text is refused with the row and the label
(``UnknownLabel``): left out silently, a typo such as ``wrong`` would shrink
the denominator and raise the precision.

With stratum weights (``--weights``: each stratum's population size) the
report adds a stratum-weighted estimate (:func:`weighted_estimate`).

The interval uses the fixed normal quantile ``z = 1.959963984540054`` (the
0.975 point of the standard normal). No statistics library is imported, so
the same counts always print the same bounds.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from giye.audit.sheet import LABELS, read_sheet

# normsinv(0.975), fixed. A later SciPy would print the same digits.
WILSON_Z = 1.959963984540054


class UnknownLabel(ValueError):
    """A sheet row whose label is not blank and not one of ``LABELS``."""


def score_sheet(path: Path, kind: str, weights: Mapping[str, float] | None = None) -> dict:
    """Score ``path``. ``kind`` has to match the sheet. ``weights`` adds the weighted estimate."""
    _fields, rows = read_sheet(path)
    kinds = {row.get("kind") or "" for row in rows}
    kinds.discard("")
    if kinds and kinds != {kind}:
        raise ValueError(f"sheet kind is {sorted(kinds)}, not {kind}")
    return score_rows(rows, kind, weights)


def score_rows(rows: list[dict[str, str]], kind: str, weights: Mapping[str, float] | None = None) -> dict:
    """Precision and Wilson bounds for ``rows``, overall and by stratum. ``kind`` is recorded on the result."""
    for number, row in enumerate(rows, start=2):  # line 1 of the sheet is the header
        label = (row.get("label") or "").strip()
        if label and label not in LABELS:
            item = row.get("item_id") or row.get("sample_id") or ""
            raise UnknownLabel(
                f"sheet line {number}{f' ({item})' if item else ''}: label {label!r} is not one of "
                f"{', '.join(repr(name) for name in LABELS)} or blank"
            )
    by_stratum: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_stratum.setdefault(row.get("stratum") or "", []).append(row)
    strata = {name: _summarise(group) for name, group in sorted(by_stratum.items())}
    report = {"kind": kind, "z": WILSON_Z, "overall": _summarise(rows), "strata": strata}
    if weights is not None:
        sizes = {name: len(group) for name, group in by_stratum.items()}
        report["weighted"] = weighted_estimate(strata, sizes, weights)
    return report


def read_weights(path: Path) -> dict[str, float]:
    """Stratum weights from a JSON object or a CSV with columns ``stratum`` and ``weight`` (or ``size``)."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"{path}: weights must be a JSON object of stratum to weight")
        pairs = list(data.items())
    else:
        reader = csv.DictReader(text.splitlines())
        column = next((name for name in ("weight", "size") if name in (reader.fieldnames or [])), None)
        if "stratum" not in (reader.fieldnames or []) or column is None:
            raise ValueError(f"{path}: weights CSV needs columns stratum and weight (or size)")
        pairs = [(row["stratum"], row[column]) for row in reader]
    weights: dict[str, float] = {}
    for name, value in pairs:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{path}: weight of stratum {name!r} is not a number: {value!r}") from None
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{path}: weight of stratum {name!r} must be a finite number >= 0")
        weights[str(name)] = number
    return weights


def weighted_estimate(
    strata: Mapping[str, dict], sheet_sizes: Mapping[str, int], weights: Mapping[str, float]
) -> dict:
    """Stratum-weighted precision: ``sum W_s p_s`` with ``W_s = w_s / sum w``, and its bounds.

    ``w_s`` is the stratum's population size (any positive scale works for
    the estimate). The bounds are ``sum W_s low_s`` and ``sum W_s high_s``:
    a sampled stratum contributes its Wilson bounds, and a census stratum
    (the sheet holds at least ``w_s`` rows, every member of the stratum)
    has no sampling error and contributes its precision to both. Summing
    per-stratum 95% bounds is conservative compared with a pooled variance,
    and it does not collapse to a zero-width interval when a sampled stratum
    is all correct, as a normal approximation would. The same is done for
    the conservative denominator (``cannot tell`` as failures).

    Every weighted stratum must be on the sheet and every sheet stratum must
    have a weight; a missing one is an error, not a silent zero. A stratum
    with weight 0 is left out. When a weighted stratum has no judged row the
    estimate is undefined (None).
    """
    missing = sorted(set(strata) - set(weights))
    if missing:
        raise ValueError(f"no weight for stratum {', '.join(repr(name or '(blank)') for name in missing)}")
    absent = sorted(name for name, weight in weights.items() if weight > 0 and name not in strata)
    if absent:
        raise ValueError(f"weighted stratum not on the sheet: {', '.join(repr(name) for name in absent)}")
    used = {name: float(weight) for name, weight in weights.items() if weight > 0}
    total = sum(used.values())
    result: dict[str, object] = {"weights": dict(sorted(used.items())), "census": []}
    census = sorted(name for name in used if sheet_sizes.get(name, 0) >= used[name])
    result["census"] = census
    for prefix, n_key, p_key, low_key, high_key in (
        ("", "n", "precision", "wilson_low", "wilson_high"),
        ("conservative_", "conservative_n", "conservative_precision", "conservative_low", "conservative_high"),
    ):
        value = low = high = 0.0
        defined = total > 0
        for name, weight in used.items():
            block = strata[name]
            if not block[n_key]:
                defined = False
                break
            share = weight / total
            p = block[p_key]
            value += share * p
            if name in census:
                low += share * p
                high += share * p
            else:
                low += share * block[low_key]
                high += share * block[high_key]
        result[f"{prefix}precision"] = value if defined else None
        result[f"{prefix}low"] = _clamp(low) if defined else None
        result[f"{prefix}high"] = _clamp(high) if defined else None
    return result


def format_score(report: dict) -> str:
    """One text table. JSON is the other output; this is the one a person reads."""
    header = (
        "stratum\tcorrect\tincorrect\tcannot_tell\tunlabeled\t"
        "n\tprecision\twilson_low\twilson_high\t"
        "conservative_n\tconservative_precision\tconservative_low\tconservative_high"
    )
    lines = [f"kind: {report['kind']}", f"z: {report['z']}", "", header]
    for name, block in report["strata"].items():
        lines.append(_line(name or "(blank)", block))
    lines.append(_line("overall", report["overall"]))
    weighted = report.get("weighted")
    if weighted is not None:
        lines += [
            "",
            "weighted (stratum weights: "
            + ", ".join(f"{name or '(blank)'}={weight:g}" for name, weight in weighted["weights"].items())
            + "; census: "
            + (", ".join(weighted["census"]) or "none")
            + ")",
            "precision\tlow\thigh\tconservative_precision\tconservative_low\tconservative_high",
            "\t".join(
                _cell(weighted[key])
                for key in (
                    "precision",
                    "low",
                    "high",
                    "conservative_precision",
                    "conservative_low",
                    "conservative_high",
                )
            ),
        ]
    return "\n".join(lines) + "\n"


def wilson(successes: int, n: int, z: float = WILSON_Z) -> tuple[float, float, float] | None:
    """``(precision, low, high)`` for ``successes`` out of ``n``. ``n == 0`` is undefined."""
    if n <= 0:
        return None
    if successes < 0 or successes > n:
        raise ValueError(f"successes must be between 0 and n (got {successes} of {n})")
    p = successes / n
    den = 1.0 + (z * z) / n
    centre = (p + (z * z) / (2.0 * n)) / den
    inside = p * (1.0 - p) / n + (z * z) / (4.0 * n * n)
    margin = z * math.sqrt(max(0.0, inside)) / den
    low = _clamp(centre - margin)
    high = _clamp(centre + margin)
    return p, low, high


def _clamp(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _summarise(rows: list[dict[str, str]]) -> dict:
    counts: Counter[str] = Counter()
    for row in rows:
        label = (row.get("label") or "").strip()
        if not label:
            counts["unlabeled"] += 1
        else:
            counts[label] += 1  # score_rows refused any other label
    correct = counts["correct"]
    incorrect = counts["incorrect"]
    cannot = counts["cannot tell"]
    primary = wilson(correct, correct + incorrect)
    # cannot tell counts as a failure: the numerator stays the corrects.
    conservative = wilson(correct, correct + incorrect + cannot)
    return {
        "correct": correct,
        "incorrect": incorrect,
        "cannot_tell": cannot,
        "unlabeled": counts["unlabeled"],
        "n": correct + incorrect,
        "precision": None if primary is None else primary[0],
        "wilson_low": None if primary is None else primary[1],
        "wilson_high": None if primary is None else primary[2],
        "conservative_n": correct + incorrect + cannot,
        "conservative_precision": None if conservative is None else conservative[0],
        "conservative_low": None if conservative is None else conservative[1],
        "conservative_high": None if conservative is None else conservative[2],
    }


def _cell(value: object) -> str:
    """One table cell: blank for a missing bound, six digits for a float."""
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _line(name: str, block: dict) -> str:
    """One tab-separated score row, in the column order ``format_score`` prints."""
    cells = [
        name,
        block["correct"],
        block["incorrect"],
        block["cannot_tell"],
        block["unlabeled"],
        block["n"],
        block["precision"],
        block["wilson_low"],
        block["wilson_high"],
        block["conservative_n"],
        block["conservative_precision"],
        block["conservative_low"],
        block["conservative_high"],
    ]
    return "\t".join(_cell(value) for value in cells)
