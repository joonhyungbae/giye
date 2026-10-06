# SPDX-License-Identifier: AGPL-3.0-only
"""Rule X2: the Korean and the English copy of one CV event fold, conservatively.

People and shows are fictitious. Venue names are public institutions, as in the demo.
"""

from __future__ import annotations

from giye.extract.crosslang import clear_marks, fold_cross_language
from giye.normalize.language import default_language

LANG = default_language()


def _row(activity_id: str, title: str, venue: str, year: str = "2022", **extra: str) -> dict[str, str]:
    row = {
        "activity_id": activity_id,
        "ledger_id": "LED-TEST-0001",
        "title": title,
        "venue": venue,
        "year": year,
        "activity_type": "group_exhibition",
        "publishable": "yes",
        "reviewer_note": "cv_section=exhibition",
        "origin": "cv:CV-TEST-ko" if any("가" <= char <= "힣" for char in title + venue) else "cv:CV-TEST-en",
    }
    row.update(extra)
    return row


def _fold(rows: list[dict[str, str]], keep_korean: bool = True):
    clear_marks(rows)
    return fold_cross_language(rows, lang=LANG, keep_korean=keep_korean)


def test_demo_pair_folds_into_the_korean_row():
    rows = [_row("ko", "신호", "서울시립미술관 외"), _row("en", "Signal", "Seoul Museum of Art")]
    folds = _fold(rows)
    assert [(fold.kept["activity_id"], fold.folded["activity_id"]) for fold in folds] == [("ko", "en")]
    assert rows[0]["publishable"] == "yes"
    assert rows[1]["publishable"] == "no"
    assert rows[1]["reviewer_note"] == "cv_section=exhibition; superseded_by=ko; rule=X2"


def test_english_first_archive_keeps_the_english_row():
    rows = [_row("ko", "신호", "서울시립미술관 외"), _row("en", "Signal", "Seoul Museum of Art")]
    _fold(rows, keep_korean=False)
    assert rows[0]["publishable"] == "no"
    assert "superseded_by=en; rule=X2" in rows[0]["reviewer_note"]
    assert rows[1]["publishable"] == "yes"


def test_two_shows_at_one_museum_in_one_year_stay_apart():
    rows = [
        _row("ko1", "신호", "서울시립미술관"),
        _row("ko2", "열린 수장고", "서울시립미술관"),
        _row("en", "Signal", "Seoul Museum of Art"),
    ]
    assert _fold(rows) == []
    assert all(row["publishable"] == "yes" for row in rows)


def test_different_years_do_not_fold():
    rows = [_row("ko", "신호", "서울시립미술관", year="2021"), _row("en", "Signal", "Seoul Museum of Art")]
    assert _fold(rows) == []


def test_different_types_do_not_fold():
    rows = [_row("ko", "신호", "서울시립미술관", activity_type="screening"), _row("en", "Signal", "Seoul Museum of Art")]
    assert _fold(rows) == []


def test_empty_venue_does_not_fold():
    rows = [_row("ko", "신호", ""), _row("en", "Signal", "")]
    assert _fold(rows) == []
    rows = [_row("ko", "신호", "서울시립미술관"), _row("en", "Signal", "")]
    assert _fold(rows) == []


def test_other_institution_does_not_fold():
    rows = [_row("ko", "신호", "서울시립미술관"), _row("en", "Signal", "Busan Museum of Art")]
    assert _fold(rows) == []


def test_one_cv_document_does_not_fold_with_itself():
    rows = [
        _row("ko", "신호", "서울시립미술관", origin="cv:CV-TEST-mixed"),
        _row("en", "Signal", "Seoul Museum of Art", origin="cv:CV-TEST-mixed"),
    ]
    assert _fold(rows) == []


