# SPDX-License-Identifier: AGPL-3.0-only
"""Precision of an audit sheet, with a Wilson 95% score interval.

``correct`` is a success. ``incorrect`` is a failure. ``cannot tell`` is
counted and left out of the primary denominator. The conservative interval
puts those rows back in as failures. A blank label is not judged yet and is
in neither denominator. Any other text is ``other`` and is also left out, so
a typo cannot become a success.

The interval uses the fixed normal quantile ``z = 1.959963984540054`` (the
0.975 point of the standard normal). No statistics library is imported, so
the same counts always print the same bounds.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from giye.audit.sheet import LABELS, read_sheet

# normsinv(0.975), fixed. A later SciPy would print the same digits.
WILSON_Z = 1.959963984540054


def score_sheet(path: Path, kind: str) -> dict:
    """Score ``path``. ``kind`` has to match the sheet."""
    _fields, rows = read_sheet(path)
    kinds = {row.get("kind") or "" for row in rows}
    kinds.discard("")
    if kinds and kinds != {kind}:
        raise ValueError(f"sheet kind is {sorted(kinds)}, not {kind}")
    return score_rows(rows, kind)


def score_rows(rows: list[dict[str, str]], kind: str) -> dict:
    by_stratum: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_stratum.setdefault(row.get("stratum") or "", []).append(row)
    strata = {name: _summarise(group) for name, group in sorted(by_stratum.items())}
    return {"kind": kind, "z": WILSON_Z, "overall": _summarise(rows), "strata": strata}


def format_score(report: dict) -> str:
    """One text table. JSON is the other output; this is the one a person reads."""
    header = (
        "stratum\tcorrect\tincorrect\tcannot_tell\tunlabeled\tother\t"
        "n\tprecision\twilson_low\twilson_high\t"
        "conservative_n\tconservative_precision\tconservative_low\tconservative_high"
    )
    lines = [f"kind: {report['kind']}", f"z: {report['z']}", "", header]
    for name, block in report["strata"].items():
        lines.append(_line(name or "(blank)", block))
    lines.append(_line("overall", report["overall"]))
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
        elif label in LABELS:
            counts[label] += 1
        else:
            counts["other"] += 1
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
        "other": counts["other"],
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
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _line(name: str, block: dict) -> str:
    cells = [
        name,
        block["correct"],
        block["incorrect"],
        block["cannot_tell"],
        block["unlabeled"],
        block["other"],
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
