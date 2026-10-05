# SPDX-License-Identifier: AGPL-3.0-only
"""Same-person rules E1–E4, team guard T1, candidates X1, and the review queue.

People are fictitious (김하늘 / Haneul Kim and the demo cast). URLs are example.org.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from giye.cli import main
from giye.collect.base import run_configured
from giye.config import load
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import (
    ACTIVITIES_FIELDS,
    ARTISTS_FIELDS,
    LINKS_FIELDS,
    MEMBERSHIP_FIELDS,
    empty_row,
)
from giye.resolve.candidates import x1_candidates
from giye.resolve.cv import read_html_cvs
from giye.resolve.evidence import (
    YEAR_WINDOW,
    cv_mentions,
    event_pattern,
    evidence_e1,
    pattern_table,
    roster_works,
    teams,
    url_key,
)
from giye.resolve.service import resolve, resolve_ledger
from giye.resolve.teams import expand_teams, person_like, team_like, team_person_mismatch

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo"
FIXTURE_CV = ROOT / "tests" / "fixtures" / "resolve" / "cv.html"
UA = "GiyeTest/0.1 (+https://example.org/contact)"


def _config(tmp_path: Path, *, cv_dir: Path | None = None, collectors: bool = False) -> Path:
    cv_line = f'cv_dir = "{cv_dir.as_posix()}"\n' if cv_dir else ""
    modules = ""
    if collectors:
        modules = f'collector_modules = ["{(DEMO / "collectors.py").as_posix()}"]\n'
    path = tmp_path / "giye.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
[archive]
name = "Synthetic media-art field (demo)"
id_prefix = "GY"
territory = "KR"

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{(DEMO / "frames.yml").as_posix()}"

[collect]
user_agent = "{UA}"
min_delay_s = 0.0
{modules}
[collect.offline_roots]
"https://example.org" = "{(DEMO / "fixtures").as_posix()}"

[resolve]
{cv_line}
[resolve.event_patterns]
EXAMPLE-RESIDENCY = "예시 ?레지던시|example residency"
EXAMPLE-WORKSHOP = "예시 ?워크숍|example workshop"
""",
        encoding="utf-8",
    )
    return path


def _ledger(tmp_path: Path, **kwargs) -> Ledger:
    return Ledger.open(load(_config(tmp_path, **kwargs)))


def _artist(lid: str, gy: str, ko: str, en: str = "", note: str = "", aliases: str = "") -> dict[str, str]:
    return empty_row(
        ARTISTS_FIELDS,
        ledger_id=lid,
        gy_id=gy,
        name_ko=ko,
        name_en=en,
        aliases=aliases,
        reviewer_note=note,
        status="STAGED",
    )


def _act(lid: str, origin: str, year: int, role: str = "", title: str = "") -> dict[str, str]:
    return empty_row(
        ACTIVITIES_FIELDS,
        activity_id=f"act-{lid}-{origin}-{year}-{role}",
        ledger_id=lid,
        title=title or origin,
        year=str(year),
        role=role,
        origin=origin,
        activity_type="other",
        source_url="https://example.org/roster",
    )


def _mem(lid: str, frame: str) -> dict[str, str]:
    return empty_row(MEMBERSHIP_FIELDS, ledger_id=lid, frame_code=frame, source_url="https://example.org/roster")


def _link(lid: str, url: str, kind: str = "website") -> dict[str, str]:
    return empty_row(LINKS_FIELDS, link_id=f"lnk-{lid}-{kind}", ledger_id=lid, url=url, link_type=kind)


def _seed(ledger: Ledger, artists: list[dict], activities: list[dict], membership: list[dict], links: list[dict] | None = None) -> None:
    ledger.write("artists", artists, task="test")
    ledger.write("activities", activities, task="test")
    ledger.write("frame_membership", membership, task="test")
    if links:
        ledger.write("links", links, task="test")


def _cv(ledger: Ledger, lid: str, activities: list[dict]) -> None:
    folder = ledger.config.work / "cv_extract"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{lid}.json").write_text(json.dumps({"activities": activities}), encoding="utf-8")


def _notes(ledger: Ledger) -> str:
    return "\n".join(row.get("reviewer_note") or "" for row in ledger.read("artists"))


