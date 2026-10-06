# SPDX-License-Identifier: AGPL-3.0-only
"""Same-person rules E1–E4, team guard T1, candidates X1, and the review queue.

People are fictitious (김하늘 / Haneul Kim and the demo cast). URLs are example.org.
"""

from __future__ import annotations

import json
import re
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


def test_distinct_x1_decision_is_not_queued_again(tmp_path: Path):
    """A distinct decision stays decided across two resolves. No second queue item."""
    from giye.resolve.decide import decide_queue

    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-ko", "GY-000001", "서지우"), _artist("LED-en", "GY-000002", "Jiwoo Seo", "Jiwoo Seo")],
        [_act("LED-ko", "EXAMPLE-RESIDENCY", 2019), _act("LED-en", "EXAMPLE-WORKSHOP", 2022)],
        [_mem("LED-ko", "EXAMPLE-RESIDENCY"), _mem("LED-en", "EXAMPLE-WORKSHOP")],
    )
    resolve_ledger(ledger)
    item = ledger.read("review_queue")[0]
    decide_queue(ledger, item["queue_id"], "distinct")
    assert "decided=different" in ledger.read("review_queue")[0]["detail"]
    assert "evidence_at_decision=none" in ledger.read("review_queue")[0]["detail"]
    first = resolve_ledger(ledger)
    second = resolve_ledger(ledger)
    assert not first.queued and not second.queued
    assert not first.merges and not second.merges
    rows = ledger.read("review_queue")
    assert len(rows) == 1
    assert rows[0]["queue_id"] == item["queue_id"]
    assert rows[0]["status"] == "done"
    assert "reopened=" not in rows[0]["detail"]


def test_a_decision_covers_the_row_that_absorbed_one_side(tmp_path: Path):
    """A decision on A–B covers D after D absorbs B. Resolve does not open A–D."""
    from giye.resolve.decide import decide_queue

    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-ko", "GY-000001", "서지우"), _artist("LED-en", "GY-000002", "Jiwoo Seo", "Jiwoo Seo")],
        [_act("LED-ko", "EXAMPLE-RESIDENCY", 2019), _act("LED-en", "EXAMPLE-WORKSHOP", 2022)],
        [_mem("LED-ko", "EXAMPLE-RESIDENCY"), _mem("LED-en", "EXAMPLE-WORKSHOP")],
    )
    resolve_ledger(ledger)
    item = ledger.read("review_queue")[0]
    decide_queue(ledger, item["queue_id"], "distinct")
    artists = ledger.read("artists")
    artists.append(_artist("LED-d", "GY-000003", "Ada Example", ""))
    ledger.write("artists", artists, task="test")
    ledger._merge_rows("LED-d", "LED-en", evidence="the English row was absorbed", rule="H")
    assert "merged LED-en" in next(row["reviewer_note"] for row in ledger.read("artists") if row["ledger_id"] == "LED-d")
    first = resolve_ledger(ledger)
    second = resolve_ledger(ledger)
    assert not first.queued and not second.queued
    rows = ledger.read("review_queue")
    assert [row["queue_id"] for row in rows] == [item["queue_id"]]
    assert rows[0]["status"] == "done"


