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
from giye.normalize.venue_names import generic_name
from giye.normalize.venues import build, institution_key

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


def test_n1_generic_names_in_two_cities_stay_apart() -> None:
    """N-1: a name of generic venue words takes the row's place into its key."""
    venues = [
        "Museum of Art, Busan",
        "Museum of Art, Daegu",
        "Museum of Art, Busan",
        "시립미술관 (부산)",
        "시립미술관 (대구)",
        "Art Center, Berlin",
        "Art Center, Seoul",
    ]
    assert not _together(venues, "Museum of Art, Busan", "Museum of Art, Daegu")
    assert not _together(venues, "시립미술관 (부산)", "시립미술관 (대구)")
    assert not _together(venues, "Art Center, Berlin", "Art Center, Seoul")
    assert {"Museum of Art, Busan"} in _groups(venues)  # one generic name in one city is one entity
    # A proper name is not qualified: one entity across rows with and without a city.
    proper = ["Example Museum of Art, Busan", "Example Museum of Art"]
    assert _together(proper, *proper)


def test_n1_stripping_a_year_or_qualifier_never_leaves_a_generic_name() -> None:
    """N-1: V7b/V7c keep the V4 key when stripping would leave generic words only."""
    assert institution_key("Space 1957", LANG) == "space 1957"
    assert institution_key("Studio Etc", LANG) == "studio etc"
    assert institution_key("Example Gallery 2019", LANG) == "example gallery"
    assert institution_key("제12회 예시비엔날레", LANG) == "예시비엔날레"
    venues = ["Space 1957, Seoul", "Space, Seoul", "Factory 2020", "Factory", "Studio Etc", "Studio"]
    assert not _together(venues, "Space 1957, Seoul", "Space, Seoul")
    assert not _together(venues, "Factory 2020", "Factory")
    assert not _together(venues, "Studio Etc", "Studio")
    assert generic_name("시립미술관 외", LANG) and generic_name("museum of art", LANG)
    assert not generic_name("gallery 1898", LANG) and not generic_name("예시미술관", LANG)
