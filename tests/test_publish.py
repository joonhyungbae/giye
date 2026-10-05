# SPDX-License-Identifier: AGPL-3.0-only
"""Site snapshot: who is published, tombstones, redirects, coverage, citations.

People and URLs are fictitious. The sentences match production build_site_dataset.py
and the production cite dialog.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from giye.config import load
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import ACTIVITIES_FIELDS, ARTISTS_FIELDS, LINKS_FIELDS, MEMBERSHIP_FIELDS, empty_row
from giye.publish.cite import citation_texts
from giye.publish.html import render
from giye.publish.snapshot import guess_medium, parse_year, publish, region_tags, resolve_frame_edition

NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]

FRAMES = """
version: 1
frames:
  - code: EXAMPLE-RESIDENCY
    name_en: Example Residency
    name_ko: 예시 레지던시
    source_url: https://example.org/residency/alumni
    roster_count: 4
    years_covered: "2019"
    status: active
    eligibility:
      decision: included
      f1_purpose: The page states the field.
      f2_cohort: A jury selects the cohort.
      f3_territory: Held in the configured territory.
      f4_roster: The alumni page is the roster.
      f5_period: Editions since 2019.
"""


def _config(tmp_path: Path, frames: str = FRAMES) -> Path:
    (tmp_path / "frames.yml").write_text(frames, encoding="utf-8")
    path = tmp_path / "giye.toml"
    path.write_text(
        """
[archive]
name = "Synthetic field"
id_prefix = "GY"

[paths]
data = "data"
frames = "frames.yml"