def test_url_key_and_event_pattern_keep_production_thresholds():
    assert url_key("https://www.artist.example.org/cv") == url_key("http://artist.example.org/")
    assert url_key("https://blog.naver.com/haneul") != url_key("https://blog.naver.com/haru")
    assert url_key("https://blog.naver.com/haneul/index.html") == "blog.naver.com/haneul"
    table = pattern_table((("EXAMPLE-WORKSHOP", "example workshop"),))
    assert event_pattern("EXAMPLE-WORKSHOP-2020", table) == "example workshop"
    assert event_pattern("NOT-A-FRAME", table) is None
    # A later layer replaces a prefix and keeps the earlier key's position.
    replaced = pattern_table(table.items(), (("EXAMPLE-WORKSHOP", "workshop only"),))
    assert event_pattern("EXAMPLE-WORKSHOP", replaced) == "workshop only"
    assert YEAR_WINDOW == 1


def test_e2_uses_the_frame_code_year_when_the_code_ends_in_a_year():
    patterns = pattern_table((("EXAMPLE-RESIDENCY", "example residency"),))
    # The activity says 2010. The frame code says 2014. The edition year is 2014.
    hit = cv_mentions(
        [{"title": "Example Residency", "venue": "", "year": 2010}],
        "EXAMPLE-RESIDENCY-2014",
        [2014],
        patterns,
    )
    assert hit is None
    assert cv_mentions(
        [{"title": "Example Residency", "venue": "", "year": 2015}],
        "EXAMPLE-RESIDENCY-2014",
        [2014],
        patterns,
    )
    assert (
        cv_mentions(
            [{"title": "Example Residency", "venue": "", "year": 2012}],
            "EXAMPLE-RESIDENCY-2014",
            [2014],
            patterns,
        )
        is None
    )


def test_e3_counts_only_bracketed_titles_and_e4_reads_the_team_role():
    rows = [
        {"origin": "FRAME", "year": "2019", "title": "FRAME", "role": "〈푸른 신호〉"},
        {"origin": "FRAME", "year": "2019", "title": "푸른 신호", "role": ""},
        {"origin": "cv:SRC", "year": "2019", "title": "〈무시〉", "role": ""},
        {"origin": "FRAME", "year": "2019", "title": "FRAME", "role": "팀: 노을크루"},
    ]
    assert roster_works(rows) == {("푸른신호", 2019)}
    assert teams(rows) == {"노을크루"}


def test_t1_team_word_members_note_and_person_alias_list():
    assert person_like("김하늘")
    assert not person_like("노을 스튜디오")
    assert team_like({"name_ko": "노을 스튜디오", "name_en": "", "reviewer_note": "", "aliases": ""}) == "team_name"
    assert team_like({"name_ko": "배수아", "name_en": "", "reviewer_note": "members=김솔|박솔", "aliases": ""}) == "members"
    assert team_like({"name_ko": "김하늘", "name_en": "", "reviewer_note": "", "aliases": ""}) == ""
    person = {"name_ko": "김하늘", "name_en": "", "reviewer_note": "", "aliases": ""}
    team = {"name_ko": "노을 스튜디오", "name_en": "Noeul Studio", "reviewer_note": "", "aliases": ""}
    assert team_person_mismatch(person, team)
    assert not team_person_mismatch(team, {"name_ko": "루멘 랩", "name_en": "", "reviewer_note": "", "aliases": ""})


def test_t1_team_word_in_a_surname_shaped_name_is_a_team():
    # Four syllables starting with a listed surname, but 그룹 says it is a group.
    assert person_like("태별그룹")
    group = {"name_ko": "태별그룹", "name_en": "", "reviewer_note": "", "aliases": ""}
    assert team_like(group) == "team_name"
    person = {"name_ko": "김하늘", "name_en": "", "reviewer_note": "", "aliases": ""}
    assert team_person_mismatch(group, person)
    # A person whose English name mentions a studio stays a person.
    assert team_like({"name_ko": "김하늘", "name_en": "Haneul Kim Studio", "reviewer_note": "", "aliases": ""}) == ""


def test_surnames_from_the_census_rule_are_personal_names():
    # 라, 계 and 시 each have more than 2,000 bearers in the 2015 census.
    for name in ("라도윤", "계하늘", "시은솔"):
        assert person_like(name)
    assert not person_like("휘바람")


