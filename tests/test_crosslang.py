# SPDX-License-Identifier: AGPL-3.0-only
"""Rule X2: the Korean and the English copy of one CV event are queued; a person's decision folds them.

People and shows are fictitious. Venue names are public institutions, as in the demo.
"""

from __future__ import annotations

from giye.extract.crosslang import DECIDED_RULE, REASON, clear_marks, fold_cross_language, pair_key
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


def _run(rows: list[dict[str, str]], queue: list[dict[str, str]] | None = None, keep_korean: bool = True):
    clear_marks(rows)
    queue = [] if queue is None else queue
    return fold_cross_language(rows, lang=LANG, keep_korean=keep_korean, queue=queue), queue


def _queued(rows: list[dict[str, str]]) -> list[str]:
    """Activity ids of the pairs X2 puts on the queue, as 'korean/latin'."""
    _result, queue = _run(rows)
    assert all(row["publishable"] == "yes" for row in rows), "X2 alone never hides a row"
    found = []
    for item in queue:
        detail = dict(part.strip().split("=", 1) for part in item["detail"].split(";")[:5])
        found.append(f"{detail['korean']}/{detail['latin']}")
    return found


def _decide(queue: list[dict[str, str]], decision: str, when: str = "2026-10-06") -> None:
    for item in queue:
        item["detail"] += f"; decided={decision}; decided_at={when}"
        item["status"] = "done"


def test_demo_pair_is_queued_not_folded():
    """Software review 5, MAJOR-2: X2 no longer folds on its own."""
    rows = [_row("ko", "신호", "서울시립미술관 외"), _row("en", "Signal", "Seoul Museum of Art")]
    result, queue = _run(rows)
    assert result.folds == [] and result.queued == 1 and result.pending == 1
    assert [item["reason"] for item in queue] == [REASON]
    assert f"x2_pair={pair_key(rows[0], rows[1])}" in queue[0]["detail"]
    assert all(row["publishable"] == "yes" for row in rows)
    # A second run opens no second item for the same pair.
    result, queue = _run(rows, queue)
    assert result.queued == 0 and result.pending == 1 and len(queue) == 1


def test_a_same_decision_folds_into_the_korean_row():
    rows = [_row("ko", "신호", "서울시립미술관 외"), _row("en", "Signal", "Seoul Museum of Art")]
    _result, queue = _run(rows)
    _decide(queue, "same")
    result, queue = _run(rows, queue)
    assert [(fold.kept["activity_id"], fold.folded["activity_id"], fold.rule) for fold in result.folds] == [
        ("ko", "en", DECIDED_RULE)
    ]
    assert rows[0]["publishable"] == "yes"
    assert rows[1]["publishable"] == "no"
    assert rows[1]["reviewer_note"] == "cv_section=exhibition; superseded_by=ko; rule=X2+H; x2_decided=2026-10-06"


def test_a_different_decision_keeps_both_rows():
    rows = [_row("ko", "신호", "서울시립미술관 외"), _row("en", "Signal", "Seoul Museum of Art")]
    _result, queue = _run(rows)
    _decide(queue, "different")
    result, queue = _run(rows, queue)
    assert result.folds == [] and result.kept_apart == 1 and result.pending == 0 and len(queue) == 1
    assert all(row["publishable"] == "yes" for row in rows)


def test_english_first_archive_keeps_the_english_row():
    rows = [_row("ko", "신호", "서울시립미술관 외"), _row("en", "Signal", "Seoul Museum of Art")]
    _result, queue = _run(rows, keep_korean=False)
    _decide(queue, "same")
    _run(rows, queue, keep_korean=False)
    assert rows[0]["publishable"] == "no"
    assert "superseded_by=en; rule=X2+H" in rows[0]["reviewer_note"]
    assert rows[1]["publishable"] == "yes"


def test_a_decision_survives_a_merge_and_a_broken_one_to_one():
    """The pair key reads the rows' content, not the ledger or activity ids a merge changes."""
    rows = [_row("ko", "신호", "서울시립미술관"), _row("en", "Signal", "Seoul Museum of Art")]
    _result, queue = _run(rows)
    _decide(queue, "same")
    for row in rows:
        row["ledger_id"] = "LED-TEST-0002"
        row["activity_id"] += "-merged"
    # A second Korean show at the same museum would stop X2 from queueing the pair now.
    rows.append(_row("ko2", "열린 수장고", "서울시립미술관", ledger_id="LED-TEST-0002"))
    result, _queue = _run(rows, queue)
    assert [fold.folded["activity_id"] for fold in result.folds] == ["en-merged"]