[publish]
site_url = "https://example.org"
dataset_version = "0.2"
dataset_title = "Synthetic field"
citation_author = "Example Archive"
""",
        encoding="utf-8",
    )
    return path


def _artist(ledger_id: str, name: str, **extra: str) -> dict[str, str]:
    row = empty_row(
        ARTISTS_FIELDS,
        ledger_id=ledger_id,
        name_ko=name,
        status="STAGED",
        source_type="PUBLIC_RECORD",
        source_url="https://example.org/residency/alumni",
        collected_at="2026-01-15",
        cv_link_ok="no",
        frame_status="IN_FRAME",
        verification="UNVERIFIED",
    )
    row.update(extra)
    return row


def _activity(ledger_id: str, **extra: str) -> dict[str, str]:
    row = empty_row(
        ACTIVITIES_FIELDS,
        activity_id=extra.pop("activity_id", "act-1"),
        ledger_id=ledger_id,
        title=extra.pop("title", "EXAMPLE-RESIDENCY"),
        year=extra.pop("year", "2019"),
        activity_type="other",
        source_url="https://example.org/residency/alumni",
        source_type="PUBLIC_RECORD",
        collected_at="2026-01-15",
        publishable="yes",
        origin="EXAMPLE-RESIDENCY",
    )
    row.update(extra)
    return row


def _publish(tmp_path: Path, artists, activities=None, membership=None, *, frames: str = FRAMES, **tables) -> dict:
    cfg = load(_config(tmp_path, frames=frames))
    ledger = Ledger.open(cfg)
    ledger.write("artists", artists, task="test")
    ledger.write("activities", activities or [], task="test")
    ledger.write("frame_membership", membership or [], task="test")
    for name, rows in tables.items():
        ledger.write(name, rows, task="test")
    publish(cfg, now=NOW)
    site = cfg.site
    return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in sorted(site.glob("*.json"))}


def test_citation_sentences_match_the_production_dialog():
    text = citation_texts(
        author="기예 Giye",
        title="김하늘",
        record_id="GY-000001",
        version="0.2",
        url="https://giye.org/artist/GY-000001",
        year=2026,
        accessed="2026-01-15",
    )
    assert text["apa"] == (
        "기예 Giye. (2026). 김하늘 [Artist record GY-000001, Dataset v0.2]. "
        "Retrieved 2026-01-15, from https://giye.org/artist/GY-000001"
    )
    assert text["chicago"] == (
        '기예 Giye. "김하늘." [Artist record GY-000001, Dataset v0.2] 2026. '
        "Accessed 2026-01-15. https://giye.org/artist/GY-000001."
    )
    assert text["bibtex"] == (
        "@misc{giye_GY_000001,\n"
        "  author       = {{기예 Giye}},\n"
        "  title        = {김하늘},\n"
        "  note         = {Artist record GY-000001, Dataset v0.2},\n"
        "  year         = {2026},\n"
        "  howpublished = {\\url{https://giye.org/artist/GY-000001}},\n"
        "  urldate      = {2026-01-15}\n"
        "}"
    )
    dataset = citation_texts(
        author="기예 Giye",
        title="Index",
        record_id=None,
        version="0.2",
        url="https://giye.org/data",
        year=2026,
        accessed="2026-01-15",
    )
    assert dataset["apa"].startswith("기예 Giye. (2026). Index [Dataset v0.2].")
    assert dataset["bibtex"].startswith("@misc{giye_dataset,")


def test_region_and_medium_keep_the_production_lists():
    # Incheon is tagged 경기 because that is what the production script wrote.
    assert region_tags("인천", "") == ["경기"]
    assert region_tags("대한민국", "") == ["기타"]
    assert region_tags("France", "") == ["기타"]
    assert guess_medium("video", "") == ["영상"]
    assert parse_year("about 2019") == 2019
    assert parse_year("") is None


def test_edition_alias_is_declared_in_the_field():
    from giye.field import EditionAlias, Field, load_field, shipped_field_path

    field = Field(
        edition_aliases=(
            EditionAlias("SYNFLD-2025", "SYNFLD-CURRENT", "2025", "the live year is filed under the current frame"),
        )
    )
    registry = ["SYNFLD-CURRENT", "NORTH"]
    years = {"SYNFLD-CURRENT": "2024-2025", "NORTH": ""}
    assert resolve_frame_edition("SYNFLD-2025", registry, years, field=field) == ("SYNFLD-CURRENT", "2025")
    assert resolve_frame_edition("NORTH-2022", registry, years, field=field) == ("NORTH", "2022")
    assert resolve_frame_edition("EXAMPLE-RESIDENCY", ["EXAMPLE-RESIDENCY"], {"EXAMPLE-RESIDENCY": "2019"}) == (
        "EXAMPLE-RESIDENCY",
        "2019",
    )
    shipped = load_field(shipped_field_path())
    assert shipped.edition_aliases
    for alias in shipped.edition_aliases:
        assert alias.reason
        assert resolve_frame_edition(alias.membership, [alias.frame], {}, field=shipped) == (
            alias.frame,
            alias.edition,
        )


def test_published_page_keeps_source_year_and_coverage(tmp_path: Path):
    artists = [
        _artist("LED-haneul", "김하늘", gy_id="GY-000001", name_en="Haneul Kim", field="video"),
        _artist("LED-park", "박서연", gy_id="GY-000002"),
    ]
    activities = [
        _activity("LED-haneul", activity_id="act-h", title="〈푸른 신호〉", year="2019", role="작가"),
        _activity("LED-park", activity_id="act-p", title="EXAMPLE-RESIDENCY", year="2019"),
        _activity("LED-haneul", activity_id="act-noyear", title="No year", year=""),
    ]
    membership = [
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id=lid,
            frame_code="EXAMPLE-RESIDENCY",
            source_url="https://example.org/residency/alumni",
            collected_at="2026-01-15",
        )
        for lid in ("LED-haneul", "LED-park")
    ]
    site = _publish(tmp_path, artists, activities, membership)
    people = {row["id"]: row for row in site["artists.json"]}
    assert list(people) == ["GY-000001", "GY-000002"]
    haneul = people["GY-000001"]
    assert haneul["name_ko"] == "김하늘"
    assert haneul["source_url"] == "https://example.org/residency/alumni"
    assert haneul["collected_at"] == "2026-01-15"
    assert haneul["medium_tags"] == ["영상"]
    assert haneul["frame_editions"] == [{"frame": "EXAMPLE-RESIDENCY", "edition": "2019", "role": "작가"}]
    assert haneul["type"] == "individual"
    titles = [row["title"] for row in site["activities.json"] if row["artist_id"] == "GY-000001"]
    assert titles == ["〈푸른 신호〉"]
    assert site["activities.json"][0]["source_url"].startswith("https://")
    frame = site["frames.json"][0]
    assert frame["included_count"] == 2
    assert frame["roster_count"] == 4
    assert frame["coverage_pct"] == 50.0
    assert frame["eligibility"]["decision"] == "included"
    assert site["coverage.json"]["frames"][0]["coverage_pct"] == 50.0
    assert site["dataset_versions.json"][0]["version"] == "0.2"
    assert site["citations.json"]["dataset"]["url"] == "https://example.org/data"
    assert "Example Archive" in site["citations.json"]["artists"][0]["apa"]
    assert "GY-000001" in site["citations.json"]["artists"][0]["bibtex"]
    # The registry file is not rewritten. Counts live in the snapshot.
    assert "included_count" not in (tmp_path / "frames.yml").read_text(encoding="utf-8")


def test_hidden_record_is_a_tombstone_and_retired_id_redirects(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    artists = [
        _artist("LED-live", "김하늘", gy_id="GY-000001"),
        _artist("LED-hidden", "비공개", gy_id="GY-000002", status="HIDDEN_BY_REQUEST"),
        _artist("LED-gap", "빈번호", gy_id="GY-000006", status="STAGED", source_url=""),
    ]
    membership = [
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id="LED-live",
            frame_code="EXAMPLE-RESIDENCY",
            source_url="https://example.org/residency/alumni",
            collected_at="2026-01-15",
        )
    ]
    retired = [{"gy_id": "GY-000003", "merged_into_ledger_id": "LED-live", "retired_at": "2026-01-15"}]
    site = _publish(tmp_path, artists, membership=membership, gy_retired=retired)
    assert [row["id"] for row in site["artists.json"]] == ["GY-000001"]
    assert site["artist_stubs.json"] == {"GY-000002": "HIDDEN_BY_REQUEST", "GY-000006": "WITHDRAWN"}
    assert "비공개" not in json.dumps(site["artist_stubs.json"], ensure_ascii=False)
    assert site["gy_redirects.json"] == {"GY-000003": "GY-000001"}
    warning = capsys.readouterr().out
    assert "WARNING:" in warning
    assert "GY-000005" in warning
    pages = render(load(_config(tmp_path)))
    hidden = (tmp_path / "data" / "site" / "html" / "GY-000002.html").read_text(encoding="utf-8")
    assert "HIDDEN_BY_REQUEST" in hidden
    assert "비공개" not in hidden
    redirect = (tmp_path / "data" / "site" / "html" / "GY-000003.html").read_text(encoding="utf-8")
    assert 'href="GY-000001.html"' in redirect
    person = (tmp_path / "data" / "site" / "html" / "GY-000001.html").read_text(encoding="utf-8")
    assert "https://example.org/residency/alumni" in person
    assert ">source</a>" in person
    assert pages


def test_background_keeps_education_and_drops_scholarship_titles(tmp_path: Path):
    artists = [_artist("LED-haneul", "김하늘", gy_id="GY-000001")]
    membership = [
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id="LED-haneul",
            frame_code="EXAMPLE-RESIDENCY",
            source_url="https://example.org/residency/alumni",
            collected_at="2026-01-15",
        )
    ]
    activities = [
        _activity("LED-haneul", activity_id="act-roster"),
        _activity(
            "LED-haneul",
            activity_id="act-edu",
            title="서울예시대학교 미술학 학사",
            year="2014",
            publishable="no",
            origin="cv:CV-DEMO",
            source_url="https://cv.example.org/haneul",
            source_type="SELF_SUBMITTED",
            reviewer_note="cv_section=education",
        ),
        _activity(
            "LED-haneul",
            activity_id="act-grant",
            title="예시 장학금",
            year="2021",
            publishable="no",
            origin="cv:CV-DEMO",
            source_url="https://cv.example.org/haneul",
            reviewer_note="cv_section=education",
        ),
    ]
    site = _publish(tmp_path, artists, activities, membership)
    sections = [row["title"] for row in site["background.json"]]
    assert sections == ["서울예시대학교 미술학 학사"]
    assert all(row["title"] != "예시 장학금" for row in site["activities.json"])


def test_year_flag_and_duplicate_link(tmp_path: Path):
    artists = [_artist("LED-haneul", "김하늘", gy_id="GY-000001")]
    membership = [
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id="LED-haneul",
            frame_code="EXAMPLE-RESIDENCY",
            source_url="https://example.org/residency/alumni",
            collected_at="2026-01-15",
        )
    ]
    activities = [_activity("LED-haneul", activity_id="act-h", title="Art After 1945", year="1945")]
    links = [
        empty_row(LINKS_FIELDS, link_id="L1", ledger_id="LED-haneul", url="https://haneul.example.org", label="site", link_type="website"),
        empty_row(LINKS_FIELDS, link_id="L2", ledger_id="LED-haneul", url="https://haneul.example.org", label="again", link_type="website"),
    ]
    cfg = load(_config(tmp_path))
    processed = cfg.processed
    processed.mkdir(parents=True)
    (processed / "activities.csv").write_text(
        "activity_id,ledger_id,flags\nact-h,LED-haneul,year_from_title|year_range\n",
        encoding="utf-8",
    )
    site = _publish(tmp_path, artists, activities, membership, links=links)
    assert site["activities.json"][0]["flags"] == ["year_from_title"]
    assert len(site["links.json"]) == 1
    assert site["links.json"][0]["url"] == "https://haneul.example.org"


def test_roster_member_without_a_source_is_refused(tmp_path: Path):
    # A membership code that is not in the registry does not inherit the frame URL.
    artists = [_artist("LED-haneul", "김하늘", gy_id="GY-000001", source_url="")]
    membership = [
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id="LED-haneul",
            frame_code="NOT-A-FRAME",
            source_url="",
            collected_at="2026-01-15",
        )
    ]
    with pytest.raises(SystemExit, match="not published"):
        _publish(tmp_path, artists, membership=membership)


def test_rows_without_a_collection_date_are_not_published(tmp_path: Path):
    # No date is filled in at publish time: an activity without collected_at is left out.
    artists = [_artist("LED-haneul", "김하늘", gy_id="GY-000001", cv_link_ok="yes")]
    activities = [
        _activity("LED-haneul", activity_id="act-dated", title="Dated"),
        _activity("LED-haneul", activity_id="act-undated", title="Undated", collected_at=""),
    ]
    site = _publish(tmp_path, artists, activities)
    assert [row["title"] for row in site["activities.json"]] == ["Dated"]


def test_roster_member_without_a_collection_date_is_refused(tmp_path: Path):
    artists = [_artist("LED-haneul", "김하늘", gy_id="GY-000001", collected_at="")]
    membership = [
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id="LED-haneul",
            frame_code="EXAMPLE-RESIDENCY",
            source_url="https://example.org/residency/alumni",
            collected_at="2026-01-15",
        )
    ]
    with pytest.raises(SystemExit, match="not published"):
        _publish(tmp_path, artists, membership=membership)


def test_missing_gy_id_is_issued_in_name_order_and_kept(tmp_path: Path):
    artists = [
        _artist("LED-park", "박서연"),
        _artist("LED-haneul", "김하늘"),
    ]
    membership = [
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id=lid,
            frame_code="EXAMPLE-RESIDENCY",
            source_url="https://example.org/residency/alumni",
            collected_at="2026-01-15",
        )
        for lid in ("LED-park", "LED-haneul")
    ]
    site = _publish(tmp_path, artists, membership=membership)
    by_name = {row["name_ko"]: row["id"] for row in site["artists.json"]}
    assert by_name == {"김하늘": "GY-000001", "박서연": "GY-000002"}
    stored = {row["name_ko"]: row["gy_id"] for row in Ledger.open(load(_config(tmp_path))).read("artists")}
    assert stored == by_name


def test_default_citation_author_is_the_production_string(tmp_path: Path):
    from giye.config import ConfigError
    from giye.publish import publish

    path = tmp_path / "giye.toml"
    path.write_text('[archive]\nname = "Field"\n', encoding="utf-8")
    cfg = load(path)
    assert cfg.citation_author == "기예 Giye"
    assert cfg.site_url == ""
    assert cfg.dataset_version == "0.2"
    with pytest.raises(ConfigError, match=r"\[publish\] site_url is required"):
        publish(cfg)
    demo = load(ROOT / "examples" / "demo" / "giye.toml")
    assert demo.site_url == "https://example.org"
    assert demo.citation_author == "Example Archive"


def _decision_frame(code: str, decision: str) -> str:
    return f"""
  - code: {code}
    name_en: {code}
    source_url: https://example.org/{code.lower()}
    roster_count: 4
    eligibility:
      decision: {decision}
      f1_purpose: States the field.
      f2_cohort: A jury selects a cohort.
      f3_territory: Held in the configured territory.
      f4_roster: A public page lists the participants.
      f5_period: Two editions.
      note: Recorded as {decision}.