def test_e1_merges_a_shared_website_and_refuses_a_different_host(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [
            _artist("LED-a", "GY-000001", "김하늘"),
            _artist("LED-b", "GY-000002", "김하늘", "Kim Haneul"),
            _artist("LED-c", "GY-000003", "이하루"),
        ],
        [_act("LED-a", "EXAMPLE-RESIDENCY", 2019), _act("LED-b", "EXAMPLE-WORKSHOP", 2021), _act("LED-c", "EXAMPLE-WORKSHOP", 2021)],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-WORKSHOP"), _mem("LED-c", "EXAMPLE-WORKSHOP")],
        [
            _link("LED-a", "https://haneul.example.org"),
            _link("LED-b", "https://www.haneul.example.org/cv"),
            _link("LED-c", "https://haru.example.org"),
        ],
    )
    result = resolve_ledger(ledger)
    assert any(item.rule == "E1" and "haneul.example.org" in item.evidence for item in result.merges)
    artists = {row["name_ko"]: row for row in ledger.read("artists") if row["name_ko"] == "김하늘"}
    assert len(artists) == 1
    note = _notes(ledger)
    assert "merge_evidence=E1 same website haneul.example.org" in note
    assert "rule=E1" in note
    assert any(row["name_ko"] == "이하루" for row in ledger.read("artists"))
    assert ledger.read("gy_retired")


def test_e1_ignores_social_links(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-a", "GY-000001", "김하늘"), _artist("LED-b", "GY-000002", "김하늘", "Other")],
        [_act("LED-a", "A", 2019), _act("LED-b", "B", 2020)],
        [_mem("LED-a", "A"), _mem("LED-b", "B")],
        [_link("LED-a", "https://video.example.org/a", "social"), _link("LED-b", "https://video.example.org/a", "social")],
    )
    result = resolve_ledger(ledger)
    assert not any(item.rule == "E1" for item in result.merges)
    assert len(ledger.read("artists")) == 2


def test_t1_blocks_a_team_and_a_person_who_share_a_website(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [
            _artist("LED-person", "GY-000001", "배수아"),
            _artist("LED-team", "GY-000002", "배수아", "Sua Bae", note="members=김솔|박솔"),
        ],
        [_act("LED-person", "EXAMPLE-RESIDENCY", 2019), _act("LED-team", "EXAMPLE-WORKSHOP", 2021)],
        [_mem("LED-person", "EXAMPLE-RESIDENCY"), _mem("LED-team", "EXAMPLE-WORKSHOP")],
        [_link("LED-person", "https://sua.example.org"), _link("LED-team", "https://sua.example.org")],
    )
    result = resolve_ledger(ledger)
    assert not result.merges
    assert ("LED-person", "LED-team") in result.blocked_team
    assert {row["ledger_id"] for row in ledger.read("artists") if row["name_ko"] == "배수아"} == {"LED-person", "LED-team"}
    assert not any(row["reason"] == "possible_same_person" and "LED-person" in row["detail"] for row in ledger.read("review_queue"))


def test_two_team_rows_may_merge_on_e1(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [
            _artist("LED-a", "GY-000001", "노을 스튜디오"),
            _artist("LED-b", "GY-000002", "노을 스튜디오", "Noeul Studio"),
        ],
        [_act("LED-a", "A", 2019), _act("LED-b", "B", 2020), _act("LED-b", "B", 2021)],
        [_mem("LED-a", "A"), _mem("LED-b", "B")],
        [_link("LED-a", "https://noeul.example.org"), _link("LED-b", "https://noeul.example.org/")],
    )
    result = resolve_ledger(ledger)
    assert any(item.rule == "E1" for item in result.merges)
    assert len([row for row in ledger.read("artists") if "노을" in row["name_ko"]]) == 1


