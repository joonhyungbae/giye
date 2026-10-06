# SPDX-License-Identifier: AGPL-3.0-only
"""A manual merge's evidence is checked against the ledger, not only its form.

The cases reproduce a software review: an ``E1`` citing a site neither record
lists, an ``H`` dated in the future, and free text or an unknown rule id passed
to the public ``Ledger.merge``. People are fictitious; URLs are example.org.
"""

from __future__ import annotations

import pytest

from giye.audit.sample import parse_markers
from giye.cli import main
from giye.config import GiyeError
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import CV_SOURCES_FIELDS, empty_row
from giye.resolve.candidates import absorption_map, merged_drop_ids
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
    # A site both list is not enough: 이하루 and 김서연 have no name in common
    # (review round 6, MAJOR-1: a duo site lists both members).
    with pytest.raises(GiyeError, match="names to overlap"):
        merge_people(ledger, "LED-a", "LED-b", evidence="E1 https://haru.example.org/cv")
    # X1 on top needs the name keys to meet; Lee Haru and Kim Seoyeon do not.
    with pytest.raises(GiyeError, match="name keys"):
        merge_people(ledger, "LED-a", "LED-b", evidence="X1+E1 https://haru.example.org/cv")
    assert _live(ledger) == {"LED-a", "LED-b"}


def test_e1_holds_for_overlapping_names_on_a_site_only_they_list(tmp_path):
    links = [_link("LED-a", "https://haru.example.org/"), _link("LED-b", "https://www.haru.example.org/about")]
    ledger = _pair(tmp_path, links=links, names=(("이하루", "Lee Haru"), ("이하루", "")))
    assert verify_merge_evidence(ledger, "LED-a", "LED-b", "E1 https://haru.example.org/cv") == "E1"
    keep, _drop = merge_people(ledger, "LED-a", "LED-b", evidence="E1 https://haru.example.org/cv")
    assert _live(ledger) == {keep}
    assert "rule=E1" in ledger.read("artists")[0]["reviewer_note"]


