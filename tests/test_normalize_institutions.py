# SPDX-License-Identifier: AGPL-3.0-only
"""Institution false-join guards (P3, V1–V9) found by the pre-release audit of 2026-10-06.

Every institution here is invented ("Example Museum of Art", XYZ, 예시미술관) or a
place name (Busan, Daegu). No person names; ledger ids are p0, p1, ….
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

from giye.normalize.language import KoreanEnglish
from giye.normalize.venues import build

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "normalize" / "activities.csv"
LANG = KoreanEnglish.load()


def _row(index: int, venue: str, person: str | None = None, title: str = "Show") -> dict[str, str]:
    return {
        "activity_id": f"a{index:03d}",
        "ledger_id": person or f"p{index}",
        "venue": venue,
        "title": title,
        "year": "2020",
        "activity_type": "group_exhibition",
        "publishable": "yes",
        "origin": "",
    }


def _groups(venues: list[str], people: list[str] | None = None) -> list[set[str]]:
    """Venue strings grouped by the entity their row was given (rows without one are left out)."""
    rows = [_row(index, venue, people[index] if people else None) for index, venue in enumerate(venues)]
    result = build(rows, write=False, lang=LANG)
    by_id: dict[str, set[str]] = {}
    for row in rows:
        venue_id = result.annotations[row["activity_id"]]["venue_id"]
        if venue_id:
            by_id.setdefault(venue_id, set()).add(row["venue"])
    return list(by_id.values())


def _together(venues: list[str], left: str, right: str, people: list[str] | None = None) -> bool:
    return any(left in group and right in group for group in _groups(venues, people))


def _signature(rows: list[dict[str, str]]) -> tuple:
    result = build(rows, write=False, lang=LANG)
    ids = tuple(sorted((key, value["venue_id"], value["venue_kind"]) for key, value in result.annotations.items()))
    venues = tuple(sorted(tuple(sorted(row.items())) for row in result.venues))
    return ids, venues, tuple(sorted(result.merges))


def test_n6_clustering_does_not_depend_on_row_order() -> None:
    """N-6: the bare acronym is in Seoul and Busan equally often, so neither city is its own site."""
    venues = ["XYZ, Seoul", "XYZ, Busan", "XYZ Seoul", "XYZ", "Foo Gallery, Seoul", "Foo Gallery, Busan"]
    tie = [_row(index, venue, f"p{index % 3}") for index, venue in enumerate(venues)]
    with FIXTURE.open(encoding="utf-8", newline="") as handle:
        fixture = list(csv.DictReader(handle))
    for rows in (tie, fixture):
        expected = _signature(rows)
        for seed in range(25):
            shuffled = rows[:]
            random.Random(seed).shuffle(shuffled)
            assert _signature(shuffled) == expected, seed
    assert not _together(venues, "XYZ Seoul", "XYZ")