"""


def _member(ledger_id: str, frame_code: str) -> dict[str, str]:
    return empty_row(
        MEMBERSHIP_FIELDS,
        ledger_id=ledger_id,
        frame_code=frame_code,
        source_url=f"https://example.org/{frame_code.lower()}",
        collected_at="2026-01-15",
    )


def test_only_included_and_adjacent_memberships_are_published(tmp_path: Path):
    """Excluded, planned, and no_public_roster stay on the coverage files and not on a person."""
    frames = (
        "version: 1\nframes:"
        + _decision_frame("INC", "included")
        + _decision_frame("ADJ", "adjacent")
        + _decision_frame("EXC", "excluded")
        + _decision_frame("PLAN", "planned")
        + _decision_frame("NOROSTER", "no_public_roster")
    )
    artists = [
        _artist("LED-kept", "김하늘"),
        _artist("LED-both", "정다운"),
        _artist("LED-near", "한별"),
        _artist("LED-out", "박서연"),
    ]
    membership = [
        _member("LED-kept", "INC"),
        _member("LED-both", "INC"),
        _member("LED-both", "EXC-2019"),
        _member("LED-near", "ADJ"),
        _member("LED-out", "EXC"),
        _member("LED-out", "PLAN"),
        _member("LED-out", "NOROSTER"),
    ]
    activities = [
        _activity("LED-out", activity_id="act-out", title="제외된 명단", origin="EXC"),
        _activity("LED-kept", activity_id="act-kept", title="채택된 명단", origin="INC"),
    ]
    site = _publish(tmp_path, artists, activities, membership, frames=frames)
    published = {row["name_ko"]: row for row in site["artists.json"]}
    assert list(published) == ["김하늘", "정다운", "한별"]
    assert published["김하늘"]["frame_codes"] == ["INC"]
    assert published["정다운"]["frame_codes"] == ["INC"]
    assert "EXC" not in json.dumps(published["정다운"]["frame_editions"])
    assert published["한별"]["frame_codes"] == ["ADJ"]
    assert published["한별"]["frame_editions"][0]["frame"] == "ADJ"
    dumped = json.dumps(site["artists.json"], ensure_ascii=False)
    assert "박서연" not in dumped
    assert [row["title"] for row in site["activities.json"]] == ["채택된 명단"]
    decisions = {row["code"]: row["eligibility"]["decision"] for row in site["frames.json"]}
    assert decisions == {
        "INC": "included",
        "ADJ": "adjacent",
        "EXC": "excluded",
        "PLAN": "planned",
        "NOROSTER": "no_public_roster",
    }
    by_code = {row["code"]: row for row in site["frames.json"]}
    assert by_code["EXC"]["published_count"] == 0
    assert by_code["EXC"]["included_count"] == 0
    assert by_code["ADJ"]["published_count"] == 1
    assert by_code["ADJ"]["eligibility"]["decision"] == "adjacent"
    coverage_codes = [row["code"] for row in site["coverage.json"]["frames"]]
    assert coverage_codes == ["INC", "ADJ", "EXC", "PLAN", "NOROSTER"]
    assert "박서연" not in json.dumps(site["coverage.json"], ensure_ascii=False)


def test_p6_counts_the_same_people_publish_publishes(tmp_path: Path):
    """One definition of a published person: P6 record depth and the site agree.

    One person's only programme is excluded, another has ``cv_link_ok=yes`` but
    no collection date. Neither is published, and P6 does not count them.
    """
    from giye.ledger.io import read_csv
    from giye.normalize.service import normalize

    frames = "version: 1\nframes:" + _decision_frame("INC", "included") + _decision_frame("EXC", "excluded")
    artists = [
        _artist("LED-kept", "김하늘"),
        _artist("LED-near", "한별", cv_link_ok="yes"),
        _artist("LED-out", "박서연"),
        _artist("LED-undated", "정다운", cv_link_ok="yes", collected_at=""),
    ]
    membership = [_member("LED-kept", "INC"), _member("LED-out", "EXC")]
    site = _publish(tmp_path, artists, [], membership, frames=frames)
    published = {row["external_ids"]["ledger_id"] for row in site["artists.json"]}
    assert published == {"LED-kept", "LED-near"}
    result = normalize(load(tmp_path / "giye.toml"))
    attrs = read_csv(result.processed / "artist_attributes.csv")
    depth = {row["ledger_id"] for row in attrs if row["field"] == "record_depth"}
    assert depth == published


def test_team_records_are_published_as_collective(tmp_path: Path):
    """A row the T1 team test marks is ``collective`` on the site, not ``individual``."""
    artists = [
        _artist("LED-team", "노을 스튜디오", reviewer_note="members=김하늘|박서연"),
        _artist("LED-person", "김하늘"),
    ]
    membership = [_member("LED-team", "EXAMPLE-RESIDENCY"), _member("LED-person", "EXAMPLE-RESIDENCY")]
    site = _publish(tmp_path, artists, [], membership)
    kinds = {row["name_ko"]: row["type"] for row in site["artists.json"]}
    assert kinds == {"노을 스튜디오": "collective", "김하늘": "individual"}