def test_new_evidence_reopens_a_decided_pair_without_a_second_item(tmp_path: Path):
    from giye.resolve.decide import decide_queue

    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-ko", "GY-000001", "서지우"), _artist("LED-en", "GY-000002", "Jiwoo Seo", "Jiwoo Seo")],
        [_act("LED-ko", "EXAMPLE-RESIDENCY", 2019), _act("LED-en", "EXAMPLE-WORKSHOP", 2022)],
        [_mem("LED-ko", "EXAMPLE-RESIDENCY"), _mem("LED-en", "EXAMPLE-WORKSHOP")],
    )
    resolve_ledger(ledger)
    item = ledger.read("review_queue")[0]
    decide_queue(ledger, item["queue_id"], "distinct")
    ledger.write(
        "links",
        [_link("LED-ko", "https://jiwoo.example.org"), _link("LED-en", "https://jiwoo.example.org")],
        task="test",
    )
    result = resolve_ledger(ledger)
    assert not result.merges
    rows = ledger.read("review_queue")
    assert len(rows) == 1
    assert rows[0]["queue_id"] == item["queue_id"]
    assert rows[0]["status"] == "open"
    assert "reopened=new_evidence (E1)" in rows[0]["detail"]
    assert "decided=different" in rows[0]["detail"]
    again = resolve_ledger(ledger)
    assert not again.merges
    assert len(ledger.read("review_queue")) == 1
    assert ledger.read("review_queue")[0]["detail"].count("reopened=new_evidence") == 1


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


def test_expand_teams_gives_a_name_only_member_a_new_record_and_queues_the_pair(tmp_path: Path):
    """A common name on another programme is not joined and not dropped: new record, queued pair."""
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
    created = expand_teams(ledger)
    assert len(created) == 1
    assert len(ledger.read("artists")) == 3
    membership = ledger.read("frame_membership")
    assert not any(row["ledger_id"] == "LED-other" and row["frame_code"] == "EXAMPLE-RESIDENCY" for row in membership)
    assert any(row["ledger_id"] == created[0] and row["frame_code"] == "EXAMPLE-RESIDENCY" for row in membership)
    # The expanded membership names its rule and the team it came from.
    assert {row["attach_rule"] for row in membership if row["ledger_id"] == created[0]} == {"team:LED-team"}
    queue = [row for row in ledger.read("review_queue") if row["reason"] == "possible_same_person"]
    assert [row["ledger_id"] for row in queue] == created
    assert "LED-other" in queue[0]["detail"] and "team member, name only" in queue[0]["detail"]
    # A re-run attaches the member to the record it already has (A1) and queues nothing new.
    assert expand_teams(ledger) == []
    assert len(ledger.read("artists")) == 3
    assert len(ledger.read("review_queue")) == len(queue)


def test_expand_teams_keeps_a_latin_only_member(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [
            _artist("LED-team", "GY-000001", "Noeul Studio", "Noeul Studio", note="members=Mira Example|Juno Sample"),
            _artist("LED-other", "GY-000002", "Mira Example", "Mira Example"),
        ],
        [_act("LED-team", "EXAMPLE-RESIDENCY", 2019), _act("LED-other", "EXAMPLE-WORKSHOP", 2020)],
        [_mem("LED-team", "EXAMPLE-RESIDENCY"), _mem("LED-other", "EXAMPLE-WORKSHOP")],
    )
    created = expand_teams(ledger)
    assert len(created) == 2
    names = sorted(row["name_en"] or row["name_ko"] for row in ledger.read("artists") if row["ledger_id"] in created)
    assert names == ["Juno Sample", "Mira Example"]
    queue = [row for row in ledger.read("review_queue") if row["reason"] == "possible_same_person"]
    assert len(queue) == 1 and "LED-other" in queue[0]["detail"]


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
    # Haneul Kim on the workshop is a different spelling of 김하늘. The English
    # CV names the residency, so X1+E2 keeps the residency row. Kim Haneul on
    # the forum is a Latin personal name in another programme: A2 does not join
    # it, and the pair stays on the review queue.
    kept = rows_named("김하늘")
    assert len(kept) == 1
    assert kept[0]["name_en"] == "Haneul Kim"
    assert "rule=X1+E2" in (kept[0].get("reviewer_note") or "")
    assert len(rows_named("Kim Haneul")) == 1
    forum = [
        row for row in ledger.read("frame_membership") if row["frame_code"] == "EXAMPLE-FORUM-2023"
    ]
    assert forum and forum[0]["attach_rule"] == "first"
    latin = [
        row
        for row in ledger.read("review_queue")
        if row["status"] == "open" and "latin name only" in (row.get("detail") or "")
    ]
    assert len(latin) == 1
    assert kept[0]["ledger_id"] in latin[0]["detail"]
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