def test_e1_refuses_a_site_a_third_record_also_lists(tmp_path):
    ledger = _pair(tmp_path, names=(("이하루", "Lee Haru"), ("이하루", "")))
    artists = ledger.read("artists")
    artists.append(_artist("LED-c", "GY-000003", "정다온", "Daon Jeong"))
    ledger.write("artists", artists, task="test")
    ledger.write(
        "links",
        [_link(lid, "https://studio.example.org/") for lid in ("LED-a", "LED-b", "LED-c")],
        task="test",
    )
    with pytest.raises(GiyeError, match="another record"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E1 https://studio.example.org/")


def test_e1_refuses_two_members_of_a_duo_site(tmp_path):
    """Review round 6: 김솔 and Lee Haru, a shared duo website, no name in common."""
    links = [_link("LED-a", "https://duo.example.org/"), _link("LED-b", "https://duo.example.org/")]
    ledger = _pair(tmp_path, links=links, names=(("김솔", ""), ("Lee Haru", "Lee Haru")))
    with pytest.raises(GiyeError, match="names to overlap"):
        merge_people(ledger, "LED-a", "LED-b", evidence="E1 https://duo.example.org/")
    assert _live(ledger) == {"LED-a", "LED-b"}


def test_e4_refuses_two_members_of_one_team(tmp_path):
    """Review round 6: 김바다 and 박바다 are both credited in team 노을 스튜디오."""
    ledger = _pair(
        tmp_path,
        names=(("김바다", ""), ("박바다", "")),
        activities=[
            _act("LED-a", "EXAMPLE-WORKSHOP-2020", 2020, role="팀: 노을 스튜디오"),
            _act("LED-b", "EXAMPLE-RESIDENCY-2019", 2019, role="팀: 노을 스튜디오"),
        ],
        membership=[_mem("LED-a", "EXAMPLE-WORKSHOP-2020"), _mem("LED-b", "EXAMPLE-RESIDENCY-2019")],
    )
    with pytest.raises(GiyeError, match="candidate pair"):
        merge_people(ledger, "LED-a", "LED-b", evidence="E4 〈노을 스튜디오〉 https://example.org/roster")
    assert _live(ledger) == {"LED-a", "LED-b"}


def test_e2_refuses_a_cv_listing_an_edition_both_records_are_on(tmp_path):
    """Review round 6: two people on one residency edition; one CV lists that edition."""
    ledger = _pair(
        tmp_path,
        names=(("한별", ""), ("한별", "Han Byeol")),
        activities=[
            _act("LED-a", "EXAMPLE-RESIDENCY-2019", 2019),
            _act("LED-b", "EXAMPLE-RESIDENCY-2019", 2019),
        ],
        membership=[_mem("LED-a", "EXAMPLE-RESIDENCY-2019"), _mem("LED-b", "EXAMPLE-RESIDENCY-2019")],
    )
    _cv_sources(ledger, [("CV-T-1", "LED-a")])
    _cv(ledger, "LED-a", [{"title": "Example Residency", "venue": "", "year": 2019, "source_id": "CV-T-1"}])
    with pytest.raises(GiyeError, match="its owner is not on"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E2 CV-T-1")


def test_e2_refuses_a_pair_whose_names_do_not_meet(tmp_path):
    """Review round 6: 박서연's CV cited to merge her with 정다운."""
    ledger = _pair(tmp_path, names=(("박서연", ""), ("정다운", "")))
    _cv_sources(ledger, [("CV-T-1", "LED-a")])
    _cv(ledger, "LED-a", [{"title": "Example Residency", "venue": "", "year": 2019, "source_id": "CV-T-1"}])
    with pytest.raises(GiyeError, match="candidate pair"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E2 CV-T-1")


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
    assert verify_merge_evidence(ledger, "LED-a", "LED-b", "E4 〈노을크루〉 https://example.org/roster") == "E4"
    with pytest.raises(GiyeError, match="not the team"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E4 〈바다크루〉 https://example.org/roster")


def test_e3_and_e4_match_the_bracketed_name_exactly(tmp_path):
    """Software review 5, minor 3: free text that contains the title is not a citation of it."""
    ledger = _pair(
        tmp_path,
        names=(("한별", ""), ("한별", "Han Byeol")),
        activities=[
            _act("LED-a", "EXAMPLE-WORKSHOP-2020", 2020, role="팀: 노을크루", title="〈푸른 신호〉"),
            _act("LED-b", "EXAMPLE-RESIDENCY-2019", 2019, role="팀: 노을크루", title="〈푸른 신호〉"),
        ],
        membership=[_mem("LED-a", "EXAMPLE-WORKSHOP-2020"), _mem("LED-b", "EXAMPLE-RESIDENCY-2019")],
    )
    # The bracketed title alone is a citation for E3.
    assert verify_merge_evidence(ledger, "LED-a", "LED-b", "E3 〈푸른 신호〉") == "E3"
    with pytest.raises(GiyeError, match="in brackets"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E3 EXAMPLE-WORKSHOP-2020 not the work 푸른 신호 at all")
    with pytest.raises(GiyeError, match="not the work"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E3 〈푸른 신호등〉 EXAMPLE-WORKSHOP-2020")
    with pytest.raises(GiyeError, match="in brackets"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E4 team 노을크루 https://example.org/roster")
    # A bracketed team still needs a citation of its own.
    with pytest.raises(GiyeError, match="names no citation"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E4 〈노을크루〉")


def test_e3_and_e4_refused_when_the_rosters_share_nothing(tmp_path):
    ledger = _pair(tmp_path)
    with pytest.raises(GiyeError, match="no work"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E3 〈푸른 신호〉 https://example.org/roster")
    with pytest.raises(GiyeError, match="no team"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E4 〈노을크루〉 https://example.org/roster")


def test_h_needs_a_past_date_and_a_reason_of_a_few_words(tmp_path):
    ledger = _pair(tmp_path)
    artists = ledger.read("artists")
    for row, when in zip(artists, ("2025-03-01", "2025-11-20T08:00:00Z")):
        row["collected_at"] = when
    ledger.write("artists", artists, task="test")
    for bad, reason in (
        ("H same face in both catalogues 2099-01-01", "future"),
        ("H same 2026-01-15", "at least 3 words"),
        ("H same face in both catalogues 2026-01-15 and 2026-02-30", "not a calendar date"),
        # Software review 5, minor 2: who judged, and not before the records existed.
        ("H same face in both catalogues 2026-01-15", "by <name or role>"),
        ("H x y z by author 1900-01-01", r"before the earlier record was collected \(2025-03-01\)"),
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


def test_public_ledger_merge_refuses_a_team_and_a_person(tmp_path):
    """Review round 5: ``Ledger.merge`` merged a team into a person with a valid-looking H."""
    ledger = _pair(tmp_path, names=(("김하늘", "Kim Haneul"), ("노을 스튜디오", "Noeul Studio")))
    with pytest.raises(GiyeError, match="T1"):
        ledger.merge("LED-a", "LED-b", evidence="H a b c 2026-01-01", rule="H")
    assert _live(ledger) == {"LED-a", "LED-b"}


def test_cli_refuses_an_unchecked_e1_on_the_demo(tmp_path, capsys):
    dest = _copy(tmp_path)
    config = str(dest / "giye.toml")
    artists = _artists(dest)
    haru = next(row for row in _named(artists, "Lee Haru"))
    seoyeon = next(row for row in _named(artists, "Kim Seoyeon"))
    args = ["merge", seoyeon["gy_id"], haru["gy_id"], "--config", config, "--evidence"]
    assert main([*args, "E1 https://example.org/unrelated"]) == 2
    assert "E1 does not hold" in capsys.readouterr().err
    assert main([*args, "H the catalogue photo matches, checked by author 2099-01-01"]) == 2
    assert "future" in capsys.readouterr().err
    assert len(_artists(dest)) == len(artists)


def test_h_dates_must_be_written_as_iso_dates(tmp_path):
    ledger = _pair(tmp_path)
    for bad in (
        "H same face in both catalogues by author 2026-01-15, seen again 01/01/2099",
        "H same face in both catalogues by author 2026.01.15",
    ):
        with pytest.raises(GiyeError, match="YYYY-MM-DD"):
            merge_people(ledger, "LED-a", "LED-b", evidence=bad)
    assert _live(ledger) == {"LED-a", "LED-b"}


def test_h_merge_of_two_lines_of_one_edition_warns_and_is_recorded(tmp_path):
    ledger = _pair(
        tmp_path,
        membership=[_mem("LED-a", "EXAMPLE-WORKSHOP-2021"), _mem("LED-b", "EXAMPLE-WORKSHOP-2021")],
    )
    with pytest.warns(UserWarning, match="EXAMPLE-WORKSHOP-2021"):
        merge_people(ledger, "LED-a", "LED-b", evidence="H same face in both catalogues, checked by the author 2026-01-15")
    [kept] = ledger.read("artists")
    assert "listed separately on EXAMPLE-WORKSHOP-2021" in kept["reviewer_note"]


def test_h_merge_of_two_members_of_one_team_warns_and_is_recorded(tmp_path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [
            _artist("LED-a", "GY-000001", "이하루", "Lee Haru", note="team=Example Duo"),
            _artist("LED-b", "GY-000002", "김서연", "Kim Seoyeon", note="team=Example Duo"),
        ],
        [_act("LED-a", "EXAMPLE-WORKSHOP-2021", 2021), _act("LED-b", "EXAMPLE-RESIDENCY-2019", 2019)],
        [_mem("LED-a", "EXAMPLE-WORKSHOP-2021"), _mem("LED-b", "EXAMPLE-RESIDENCY-2019")],
    )
    with pytest.warns(UserWarning, match="Example Duo"):
        merge_people(ledger, "LED-a", "LED-b", evidence="H same face in both catalogues, checked by the author 2026-01-15")
    [kept] = ledger.read("artists")
    assert "both members of team Example Duo" in kept["reviewer_note"]


def test_a_chain_keeps_every_merge_marker_and_its_evidence(tmp_path):
    # A absorbs B on E1, then C absorbs A on H. C must still say B was merged,
    # on which evidence and rule, or the B merge is no longer traceable.
    links = [_link("LED-a", "https://haru.example.org/"), _link("LED-b", "https://www.haru.example.org/about")]
    ledger = _pair(tmp_path, links=links, names=(("이하루", "Lee Haru"), ("이하루", "")))
    artists = ledger.read("artists")
    artists.append(_artist("LED-c", "GY-000003", "정다온", "Daon Jeong"))
    ledger.write("artists", artists, task="test")
    merge_people(ledger, "LED-a", "LED-b", evidence="E1 https://haru.example.org/cv")
    h = "H the artist confirmed both spellings by letter, recorded by author 2026-01-15"
    merge_people(ledger, "LED-c", "LED-a", evidence=h)

    rows = ledger.read("artists")
    assert _live(ledger) == {"LED-c"}
    note = rows[0]["reviewer_note"]
    assert parse_markers(note) == [
        ("LED-b", "E1 https://haru.example.org/cv", "E1"),
        ("LED-a", h, "H"),
    ]
    # Each marker appears once.
    assert note.count("merged LED-b") == 1 and note.count("merged LED-a") == 1
    assert merged_drop_ids(rows) == {"LED-c": {"LED-a", "LED-b"}}
    assert absorption_map(rows) == {"LED-a": "LED-c", "LED-b": "LED-c"}
    # Both retired ids redirect in one step to the final survivor.
    assert ledger.redirects() == {"GY-000001": "GY-000003", "GY-000002": "GY-000003"}