def test_e2_merges_when_the_cv_names_the_roster_year_and_not_two_years_off(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-cv", "GY-000001", "표은솔", "Pyo Eunsol"), _artist("LED-roster", "GY-000002", "표은솔")],
        [_act("LED-cv", "EXAMPLE-RESIDENCY", 2019), _act("LED-roster", "EXAMPLE-WORKSHOP", 2021)],
        [_mem("LED-cv", "EXAMPLE-RESIDENCY"), _mem("LED-roster", "EXAMPLE-WORKSHOP")],
    )
    _cv(ledger, "LED-cv", [{"title": "예시 워크숍", "venue": "Example Workshop", "year": 2021}])
    result = resolve_ledger(ledger)
    assert any(item.rule == "E2" and "EXAMPLE-WORKSHOP" in item.evidence and "2021" in item.evidence for item in result.merges)
    assert "rule=E2" in _notes(ledger)
    assert len([row for row in ledger.read("artists") if row["name_ko"] == "표은솔"]) == 1

    other = _ledger(tmp_path / "far")
    _seed(
        other,
        [_artist("LED-cv", "GY-000001", "표은솔", "Pyo Eunsol"), _artist("LED-roster", "GY-000002", "표은솔")],
        [_act("LED-cv", "EXAMPLE-RESIDENCY", 2019), _act("LED-roster", "EXAMPLE-WORKSHOP", 2021)],
        [_mem("LED-cv", "EXAMPLE-RESIDENCY"), _mem("LED-roster", "EXAMPLE-WORKSHOP")],
    )
    _cv(other, "LED-cv", [{"title": "예시 워크숍", "venue": "", "year": 2018}])
    result = resolve_ledger(other)
    assert not any(item.rule == "E2" for item in result.merges)
    assert len(other.read("artists")) == 2


def test_e3_merges_a_shared_bracketed_work_and_not_an_unbracketed_title(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-a", "GY-000001", "한별"), _artist("LED-b", "GY-000002", "한별", "Han Byeol")],
        [
            _act("LED-a", "EXAMPLE-RESIDENCY", 2019, role="〈푸른 신호〉"),
            _act("LED-b", "EXAMPLE-RESIDENCY", 2020, role="〈푸른 신호〉"),
        ],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-RESIDENCY")],
    )
    result = resolve_ledger(ledger)
    assert any(item.rule == "E3" and "푸른신호" in item.evidence for item in result.merges)

    plain = _ledger(tmp_path / "plain")
    _seed(
        plain,
        [_artist("LED-a", "GY-000001", "한별"), _artist("LED-b", "GY-000002", "한별", "Han Byeol")],
        [
            _act("LED-a", "EXAMPLE-RESIDENCY", 2019, role="푸른 신호"),
            _act("LED-b", "EXAMPLE-WORKSHOP", 2020, role="푸른 신호"),
        ],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-WORKSHOP")],
    )
    result = resolve_ledger(plain)
    assert not any(item.rule == "E3" for item in result.merges)
    assert len(plain.read("artists")) == 2


def test_e3_year_gap_of_two_does_not_merge(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-a", "GY-000001", "한별"), _artist("LED-b", "GY-000002", "한별", "Han Byeol")],
        [
            _act("LED-a", "EXAMPLE-RESIDENCY", 2019, role="〈푸른 신호〉"),
            _act("LED-b", "EXAMPLE-WORKSHOP", 2021, role="〈푸른 신호〉"),
        ],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-WORKSHOP")],
    )
    result = resolve_ledger(ledger)
    assert not any(item.rule == "E3" for item in result.merges)


def test_e4_merges_a_shared_team_credit_and_not_two_team_names(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-a", "GY-000001", "문지호"), _artist("LED-b", "GY-000002", "문지호", "Jiho Moon")],
        [
            _act("LED-a", "EXAMPLE-RESIDENCY", 2019, role="팀: 노을크루"),
            _act("LED-b", "EXAMPLE-WORKSHOP", 2021, role="팀: 노을크루"),
        ],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-WORKSHOP")],
    )
    result = resolve_ledger(ledger)
    assert any(item.rule == "E4" and "노을크루" in item.evidence for item in result.merges)
    assert "rule=E4" in _notes(ledger)

    other = _ledger(tmp_path / "other-team")
    _seed(
        other,
        [_artist("LED-a", "GY-000001", "문지호"), _artist("LED-b", "GY-000002", "문지호", "Jiho Moon")],
        [
            _act("LED-a", "EXAMPLE-RESIDENCY", 2019, role="팀: 노을크루"),
            _act("LED-b", "EXAMPLE-WORKSHOP", 2021, role="팀: 다른크루"),
        ],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-WORKSHOP")],
    )
    result = resolve_ledger(other)
    assert not any(item.rule == "E4" for item in result.merges)
    assert len(other.read("artists")) == 2