def test_marks_of_the_automatic_x2_are_cleared():
    """Folds written before 2026-10-06 (rule=X2, no decision) are undone and queued."""
    rows = [
        _row("ko", "신호", "서울시립미술관"),
        _row("en", "Signal", "Seoul Museum of Art", publishable="no",
             reviewer_note="cv_section=exhibition; superseded_by=ko; rule=X2"),
    ]
    result, _queue = _run(rows)
    assert result.folds == [] and result.queued == 1
    assert rows[1]["publishable"] == "yes" and rows[1]["reviewer_note"] == "cv_section=exhibition"


def test_two_shows_at_one_museum_in_one_year_stay_apart():
    rows = [
        _row("ko1", "신호", "서울시립미술관"),
        _row("ko2", "열린 수장고", "서울시립미술관"),
        _row("en", "Signal", "Seoul Museum of Art"),
    ]
    assert _queued(rows) == []


def test_different_years_are_not_a_pair():
    rows = [_row("ko", "신호", "서울시립미술관", year="2021"), _row("en", "Signal", "Seoul Museum of Art")]
    assert _queued(rows) == []


def test_different_types_are_not_a_pair():
    rows = [_row("ko", "신호", "서울시립미술관", activity_type="screening"), _row("en", "Signal", "Seoul Museum of Art")]
    assert _queued(rows) == []


def test_empty_venue_is_not_a_pair():
    rows = [_row("ko", "신호", ""), _row("en", "Signal", "")]
    assert _queued(rows) == []
    rows = [_row("ko", "신호", "서울시립미술관"), _row("en", "Signal", "")]
    assert _queued(rows) == []


def test_other_institution_is_not_a_pair():
    rows = [_row("ko", "신호", "서울시립미술관"), _row("en", "Signal", "Busan Museum of Art")]
    assert _queued(rows) == []


def test_one_cv_document_is_not_a_pair_with_itself():
    rows = [
        _row("ko", "신호", "서울시립미술관", origin="cv:CV-TEST-mixed"),
        _row("en", "Signal", "Seoul Museum of Art", origin="cv:CV-TEST-mixed"),
    ]
    assert _queued(rows) == []


def test_a_same_document_match_still_blocks_the_one_to_one_test():
    rows = [
        _row("ko", "청년전", "서울시립미술관", origin="cv:CV-TEST-mixed"),
        _row("en1", "Youth Show", "Seoul Museum of Art", origin="cv:CV-TEST-mixed"),
        _row("en2", "Signal", "Seoul Museum of Art", origin="cv:CV-TEST-en"),
    ]
    assert _queued(rows) == []


def test_non_cv_rows_are_not_candidates():
    rows = [_row("ko", "신호", "서울시립미술관"), _row("en", "Signal", "Seoul Museum of Art", origin="EXAMPLE-2022")]
    assert _queued(rows) == []


def test_second_run_is_idempotent_and_the_mark_is_reversible():
    rows = [_row("ko", "신호", "서울시립미술관 외"), _row("en", "Signal", "Seoul Museum of Art")]
    _result, queue = _run(rows)
    _decide(queue, "same")
    _run(rows, queue)
    first = [dict(row) for row in rows]
    _run(rows, queue)
    assert rows == first
    assert clear_marks(rows) == 1
    assert rows[1]["publishable"] == "yes"
    assert rows[1]["reviewer_note"] == "cv_section=exhibition"


def test_two_unrelated_latin_titles_are_not_even_queued():
    """Review round 5: a Korean-CV row with a Latin title folded into an unrelated English show."""
    rows = [
        _row("ko", "Noise Garden", "서울시립미술관", origin="cv:CV-TEST-ko"),
        _row("en", "Machine Dreams", "Seoul Museum of Art"),
    ]
    assert _queued(rows) == []


def test_two_unrelated_hangul_titles_are_not_even_queued():
    rows = [
        _row("ko", "물의 기억", "서울시립미술관"),
        _row("en", "빛의 정원", "Seoul Museum of Art", origin="cv:CV-TEST-en"),
    ]
    assert _queued(rows) == []


