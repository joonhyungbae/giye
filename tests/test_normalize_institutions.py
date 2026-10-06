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
from giye.normalize.venue_names import generic_name, hangul_signature
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


def test_n3_v9_does_not_join_two_hangul_institutions_that_read_alike() -> None:
    """N-3: 시립 reads as nothing, so 예시미술관 and 예시시립미술관 give one bag. Neither joins."""
    venues = ["예시미술관", "예시시립미술관", "Yesi Museum of Art"]
    groups = _groups(venues)
    assert not _together(venues, "예시미술관", "예시시립미술관")
    assert not any("Yesi Museum of Art" in group and len(group) > 1 for group in groups)
    # One Hangul institution with that reading: V9 joins as before.
    assert _together(["예시시립미술관", "Yesi Museum of Art"], "예시시립미술관", "Yesi Museum of Art")
    # Spellings of one name (spaces, a qualifier) are not a second institution.
    alone = ["예시 미술관", "예시미술관 외", "Yesi Museum of Art"]
    assert _together(alone, "예시 미술관", "Yesi Museum of Art")
    assert hangul_signature("예시미술관", LANG) != hangul_signature("예시시립미술관", LANG)


def test_n4_an_acronym_of_two_institutions_joins_neither() -> None:
    """N-4: a shared acronym is ambiguous; one institution's acronym still joins it (V5d)."""
    shared = ["Example Arts Service (EAS)"] * 2 + ["Example Art School (EAS)"] * 2
    people = ["p1", "p2", "p3", "p4"]
    assert not _together(shared, "Example Arts Service (EAS)", "Example Art School (EAS)", people)
    rows = [_row(index, venue, people[index]) for index, venue in enumerate(shared)]
    result = build(rows, write=False, lang=LANG)
    assert not [merge for merge in result.merges if merge[0] == "V5d"]
    single = ["Example Arts Service (EAS)"] * 2
    rows = [_row(index, venue, people[index]) for index, venue in enumerate(single)]
    assert [merge[0] for merge in build(rows, write=False, lang=LANG).merges] == ["V5d"]
    # A Hangul and a Latin name of one institution (joined by V9) share the acronym without ambiguity.
    one = ["예시미술관 (YMA)", "예시미술관 (YMA)", "Yesi Museum of Art (YMA)", "Yesi Museum of Art (YMA)", "Yesi Museum of Art"]
    assert _together(one, "예시미술관 (YMA)", "Yesi Museum of Art (YMA)", ["p1", "p2", "p3", "p4", "p5"])


def test_n4_a_funder_acronym_is_the_funder() -> None:
    """N-4: the acronym of a funder in the same row does not become the row's venue."""
    venues = ["Example Culture Center (ECC)"] * 2 + ["Example Arts Council (ECC)"] * 2
    rows = [_row(index, venue, f"p{index}") for index, venue in enumerate(venues)]
    result = build(rows, write=False, lang=LANG)
    kinds = [result.annotations[row["activity_id"]]["venue_kind"] for row in rows]
    assert kinds == ["institution", "institution", "funder", "funder"]
    center = result.annotations[rows[0]["activity_id"]]["venue_id"]
    assert all(result.annotations[row["activity_id"]]["venue_id"] != center for row in rows[2:])


def test_n5_a_title_before_the_venue_is_not_the_venue() -> None:
    """N-5: "Title, Venue, City" counts the row at the venue, and the title is no entity."""
    venues = [
        "Light Garden, Example Museum of Art",
        "Example Museum of Art",
        "Light Garden, Example Gallery, Busan",
        "예시의 정원, 예시미술관",
        "예시미술관",
    ]
    groups = _groups(venues)
    assert {"Light Garden, Example Museum of Art", "Example Museum of Art"} in groups
    assert {"Light Garden, Example Gallery, Busan"} in groups
    assert {"예시의 정원, 예시미술관", "예시미술관"} in groups
    rows = [_row(index, venue) for index, venue in enumerate(venues)]
    names = {venue["name"] for venue in build(rows, write=False, lang=LANG).venues}
    assert "Light Garden" not in names and "예시의 정원" not in names
    # The fragment equal to the row's title is not the venue even when it names a kind of venue.
    rows = [_row(0, "Example Festival, Example Hall", title="Example Festival"), _row(1, "Example Hall")]
    result = build(rows, write=False, lang=LANG)
    assert result.annotations["a000"]["venue_id"] == result.annotations["a001"]["venue_id"]
    # A bracketed alias and an acronym stay names; two venue-like fragments keep the first.
    assert _together(["Nabi Example (Example Art Center)", "Nabi Example"], "Nabi Example (Example Art Center)", "Nabi Example")
    assert _together(["XYZ, Example Gallery", "XYZ"], "XYZ, Example Gallery", "XYZ")


def test_n7_the_packaged_gazetteer_reads_korean_units_as_places() -> None:
    """N-7: without a GeoNames tree, Korean cities, counties, districts and nested places are places (V3c)."""
    places = ["Cheongju", "청주", "Jeju", "종로구", "서울 종로구", "Seoul Korea", "Tokyo Japan", "New York City"]
    rows = [_row(index, venue) for index, venue in enumerate(places)]
    result = build(rows, write=False, lang=LANG)
    assert {value["venue_kind"] for value in result.annotations.values()} == {"place_only"}
    assert not result.venues
    # All 17 first-level units resolve, in Hangul and in Latin.
    regions = "서울 부산 대구 인천 광주 대전 울산 세종 경기도 강원도 충청북도 충청남도 전라남도 경상북도 경상남도 제주도 전북특별자치도"
    for name in [*regions.split(), "Gyeonggi-do", "Chungcheongnam-do", "Jeollanam-do", "Sejong"]:
        assert LANG.gazetteer.resolve_fragments([name])[0][1] == "KR", name
    # Two place names that do not agree are not one place.
    assert build([_row(0, "Busan Daegu")], write=False, lang=LANG).annotations["a000"]["venue_kind"] == "institution"


def test_n7_rules_md_v8_example_holds_with_the_packaged_gazetteer() -> None:
    """RULES.md V8: MMCA Seoul is a branch when MMCA Cheongju is written too; ZKM Karlsruhe joins ZKM."""
    venues = ["MMCA", "MMCA Seoul", "MMCA Cheongju", "MMCA, Seoul"]
    assert not _together(venues, "MMCA", "MMCA Seoul")
    assert not _together(venues, "MMCA", "MMCA Cheongju")
    assert _together(["ZKM", "ZKM Karlsruhe", "ZKM, Karlsruhe"], "ZKM", "ZKM Karlsruhe")


def test_n3_x2_does_not_fold_through_an_ambiguous_reading() -> None:
    from giye.extract.crosslang import clear_marks, fold_cross_language

    def cv(activity_id: str, title: str, venue: str, person: str) -> dict[str, str]:
        return {
            "activity_id": activity_id,
            "ledger_id": person,
            "title": title,
            "venue": venue,
            "year": "2021",
            "activity_type": "group_exhibition",
            "publishable": "yes",
            "reviewer_note": "",
            "origin": f"cv:{activity_id}",
        }

    rows = [cv("ko", "빛", "예시미술관", "p1"), cv("en", "Machines", "Yesi Museum of Art", "p1")]
    clear_marks(rows)
    assert [fold.folded["activity_id"] for fold in fold_cross_language(rows, lang=LANG, keep_korean=True)] == ["en"]
    rows.append(cv("other", "물", "예시시립미술관", "p2"))
    clear_marks(rows)
    assert fold_cross_language(rows, lang=LANG, keep_korean=True) == []