def test_same_name_without_evidence_is_queued_and_not_merged(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-a", "GY-000001", "최민수"), _artist("LED-b", "GY-000002", "최민수", "Minsu Choi")],
        [_act("LED-a", "EXAMPLE-RESIDENCY", 2019), _act("LED-b", "EXAMPLE-WORKSHOP", 2021)],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-WORKSHOP")],
    )
    result = resolve_ledger(ledger)
    assert not result.merges
    assert len(ledger.read("artists")) == 2
    queued = ledger.read("review_queue")
    assert len(queued) == 1
    assert queued[0]["reason"] == "possible_same_person"
    assert queued[0]["status"] == "open"
    assert "LED-a" in queued[0]["detail"] and "LED-b" in queued[0]["detail"]
    assert "rule=same_script_exact" in queued[0]["detail"]
    again = resolve_ledger(ledger)
    assert not again.merges
    assert len(ledger.read("review_queue")) == 1


def test_x1_queues_without_evidence_and_merges_with_e1(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-ko", "GY-000001", "서지우"), _artist("LED-en", "GY-000002", "Jiwoo Seo", "Jiwoo Seo")],
        [_act("LED-ko", "EXAMPLE-RESIDENCY", 2019), _act("LED-en", "EXAMPLE-WORKSHOP", 2022)],
        [_mem("LED-ko", "EXAMPLE-RESIDENCY"), _mem("LED-en", "EXAMPLE-WORKSHOP")],
    )
    assert x1_candidates(ledger.read("artists")) == [("LED-ko", "LED-en")]
    result = resolve_ledger(ledger)
    assert not result.merges
    detail = ledger.read("review_queue")[0]["detail"]
    assert detail.startswith("romanization match: 서지우 ~ Jiwoo Seo")
    assert "LED-en" in detail

    shared = _ledger(tmp_path / "site")
    _seed(
        shared,
        [_artist("LED-ko", "GY-000001", "서지우"), _artist("LED-en", "GY-000002", "Jiwoo Seo", "Jiwoo Seo")],
        [_act("LED-ko", "EXAMPLE-RESIDENCY", 2019), _act("LED-en", "EXAMPLE-WORKSHOP", 2022)],
        [_mem("LED-ko", "EXAMPLE-RESIDENCY"), _mem("LED-en", "EXAMPLE-WORKSHOP")],
        [_link("LED-ko", "https://jiwoo.example.org"), _link("LED-en", "https://jiwoo.example.org")],
    )
    result = resolve_ledger(shared)
    assert any(item.rule == "X1+E1" and "jiwoo.example.org" in item.evidence for item in result.merges)
    assert "rule=X1+E1" in _notes(shared)
    assert len(shared.read("artists")) == 1


def test_x1_same_frame_is_not_queued(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-ko", "GY-000001", "김하늘"), _artist("LED-en", "GY-000002", "Haneul Kim", "Haneul Kim")],
        [_act("LED-ko", "EXAMPLE-RESIDENCY", 2019), _act("LED-en", "EXAMPLE-RESIDENCY", 2020)],
        [_mem("LED-ko", "EXAMPLE-RESIDENCY"), _mem("LED-en", "EXAMPLE-RESIDENCY")],
    )
    result = resolve_ledger(ledger)
    assert not result.merges
    assert not result.queued
    assert ledger.read("review_queue") == []


def test_pinned_apart_blocks_e3(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [
            _artist("LED-a", "GY-000001", "한별", note="identity=demo:one"),
            _artist("LED-b", "GY-000002", "한별", "Han Byeol", note="identity=demo:two"),
        ],
        [
            _act("LED-a", "EXAMPLE-RESIDENCY", 2019, role="〈푸른 신호〉"),
            _act("LED-b", "EXAMPLE-WORKSHOP", 2020, role="〈푸른 신호〉"),
        ],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-WORKSHOP")],
    )
    result = resolve_ledger(ledger)
    assert not any(item.rule == "E3" for item in result.merges)
    assert len(ledger.read("artists")) == 2


def test_expand_teams_reuses_a_person_by_attachment_not_exact_name(tmp_path: Path):
    """English-only member credit joins the existing row by A2, not a duplicate.

    The stored Korean name is not the credit string, so an exact ``name_ko``
    lookup misses. The English tokens agree, which is attachment rule A2.
    """
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [
            _artist("LED-team", "GY-000001", "노을 스튜디오", note="members=Lumen Lab"),
            _artist("LED-lab", "GY-000002", "루멘 랩", "Lumen Lab"),
        ],
        [_act("LED-team", "EXAMPLE-RESIDENCY", 2019, title="EXAMPLE-RESIDENCY")],
        [_mem("LED-team", "EXAMPLE-RESIDENCY")],
    )
    assert expand_teams(ledger) == []
    assert {row["ledger_id"] for row in ledger.read("artists")} == {"LED-team", "LED-lab"}
    assert any(
        row["ledger_id"] == "LED-lab" and row["frame_code"] == "EXAMPLE-RESIDENCY"
        for row in ledger.read("frame_membership")
    )