def test_same_latin_title_with_an_edition_mark_is_queued():
    rows = [
        _row("ko", "Example Digital Art Exhibition", "서울시립미술관", origin="cv:CV-TEST-ko"),
        _row("en", "Example '05' Digital Art Exhibition", "Seoul Museum of Art"),
    ]
    assert _queued(rows) == ["ko/en"]


def test_cross_script_titles_are_queued_for_a_person():
    """Review round 5: 〈물의 기억〉 and "Machine Dreams" at one museum folded. Now a person decides."""
    rows = [_row("ko", "물의 기억", "서울시립미술관"), _row("en", "Machine Dreams", "Seoul Museum of Art")]
    assert _queued(rows) == ["ko/en"]


def test_title_bag_drops_numbers_and_one_letter_tokens():
    from giye.extract.crosslang import title_bag, titles_agree

    assert title_bag("Signal 2022 — a Show") == frozenset({"signal", "show"})
    assert titles_agree("Signal", "Signal 2022")
    assert not titles_agree("Signal Exhibition", "Noise Exhibition Garden")


def test_generic_venues_or_two_cities_are_not_a_pair():
    """N-2: a generic name names no particular place, and two cities are two events."""
    cases = [
        [_row("ko", "빛", "Art Space, 대구"), _row("en", "Machines", "Art Space, Berlin")],
        [_row("ko", "빛", "갤러리, 부산"), _row("en", "Machines", "Gallery, London")],
        [_row("ko", "빛", "Space 2019, 부산"), _row("en", "Machines", "Space, New York")],
        [_row("ko", "빛", "예시미술관, 대구"), _row("en", "Machines", "Yesi Museum of Art, Busan")],
    ]
    for rows in cases:
        assert _queued(rows) == [], rows[1]["venue"]
    # The same specific institution with the same city, or with a city on one side only, is still a pair.
    for venues in (("예시미술관, 부산", "Yesi Museum of Art, Busan"), ("예시미술관, 부산", "Yesi Museum of Art")):
        rows = [_row("ko", "빛", venues[0]), _row("en", "Machines", venues[1])]
        assert _queued(rows) == ["ko/en"], venues


def test_queue_decide_records_and_honours_an_x2_decision(tmp_path, capsys):
    """The CLI path a person uses: same needs H, folds at once, and a re-apply keeps the fold."""
    import shutil

    from giye.cli import main
    from giye.config import load
    from giye.demo import run_demo
    from giye.extract.apply import apply_extractions
    from giye.ledger.ledger import Ledger
    from tests.test_decisions import CLOCK, DEMO

    dest = tmp_path / "demo"
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns("data", "__pycache__", "*.pyc", "decisions.csv"))
    result = run_demo(dest / "giye.toml", dest / "data", now=CLOCK)
    assert result.queue_items == 5 and result.activities == 38
    config = str(dest / "giye.toml")
    ledger = Ledger.open(load(config))
    item = next(row for row in ledger.read("review_queue") if row["reason"] == REASON)
    assert item["status"] == "open"
    signal = next(row for row in ledger.read("activities") if row["title"] == "Signal")
    assert signal["publishable"] == "yes"

    # same without H evidence, or H without who judged, is refused.
    assert main(["queue", "decide", item["queue_id"], "--config", config, "--decision", "same"]) == 2
    weak = "H one show in both CVs 2026-01-20"
    assert main(["queue", "decide", item["queue_id"], "--config", config, "--decision", "same", "--evidence", weak]) == 2
    capsys.readouterr()
    evidence = "H one show in both CVs, judged by author 2026-01-20"
    assert main(["queue", "decide", item["queue_id"], "--config", config, "--decision", "same", "--evidence", evidence]) == 0
    signal = next(row for row in ledger.read("activities") if row["title"] == "Signal")
    assert signal["publishable"] == "no" and "rule=X2+H" in signal["reviewer_note"]
    decided = next(row for row in ledger.read("review_queue") if row["queue_id"] == item["queue_id"])
    assert "decided=same" in decided["detail"] and decided["status"] == "done"

    # Applying the extractions again clears the mark and folds it again from the decision.
    apply_extractions(ledger)
    signal = next(row for row in ledger.read("activities") if row["title"] == "Signal")
    assert signal["publishable"] == "no" and "rule=X2+H" in signal["reviewer_note"]
    assert [row for row in ledger.read("review_queue") if row["reason"] == REASON] == [decided]