def test_a_mixed_script_name_with_one_hangul_syllable_has_a_shared_key(tmp_path: Path):
    """A Latin word plus one Hangul syllable keys on its compact spelling, with or without an English name."""
    from giye.resolve.attach import attach_row, name_keys

    assert name_keys("Wave몸", "", "") & name_keys("Wave몸", "Wave Mom", "")
    cfg = load(_config(tmp_path))
    existing = _artist("LED-group", "GY-000001", "Wave몸", "Wave Mom", aliases="김바다|박바다")
    decision = attach_row(
        artists=[existing],
        families_by_lid={"LED-group": {"EXAMPLE-WORKSHOP"}},
        links=[],
        frame_code="EXAMPLE-RESIDENCY",
        name_ko="Wave몸",
        name_en="",
        aliases="김바다|박바다",
        identity="",
        websites=[],
        field=cfg.field_config,
    )
    assert decision.ledger_id == "LED-group"


def test_t1_blocks_a_team_with_a_personal_shaped_name_and_its_member(tmp_path: Path):
    """E1 must not join a team to a member whose note names it, even when the team name looks personal."""
    ledger = _ledger(tmp_path)
    team = _artist("LED-team", "GY-000001", "Mira Sample", "Mira Sample", aliases="김바다|Mira Duo")
    member = _artist("LED-member", "GY-000002", "김바다", "Bada Kim", note="example_2021; team=Mira Sample")
    _seed(
        ledger,
        [team, member],
        [_act("LED-team", "EXAMPLE-RESIDENCY", 2019), _act("LED-member", "EXAMPLE-WORKSHOP", 2021)],
        [_mem("LED-team", "EXAMPLE-RESIDENCY"), _mem("LED-member", "EXAMPLE-WORKSHOP")],
        [_link("LED-team", "https://mira.example.org"), _link("LED-member", "https://mira.example.org")],
    )
    assert team_like(team) == "" and team_like(member) == ""
    assert team_person_mismatch(team, member)
    result = resolve_ledger(ledger)
    assert not result.merges
    assert ("LED-member", "LED-team") in result.blocked_team
    assert {row["ledger_id"] for row in ledger.read("artists")} == {"LED-team", "LED-member"}


def test_t1_member_notes_and_members_lists_name_the_team():
    from giye.resolve.teams import member_of_team

    team = {"ledger_id": "LED-team", "name_ko": "Mira Sample", "name_en": "", "reviewer_note": ""}
    by_note = {"name_ko": "김바다", "reviewer_note": "팀 구성원: Mira Sample (LED-team)"}
    by_id = {"name_ko": "김바다", "reviewer_note": "팀 구성원: Renamed (LED-team)"}
    listed = {"name_ko": "박바다", "name_en": "", "reviewer_note": ""}
    team_with_list = {**team, "reviewer_note": "members=김바다|박바다"}
    stranger = {"name_ko": "최바다", "reviewer_note": "team=Other Group"}
    assert member_of_team(by_note, team) and member_of_team(by_id, team)
    assert member_of_team(listed, team_with_list)
    assert not member_of_team(stranger, team) and not member_of_team(team, by_note)