def test_expand_teams_does_not_guess_a_personal_name_on_another_programme(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [
            _artist("LED-team", "GY-000001", "노을 스튜디오", note="members=김하늘"),
            _artist("LED-other", "GY-000002", "김하늘", "Haneul Kim"),
        ],
        [
            _act("LED-team", "EXAMPLE-RESIDENCY", 2019),
            _act("LED-other", "EXAMPLE-WORKSHOP", 2020),
        ],
        [_mem("LED-team", "EXAMPLE-RESIDENCY"), _mem("LED-other", "EXAMPLE-WORKSHOP")],
    )
    assert expand_teams(ledger) == []
    assert len(ledger.read("artists")) == 2
    assert not any(
        row["ledger_id"] == "LED-other" and row["frame_code"] == "EXAMPLE-RESIDENCY"
        for row in ledger.read("frame_membership")
    )


def test_expand_teams_adds_members_once_and_does_not_merge_them(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-team", "GY-000001", "노을 스튜디오", note="members=김바다|박바다")],
        [_act("LED-team", "EXAMPLE-RESIDENCY", 2019, title="EXAMPLE-RESIDENCY")],
        [_mem("LED-team", "EXAMPLE-RESIDENCY")],
    )
    created = expand_teams(ledger)
    assert len(created) == 2
    names = {row["name_ko"] for row in ledger.read("artists")}
    assert names == {"노을 스튜디오", "김바다", "박바다"}
    roles = {row["role"] for row in ledger.read("activities") if row["ledger_id"] != "LED-team"}
    assert roles == {"팀: 노을 스튜디오"}
    assert expand_teams(ledger) == []
    assert len(ledger.read("artists")) == 3
    assert len(ledger.read("activities")) == 3


def test_group_credit_expands_and_a_split_person_does_not(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-group", "GY-000001", "루멘 랩", note="; group; raw=루멘 랩 (김하늘, 박서연)")],
        [_act("LED-group", "EXAMPLE-RESIDENCY", 2019)],
        [_mem("LED-group", "EXAMPLE-RESIDENCY")],
    )
    expand_teams(ledger)
    assert {row["name_ko"] for row in ledger.read("artists")} == {"루멘 랩", "김하늘", "박서연"}

    person = _ledger(tmp_path / "person")
    _seed(
        person,
        [_artist("LED-one", "GY-000001", "김하늘", note="; group; raw=루멘 랩 (김하늘)")],
        [_act("LED-one", "EXAMPLE-RESIDENCY", 2019)],
        [_mem("LED-one", "EXAMPLE-RESIDENCY")],
    )
    assert expand_teams(person) == []
    assert len(person.read("artists")) == 1