def test_a_same_document_match_still_blocks_the_one_to_one_test():
    rows = [
        _row("ko", "청년전", "서울시립미술관", origin="cv:CV-TEST-mixed"),
        _row("en1", "Youth Show", "Seoul Museum of Art", origin="cv:CV-TEST-mixed"),
        _row("en2", "Signal", "Seoul Museum of Art", origin="cv:CV-TEST-en"),
    ]
    assert _fold(rows) == []


def test_non_cv_rows_are_not_candidates():
    rows = [_row("ko", "신호", "서울시립미술관"), _row("en", "Signal", "Seoul Museum of Art", origin="EXAMPLE-2022")]
    assert _fold(rows) == []


def test_second_run_is_idempotent_and_the_mark_is_reversible():
    rows = [_row("ko", "신호", "서울시립미술관 외"), _row("en", "Signal", "Seoul Museum of Art")]
    _fold(rows)
    first = [dict(row) for row in rows]
    _fold(rows)
    assert rows == first
    assert clear_marks(rows) == 1
    assert rows[1]["publishable"] == "yes"
    assert rows[1]["reviewer_note"] == "cv_section=exhibition"


def test_two_unrelated_latin_titles_do_not_fold():
    """Review round 5: a Korean-CV row with a Latin title folded into an unrelated English show."""
    rows = [
        _row("ko", "Noise Garden", "서울시립미술관", origin="cv:CV-TEST-ko"),
        _row("en", "Machine Dreams", "Seoul Museum of Art"),
    ]
    assert _fold(rows) == []
    assert all(row["publishable"] == "yes" for row in rows)


def test_two_unrelated_hangul_titles_do_not_fold():
    rows = [
        _row("ko", "물의 기억", "서울시립미술관"),
        _row("en", "빛의 정원", "Seoul Museum of Art", origin="cv:CV-TEST-en"),
    ]
    assert _fold(rows) == []


def test_same_latin_title_with_an_edition_mark_folds():
    rows = [
        _row("ko", "Example Digital Art Exhibition", "서울시립미술관", origin="cv:CV-TEST-ko"),
        _row("en", "Example '05' Digital Art Exhibition", "Seoul Museum of Art"),
    ]
    assert [fold.folded["activity_id"] for fold in _fold(rows)] == ["en"]


def test_cross_script_titles_keep_the_venue_rule():
    """A Hangul and a Latin title cannot be compared without a translation; the venue decides."""
    rows = [_row("ko", "물의 기억", "서울시립미술관"), _row("en", "Machine Dreams", "Seoul Museum of Art")]
    assert [fold.folded["activity_id"] for fold in _fold(rows)] == ["en"]


def test_title_bag_drops_numbers_and_one_letter_tokens():
    from giye.extract.crosslang import title_bag, titles_agree

    assert title_bag("Signal 2022 — a Show") == frozenset({"signal", "show"})
    assert titles_agree("Signal", "Signal 2022")
    assert not titles_agree("Signal Exhibition", "Noise Exhibition Garden")


def test_generic_venues_or_two_cities_do_not_fold():
    """N-2: a generic name names no particular place, and two cities are two events."""
    cases = [
        [_row("ko", "빛", "Art Space, 대구"), _row("en", "Machines", "Art Space, Berlin")],
        [_row("ko", "빛", "갤러리, 부산"), _row("en", "Machines", "Gallery, London")],
        [_row("ko", "빛", "Space 2019, 부산"), _row("en", "Machines", "Space, New York")],
        [_row("ko", "빛", "예시미술관, 대구"), _row("en", "Machines", "Yesi Museum of Art, Busan")],
    ]
    for rows in cases:
        assert _fold(rows) == [], rows[1]["venue"]
    # The same specific institution with the same city, or with a city on one side only, still folds.
    for venues in (("예시미술관, 부산", "Yesi Museum of Art, Busan"), ("예시미술관, 부산", "Yesi Museum of Art")):
        rows = [_row("ko", "빛", venues[0]), _row("en", "Machines", venues[1])]
        assert [fold.folded["activity_id"] for fold in _fold(rows)] == ["en"], venues