def test_expand_teams_with_frames_touches_only_those_editions(tmp_path: Path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [
            _artist("LED-a", "GY-000001", "노을 스튜디오", note="members=김바다"),
            _artist("LED-b", "GY-000002", "새벽 랩", note="members=박바다"),
            _artist("LED-p", "GY-000003", "박바다", note="curated"),
        ],
        [_act("LED-a", "EXAMPLE-RESIDENCY-2019", 2019), _act("LED-b", "EXAMPLE-WORKSHOP-2020", 2020)],
        [_mem("LED-a", "EXAMPLE-RESIDENCY-2019"), _mem("LED-b", "EXAMPLE-WORKSHOP-2020"),
         _mem("LED-p", "EXAMPLE-WORKSHOP-2020")],
    )
    other = {row["ledger_id"]: row for row in ledger.read("artists") if row["ledger_id"] != "LED-a"}
    created = expand_teams(ledger, frames={"EXAMPLE-RESIDENCY-2019"})
    assert len(created) == 1
    after = {row["ledger_id"]: row for row in ledger.read("artists")}
    assert after[created[0]]["name_ko"] == "김바다"
    assert {lid: after[lid] for lid in other} == other
    assert {row["ledger_id"] for row in ledger.read("activities")} == {"LED-a", "LED-b", created[0]}
    # Without a filter the other frame's team is expanded too (giye resolve): the existing
    # member gets the team credit row but no note; only a record created here carries one.
    expand_teams(ledger)
    after = {row["ledger_id"]: row for row in ledger.read("artists")}
    assert after["LED-p"]["reviewer_note"] == "curated"
    assert any(row["ledger_id"] == "LED-p" and row["role"] == "팀: 새벽 랩" for row in ledger.read("activities"))
    assert after[created[0]]["reviewer_note"] == "팀 구성원: 노을 스튜디오 (LED-a)"


def test_x1_queues_a_korean_row_whose_own_latin_name_meets_the_other(tmp_path: Path):
    """Review round 6, MAJOR-2: 이도윤 / Do-yun Lee and a Latin-only Doyun Lee were neither joined nor queued."""
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-ko", "GY-000001", "이도윤", "Do-yun Lee"), _artist("LED-en", "GY-000002", "Doyun Lee", "Doyun Lee")],
        [_act("LED-ko", "EXAMPLE-RESIDENCY", 2019), _act("LED-en", "EXAMPLE-WORKSHOP", 2022)],
        [_mem("LED-ko", "EXAMPLE-RESIDENCY"), _mem("LED-en", "EXAMPLE-WORKSHOP")],
    )
    assert x1_candidates(ledger.read("artists")) == [("LED-ko", "LED-en")]
    result = resolve_ledger(ledger)
    # The spelling is never enough: no merge, one queue item marked for counting.
    assert not result.merges
    queued = ledger.read("review_queue")
    assert len(queued) == 1
    assert "rule=x1_own_en" in queued[0]["detail"]
    assert {"LED-ko", "LED-en"} <= {queued[0]["ledger_id"], *re.findall(r"LED-[a-z]+", queued[0]["detail"])}
    assert len(ledger.read("artists")) == 2


def _two_cv_people(tmp_path: Path, *, both_files: bool) -> Ledger:
    from giye.ledger.schemas import CV_SOURCES_FIELDS

    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-a", "GY-000001", "한별"), _artist("LED-b", "GY-000002", "윤가온")],
        [_act("LED-a", "EXAMPLE-WORKSHOP-2021", 2021), _act("LED-b", "EXAMPLE-RESIDENCY-2019", 2019)],
        [_mem("LED-a", "EXAMPLE-WORKSHOP-2021"), _mem("LED-b", "EXAMPLE-RESIDENCY-2019")],
    )
    ledger.write(
        "cv_sources",
        [
            empty_row(CV_SOURCES_FIELDS, source_id="CV-A", ledger_id="LED-a", url="https://a.example.org/cv"),
            empty_row(CV_SOURCES_FIELDS, source_id="CV-B", ledger_id="LED-b", url="https://b.example.org/cv"),
        ],
        task="test",
    )
    if both_files:
        _cv(ledger, "LED-a", [{"title": "Line of A", "venue": "", "year": 2020, "source_id": "CV-A"}])
    _cv(ledger, "LED-b", [{"title": "Line of B", "venue": "", "year": 2018, "source_id": "CV-B"}])
    return ledger


