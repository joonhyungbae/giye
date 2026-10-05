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
