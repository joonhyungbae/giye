# SPDX-License-Identifier: AGPL-3.0-only
"""A manual merge's evidence is checked against the ledger, not only its form.

The cases reproduce a software review: an ``E1`` citing a site neither record
lists, an ``H`` dated in the future, and free text or an unknown rule id passed
to the public ``Ledger.merge``. People are fictitious; URLs are example.org.
"""

from __future__ import annotations

import pytest

from giye.cli import main
from giye.config import GiyeError
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import CV_SOURCES_FIELDS, empty_row
from giye.resolve.decide import merge_people, verify_merge_evidence
from tests.test_decisions import _artists, _copy, _named
from tests.test_resolve import _act, _artist, _cv, _ledger, _link, _mem, _seed


def _pair(tmp_path, *, links=None, activities=None, membership=None, names=(("이하루", "Lee Haru"), ("김서연", "Kim Seoyeon"))):
    ledger = _ledger(tmp_path)
    (ko_a, en_a), (ko_b, en_b) = names
    _seed(
        ledger,
        [_artist("LED-a", "GY-000001", ko_a, en_a), _artist("LED-b", "GY-000002", ko_b, en_b)],
        activities or [_act("LED-a", "EXAMPLE-WORKSHOP-2021", 2021), _act("LED-b", "EXAMPLE-RESIDENCY-2019", 2019)],
        membership or [_mem("LED-a", "EXAMPLE-WORKSHOP-2021"), _mem("LED-b", "EXAMPLE-RESIDENCY-2019")],
        links,
    )
    return ledger


def _live(ledger: Ledger) -> set[str]:
    return {row["ledger_id"] for row in ledger.read("artists")}


def test_e1_must_cite_a_site_both_records_list(tmp_path):
    ledger = _pair(tmp_path, links=[_link("LED-a", "https://haru.example.org/")])
    with pytest.raises(GiyeError, match="E1 does not hold"):
        merge_people(ledger, "LED-a", "LED-b", evidence="E1 https://example.org/unrelated")
    # A site of one record only is not E1 either.
    with pytest.raises(GiyeError, match="not a website of both"):
        merge_people(ledger, "LED-a", "LED-b", evidence="E1 https://haru.example.org/cv")
    assert _live(ledger) == {"LED-a", "LED-b"}

    ledger.write("links", [_link("LED-a", "https://haru.example.org/"), _link("LED-b", "https://www.haru.example.org/about")], task="test")
    assert verify_merge_evidence(ledger, "LED-a", "LED-b", "E1 https://haru.example.org/cv") == "E1"
    # X1 on top needs the name keys to meet; Lee Haru and Kim Seoyeon do not.
    with pytest.raises(GiyeError, match="name keys"):
        merge_people(ledger, "LED-a", "LED-b", evidence="X1+E1 https://haru.example.org/cv")
    keep, _drop = merge_people(ledger, "LED-a", "LED-b", evidence="E1 https://haru.example.org/cv")
    assert _live(ledger) == {keep}
    assert "rule=E1" in ledger.read("artists")[0]["reviewer_note"]


def _cv_sources(ledger: Ledger, rows: list[tuple[str, str]]) -> None:
    ledger.write(
        "cv_sources",
        [empty_row(CV_SOURCES_FIELDS, source_id=sid, ledger_id=lid, url=f"https://cv.example.org/{sid}") for sid, lid in rows],
        task="test",
    )


def test_e2_cited_cv_must_belong_to_one_record_and_list_the_other_edition(tmp_path):
    ledger = _pair(tmp_path, names=(("김하늘", ""), ("Haneul Kim", "Haneul Kim")))
    artists = ledger.read("artists")
    artists.append(_artist("LED-c", "GY-000003", "박서연", "Seoyeon Park"))
    ledger.write("artists", artists, task="test")
    _cv_sources(ledger, [("CV-T-1", "LED-a"), ("CV-T-2", "LED-a"), ("CV-T-3", "LED-c")])
    _cv(
        ledger,
        "LED-a",
        [
            {"title": "Example Residency", "venue": "", "year": 2019, "source_id": "CV-T-1"},
            {"title": "Open studio", "venue": "", "year": 2019, "source_id": "CV-T-2"},
        ],
    )
    for bad, reason in (
        ("E2 https://example.org/press/2019", "no CV source"),
        ("E2 CV-T-3", "neither record"),
        ("E2 CV-T-2", "does not list a roster edition"),
    ):
        with pytest.raises(GiyeError, match=reason):
            verify_merge_evidence(ledger, "LED-a", "LED-b", bad)
    assert verify_merge_evidence(ledger, "LED-a", "LED-b", "E2 CV-T-1") == "E2"
    assert verify_merge_evidence(ledger, "LED-b", "LED-a", "X1+E2 https://cv.example.org/CV-T-1") == "X1+E2"