def test_resolver_reads_the_absorbed_cv_after_a_merge(tmp_path: Path):
    """Review round 6, MAJOR-3a: the absorbed record's extraction was ignored when the survivor had one."""
    from giye.resolve.cv import load_cv_activities
    from giye.resolve.decide import merge_people

    ledger = _two_cv_people(tmp_path, both_files=True)
    merge_people(ledger, "LED-a", "LED-b", evidence="H same studio and same works, checked by the author 2026-01-15")
    titles = sorted(line["title"] for line in load_cv_activities(ledger, ledger.config).get("LED-a", []))
    assert titles == ["Line of A", "Line of B"]


def test_restoring_the_backups_gives_each_cv_back_to_its_owner(tmp_path: Path):
    """Review round 6, MAJOR-3b: after the documented restore the survivor kept the other person's CV."""
    from giye.resolve.cv import load_cv_activities
    from giye.resolve.decide import merge_people

    ledger = _two_cv_people(tmp_path, both_files=False)
    before = {table: ledger.read(table) for table in ("artists", "activities", "frame_membership", "cv_sources")}
    merge_people(ledger, "LED-a", "LED-b", evidence="H same studio and same works, checked by the author 2026-01-15")
    assert [line["title"] for line in load_cv_activities(ledger, ledger.config)["LED-a"]] == ["Line of B"]
    # The restore: every table the merge wrote goes back to its earlier bytes.
    for table, rows in before.items():
        ledger.write(table, rows, task="restore")
    ledger.write("gy_retired", [], task="restore")
    cvs = load_cv_activities(ledger, ledger.config)
    assert "LED-a" not in cvs
    assert [line["title"] for line in cvs["LED-b"]] == ["Line of B"]


def test_e3_cv_side_matches_whole_words_not_substrings():
    """Software review round 6, MAJOR-4: 〈Sea〉 was found in "Research" and 〈Light〉 in "Lighthouse"."""
    from giye.resolve.evidence import cv_lists_work

    assert cv_lists_work([{"title": "Research Residency Showcase", "year": 2020}], {("sea", 2020)}) is None
    assert cv_lists_work([{"title": "Lighthouse Festival", "year": 2020}], {("light", 2020)}) is None
    assert cv_lists_work([{"title": "〈Sea〉 open studio", "year": 2020}], {("sea", 2020)})
    assert cv_lists_work([{"title": "Light, Example Hall", "year": 2021}], {("light", 2020)})
    # Hangul: not inside a longer compound, but a suffix may follow.
    assert cv_lists_work([{"title": "푸른바다 개인전", "year": 2020}], {("바다", 2020)}) is None
    assert cv_lists_work([{"title": "바다전", "year": 2020}], {("바다", 2020)})
    # Spacing inside a title is not stable, so a key without spaces still meets a spaced title.
    assert cv_lists_work([{"title": "푸른 신호 전시", "year": 2020}], {("푸른신호", 2020)})


def test_html_cv_shared_by_two_records_is_reported_not_silently_skipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cvs = tmp_path / "cvs"
    cvs.mkdir()
    (cvs / "doyun.html").write_text(
        '<article data-name-en="Doyun Lee"><ul><li data-year="2019">Example Residency</li></ul></article>',
        encoding="utf-8",
    )
    ledger = _ledger(tmp_path, cv_dir=cvs)
    _seed(
        ledger,
        [_artist("LED-a", "GY-000001", "이도윤", "Doyun Lee"), _artist("LED-b", "GY-000002", "Doyun Lee", "Doyun Lee")],
        [_act("LED-a", "EXAMPLE-RESIDENCY", 2019), _act("LED-b", "EXAMPLE-WORKSHOP", 2021)],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-WORKSHOP")],
    )
    from giye.resolve.cv import load_cv_activities

    found = load_cv_activities(ledger, ledger.config)
    assert found == {}
    out = capsys.readouterr().out
    assert "doyun.html" in out and "2 records" in out