def test_reopen_a_done_item_whose_pair_was_not_merged(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-a", "GY-000001", "최민수"), _artist("LED-b", "GY-000002", "최민수", "Minsu Choi")],
        [_act("LED-a", "EXAMPLE-RESIDENCY", 2019), _act("LED-b", "EXAMPLE-WORKSHOP", 2021)],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-WORKSHOP")],
    )
    ledger.write(
        "review_queue",
        [
            {
                "queue_id": "q-1",
                "ledger_id": "LED-a",
                "reason": "possible_same_person",
                "detail": "LED-a shares a name with LED-b (rule=same_script_exact)",
                "status": "done",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
        task="test",
    )
    result = resolve_ledger(ledger)
    assert "q-1" in result.reopened
    item = ledger.read("review_queue")[0]
    assert item["status"] == "open"
    assert "reopened=wrong_close" in item["detail"]


def test_html_cv_binds_by_name(tmp_path: Path):
    people = read_html_cvs(FIXTURE_CV.parent)
    assert {person["name_ko"] for person in people} >= {"표은솔", "김하늘"}
    ledger = _ledger(tmp_path, cv_dir=FIXTURE_CV.parent)
    _seed(
        ledger,
        [_artist("LED-cv", "GY-000001", "표은솔", "Pyo Eunsol"), _artist("LED-roster", "GY-000002", "표은솔")],
        [_act("LED-cv", "EXAMPLE-RESIDENCY", 2019), _act("LED-roster", "EXAMPLE-WORKSHOP", 2021)],
        [_mem("LED-cv", "EXAMPLE-RESIDENCY"), _mem("LED-roster", "EXAMPLE-WORKSHOP")],
    )
    result = resolve_ledger(ledger)
    assert any(item.rule == "E2" for item in result.merges)


def test_evidence_e1_requires_the_same_key():
    sites = {"LED-a": {"haneul.example.org"}, "LED-b": {"haneul.example.org"}, "LED-c": {"haru.example.org"}}
    assert evidence_e1("LED-a", "LED-b", sites) == "E1 same website haneul.example.org"
    assert evidence_e1("LED-a", "LED-c", sites) is None


def test_demo_resolve_fires_each_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def boom(self, url, *args, **kwargs):
        raise AssertionError(f"network used: {url}")

    monkeypatch.setattr(requests.Session, "get", boom)
    path = _config(tmp_path, cv_dir=DEMO / "fixtures" / "cv", collectors=True)
    config = load(path)
    run_configured(config, collected_at="2026-10-04", run_id="2026-10-04T00:00:00Z")
    assert main(["resolve", "--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert "E1 same website dawoon.example.org" in out
    assert "EXAMPLE-WORKSHOP" in out and "E2" in out
    assert "푸른신호" in out
    assert "노을크루" in out
    assert "romanization match: 서지우 ~ Jiwoo Seo" in out
    assert "same_name_blocked_team" in out
    ledger = Ledger.open(config)
    artists = ledger.read("artists")
    notes = "\n".join(row.get("reviewer_note") or "" for row in artists)

    def rows_named(name: str) -> list[dict]:
        return [row for row in artists if row["name_ko"] == name]

    assert len(rows_named("정다운")) == 1
    assert "rule=E1" in notes
    assert len(rows_named("표은솔")) == 1
    assert "rule=E2" in notes
    assert len(rows_named("한별")) == 1
    assert "rule=E3" in notes
    assert len(rows_named("문지호")) == 1
    assert "rule=E4" in notes
    assert len(rows_named("최민수")) == 2
    assert len(rows_named("배수아")) == 2
    # The forum row attaches to the workshop spelling (A2) before resolve.
    # That row then has the extra roster activity, so X1+E2 keeps it.
    # The demo path keeps 김하늘 because the Korean CV is applied first.
    assert len(rows_named("김하늘")) == 0
    haneul = rows_named("Haneul Kim")[0]
    assert haneul["name_en"] == "Haneul Kim"
    assert "rule=X1+E2" in (haneul.get("reviewer_note") or "")
    assert not any(row["name_ko"] == "Kim Haneul" for row in artists)
    forum = [
        row for row in ledger.read("frame_membership") if row["frame_code"] == "EXAMPLE-FORUM-2023"
    ]
    assert forum and forum[0]["attach_rule"] == "A2"
    assert "X1+E2" in out and "EXAMPLE-RESIDENCY-2019" in out
    assert any(row["name_ko"] == "노을 스튜디오" for row in artists)
    assert any(row["name_ko"] == "김바다" for row in artists)
    bae = tuple(sorted(row["ledger_id"] for row in rows_named("배수아")))
    assert f"same_name_blocked_team {bae[0]} {bae[1]}" in out
    again = resolve(config)
    assert again.merges == []
    assert again.expanded == []


def test_personal_name_test_comes_from_the_language_module(tmp_path: Path):
    """A3/A4/T1 read ``personal_name`` from the configured module, not a Korean list in the rules."""
    from giye.config import load
    from giye.normalize.language import language_for
    from tests.toy_language import Toy

    toy = Toy()
    assert person_like("김하늘") and not person_like("김하늘", toy)
    assert person_like("qamo", toy) and not person_like("qamo")
    duo = {"name_ko": "물결", "name_en": "", "reviewer_note": "", "aliases": "qamo|qibe"}
    assert team_like(duo) == ""
    assert team_like(duo, language=toy) == "aliases"
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Toy"
id_prefix = "GY"
[paths]
data = "{(tmp_path / "data").as_posix()}"
[normalize]
language_module = "tests.toy_language:Toy"
""",
        encoding="utf-8",
    )
    assert language_for(load(path)).personal_name("qamo")