def test_e3_and_e4_must_cite_the_shared_work_or_team(tmp_path):
    ledger = _pair(
        tmp_path,
        names=(("한별", ""), ("한별", "Han Byeol")),
        activities=[
            _act("LED-a", "EXAMPLE-WORKSHOP-2020", 2020, role="팀: 노을크루", title="〈푸른 신호〉"),
            _act("LED-b", "EXAMPLE-RESIDENCY-2019", 2019, role="팀: 노을크루", title="〈푸른 신호〉"),
        ],
        membership=[_mem("LED-a", "EXAMPLE-WORKSHOP-2020"), _mem("LED-b", "EXAMPLE-RESIDENCY-2019")],
    )
    assert verify_merge_evidence(ledger, "LED-a", "LED-b", "E3 〈푸른 신호〉 https://example.org/roster") == "E3"
    with pytest.raises(GiyeError, match="not the work"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E3 〈다른 작품〉 https://example.org/roster")
    assert verify_merge_evidence(ledger, "LED-a", "LED-b", "E4 team 노을크루 https://example.org/roster") == "E4"
    with pytest.raises(GiyeError, match="not the team"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E4 team 바다크루 https://example.org/roster")


def test_e3_and_e4_refused_when_the_rosters_share_nothing(tmp_path):
    ledger = _pair(tmp_path)
    with pytest.raises(GiyeError, match="no work"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E3 〈푸른 신호〉 https://example.org/roster")
    with pytest.raises(GiyeError, match="no team"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E4 team 노을크루 https://example.org/roster")


def test_h_needs_a_past_date_and_a_reason_of_a_few_words(tmp_path):
    ledger = _pair(tmp_path)
    for bad, reason in (
        ("H same face in both catalogues 2099-01-01", "future"),
        ("H same 2026-01-15", "at least 3 words"),
        ("H same face in both catalogues 2026-01-15 and 2026-02-30", "not a calendar date"),
    ):
        with pytest.raises(GiyeError, match=reason):
            merge_people(ledger, "LED-a", "LED-b", evidence=bad)
    assert _live(ledger) == {"LED-a", "LED-b"}
    merge_people(ledger, "LED-a", "LED-b", evidence="H same face in both catalogues, checked by the author 2026-01-15")
    assert len(_live(ledger)) == 1


def test_public_ledger_merge_refuses_free_text_and_unknown_rules(tmp_path):
    ledger = _pair(tmp_path, links=[_link("LED-a", "https://haru.example.org/")])
    with pytest.raises(GiyeError):
        ledger.merge("LED-a", "LED-b", evidence="whatever", rule="ZZ")
    with pytest.raises(GiyeError, match="E1 does not hold"):
        ledger.merge("LED-a", "LED-b", evidence="E1 https://example.org/unrelated", rule="E1")
    reason = "H same face in both catalogues, checked by the author 2026-01-15"
    with pytest.raises(GiyeError, match="not the rule"):
        ledger.merge("LED-a", "LED-b", evidence=reason, rule="E1")
    assert _live(ledger) == {"LED-a", "LED-b"}
    ledger.merge("LED-a", "LED-b", evidence=reason, rule="H")
    assert _live(ledger) == {"LED-a"}


def test_cli_refuses_an_unchecked_e1_on_the_demo(tmp_path, capsys):
    dest = _copy(tmp_path)
    config = str(dest / "giye.toml")
    artists = _artists(dest)
    haru = next(row for row in _named(artists, "Lee Haru"))
    seoyeon = next(row for row in _named(artists, "Kim Seoyeon"))
    args = ["merge", seoyeon["gy_id"], haru["gy_id"], "--config", config, "--evidence"]
    assert main([*args, "E1 https://example.org/unrelated"]) == 2
    assert "E1 does not hold" in capsys.readouterr().err
    assert main([*args, "H the catalogue photo matches, checked 2099-01-01"]) == 2
    assert "future" in capsys.readouterr().err
    assert len(_artists(dest)) == len(artists)
