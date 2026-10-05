# SPDX-License-Identifier: AGPL-3.0-only
"""Derived values P1–P6. People are fictitious. CV text is a local file, not a fetch."""

from __future__ import annotations

from pathlib import Path

from giye.cli import main
from giye.config import load
from giye.ledger.io import read_csv, write_csv
from giye.ledger.schemas import (
    ACTIVITIES_FIELDS,
    ARTISTS_FIELDS,
    CV_SOURCES_FIELDS,
    LINKS_FIELDS,
    MEMBERSHIP_FIELDS,
    empty_row,
)
from giye.normalize.rules import lang_of, match_key, norm_text, year_flags
from giye.normalize.service import normalize

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, text: str | None = None) -> Path:
    path = tmp_path / "giye.toml"
    path.write_text(
        text
        or f"""
[archive]
name = "Synthetic media-art field"
id_prefix = "GY"
territory = "KR"
languages = ["ko", "en"]

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "frames.yml"

[resolve.event_patterns]
EXAMPLE-RESIDENCY = "예시 ?레지던시|example residency"
""",
        encoding="utf-8",
    )
    return path


def _write_ledger(tmp_path: Path, artists: list[dict], activities: list[dict], **tables: list[dict]) -> None:
    ledger = tmp_path / "data" / "ledger"
    write_csv(path=ledger / "artists.csv", fields=ARTISTS_FIELDS, rows=[empty_row(ARTISTS_FIELDS, **row) for row in artists])
    write_csv(
        path=ledger / "activities.csv",
        fields=ACTIVITIES_FIELDS,
        rows=[empty_row(ACTIVITIES_FIELDS, **row) for row in activities],
    )
    if "cv_sources" in tables:
        write_csv(
            path=ledger / "cv_sources.csv",
            fields=CV_SOURCES_FIELDS,
            rows=[empty_row(CV_SOURCES_FIELDS, **row) for row in tables["cv_sources"]],
        )
    if "membership" in tables:
        write_csv(
            path=ledger / "frame_membership.csv",
            fields=MEMBERSHIP_FIELDS,
            rows=[empty_row(MEMBERSHIP_FIELDS, **row) for row in tables["membership"]],
        )


def _attr(rows: list[dict], ledger_id: str, field: str) -> dict | None:
    found = [row for row in rows if row["ledger_id"] == ledger_id and row["field"] == field]
    return found[0] if found else None


def test_p1_year_flags_do_not_delete() -> None:
    rows = [
        {"activity_id": "y0", "year": "", "title": "Untitled"},
        {"activity_id": "y1", "year": "1800", "title": "Old"},
        {"activity_id": "y2", "year": "1945", "title": "Art After 1945"},
        {"activity_id": "ok", "year": "2008", "title": "A range is not a period"},
    ]
    flags = year_flags(rows)
    assert flags["y0"] == ["year_missing"]  # Y0
    assert flags["y1"] == ["year_range"]  # Y1
    assert flags["y2"] == ["year_from_title"]  # Y2
    assert "ok" not in flags


def test_p2_normalises_text_and_script() -> None:
    assert norm_text("  서울\u00a0  시립  ") == "서울 시립"
    assert match_key("Art, Centre!") == "artcentre"
    assert lang_of("서울시립미술관") == "ko"
    assert lang_of("Seoul Museum of Art") == "en"
    assert lang_of("서울 Museum") == "mixed"
    assert lang_of("2020") == ""


def test_p5_birth_base_and_ledger_wins(tmp_path: Path) -> None:
    cv = tmp_path / "data" / "raw" / "cv"
    cv.mkdir(parents=True)
    (cv / "haneul.txt").write_text(
        "Haneul Kim (b. 1985). Based in Seoul. Makes video.\n",
        encoding="utf-8",
    )
    (cv / "conflict.txt").write_text("b. 1980 and born in 1990.\n", encoding="utf-8")
    (cv / "team.txt").write_text("b. 1970\n", encoding="utf-8")
    (cv / "kept.txt").write_text("Lives and works in London.\n", encoding="utf-8")
    _write_ledger(
        tmp_path,
        [
            {"ledger_id": "p-haneul", "name_ko": "김하늘", "name_en": "Haneul Kim"},
            {"ledger_id": "p-conflict", "name_ko": "김바다", "name_en": "Bada Kim"},
            {"ledger_id": "p-team", "name_ko": "하늘집단", "reviewer_note": "members=김하늘|김바다"},
            {"ledger_id": "p-kept", "name_en": "Kept Person", "country": "FR", "active_since": "2001", "field": "painting"},
        ],
        [
            {
                "activity_id": "e1",
                "ledger_id": "p-haneul",
                "title": "video study",
                "year": "2014",
                "activity_type": "group_exhibition",
                "publishable": "yes",
                "venue": "Nam June Paik Art Center, Yongin",
            },
            {
                "activity_id": "e2",
                "ledger_id": "p-haneul",
                "title": "another video",
                "year": "2016",
                "activity_type": "screening",
                "publishable": "yes",
                "venue": "Online",
            },
            {
                "activity_id": "e3",
                "ledger_id": "p-haneul",
                "title": "Art After 1945",
                "year": "1945",
                "activity_type": "group_exhibition",
                "publishable": "yes",
            },
            {
                "activity_id": "kept-row",
                "ledger_id": "p-kept",
                "title": "video",
                "year": "1990",
                "activity_type": "group_exhibition",
                "publishable": "yes",
            },
            {
                "activity_id": "kept-row-2",
                "ledger_id": "p-kept",
                "title": "video work",
                "year": "1992",
                "activity_type": "group_exhibition",
                "publishable": "yes",
            },
        ],
        cv_sources=[
            {"source_id": "s-haneul", "ledger_id": "p-haneul", "url": "https://example.org/cv/haneul", "active": "true", "snapshot_path": "data/raw/cv/haneul"},
            {"source_id": "s-conflict", "ledger_id": "p-conflict", "url": "https://example.org/cv/conflict", "active": "true", "snapshot_path": "data/raw/cv/conflict"},
            {"source_id": "s-team", "ledger_id": "p-team", "url": "https://example.org/cv/team", "active": "true", "snapshot_path": "data/raw/cv/team"},
            {"source_id": "s-kept", "ledger_id": "p-kept", "url": "https://example.org/cv/kept", "active": "true", "snapshot_path": "data/raw/cv/kept"},
        ],
    )
    before = (tmp_path / "data" / "ledger" / "artists.csv").read_bytes()
    result = normalize(load(_config(tmp_path)))
    assert (tmp_path / "data" / "ledger" / "artists.csv").read_bytes() == before
    attrs = read_csv(result.processed / "artist_attributes.csv")
    birth = _attr(attrs, "p-haneul", "birth_year")
    assert birth is not None
    assert birth["value"] == "1985"
    assert birth["rule"].startswith("B1")
    assert birth["evidence_url"] == "https://example.org/cv/haneul"
    assert _attr(attrs, "p-conflict", "birth_year") is None
    assert _attr(attrs, "p-team", "birth_year") is None
    country = _attr(attrs, "p-haneul", "country")
    assert country is not None and country["value"] == "KR" and country["rule"].startswith("L1")
    region = _attr(attrs, "p-haneul", "region")
    assert region is not None and region["value"] == "서울"
    since = _attr(attrs, "p-haneul", "active_since")
    assert since is not None and since["value"] == "2014" and since["evidence"] == "e1"
    medium = _attr(attrs, "p-haneul", "medium")
    assert medium is not None and medium["value"] == "영상" and medium["rule"].startswith("M1")
    # Ledger cells win, including medium when field is already set.
    assert _attr(attrs, "p-kept", "country") is None
    assert _attr(attrs, "p-kept", "region") is None
    assert _attr(attrs, "p-kept", "active_since") is None
    assert _attr(attrs, "p-kept", "medium") is None
    activities = read_csv(result.processed / "activities.csv")
    by_id = {row["activity_id"]: row for row in activities}
    assert by_id["e1"]["venue_country"] == "KR"
    assert by_id["e1"]["venue_region"] == "경기"
    assert by_id["e1"]["venue_kind"] == "institution"
    assert "year_from_title" in by_id["e3"]["flags"]
    assert "B1" in result.report and "ledger" in result.report.lower()


def test_p4_links_a_cv_row_to_the_edition(tmp_path: Path) -> None:
    _write_ledger(
        tmp_path,
        [{"ledger_id": "p1", "name_en": "Haneul Kim"}],
        [
            {
                "activity_id": "roster",
                "ledger_id": "p1",
                "title": "edition",
                "year": "2019",
                "origin": "EXAMPLE-RESIDENCY-2019",
                "activity_type": "residency",
                "publishable": "yes",
            },
            {
                "activity_id": "cv-hit",
                "ledger_id": "p1",
                "title": "Example residency open studio",
                "year": "2019",
                "origin": "cv:s1",
                "activity_type": "residency",
                "publishable": "yes",
            },
            {
                "activity_id": "cv-miss",
                "ledger_id": "p1",
                "title": "Example residency open studio",
                "year": "2010",
                "origin": "cv:s1",
                "activity_type": "residency",
                "publishable": "yes",
            },
        ],
        membership=[{"ledger_id": "p1", "frame_code": "EXAMPLE-RESIDENCY-2019"}],
    )
    result = normalize(load(_config(tmp_path)))
    rows = {row["activity_id"]: row for row in read_csv(result.processed / "activities.csv")}
    assert rows["roster"]["event_link"] == "EXAMPLE-RESIDENCY-2019"
    assert rows["cv-hit"]["event_link"] == "EXAMPLE-RESIDENCY-2019"
    assert rows["cv-miss"]["event_link"] == ""


def test_cli_normalize_writes_processed(tmp_path: Path) -> None:
    _write_ledger(
        tmp_path,
        [{"ledger_id": "p1", "name_ko": "김하늘"}],
        [{"activity_id": "a1", "ledger_id": "p1", "title": "Piece", "venue": "Seoul", "year": "2020", "publishable": "yes", "activity_type": "screening"}],
    )
    code = main(["normalize", "--config", str(_config(tmp_path))])
    assert code == 0
    processed = tmp_path / "data" / "processed"
    assert (processed / "activities.csv").is_file()
    assert (processed / "artist_attributes.csv").is_file()
    assert (processed / "venues.csv").is_file()
    assert (processed / "venue_audit.md").is_file()
    assert (processed / "manifest.json").is_file()
    assert "P1" in (processed / "report.md").read_text(encoding="utf-8")
    assert "P6 record depth" in (processed / "report.md").read_text(encoding="utf-8")


def test_p6_assigns_the_highest_level_and_names_the_evidence(tmp_path: Path) -> None:
    url = "https://example.org/roster"
    _write_ledger(
        tmp_path,
        [
            {"ledger_id": "p-roster", "name_ko": "김하늘", "source_url": url, "status": "STAGED"},
            {"ledger_id": "p-medium", "name_ko": "김바다", "source_url": url, "status": "STAGED"},
            {"ledger_id": "p-site", "name_ko": "박서연", "source_url": url, "status": "STAGED"},
            {"ledger_id": "p-cv", "name_ko": "이하루", "source_url": url, "status": "STAGED"},
            {"ledger_id": "p-out", "name_ko": "정다운", "source_url": url, "status": "STAGED"},
        ],
        [
            {"activity_id": "m1", "ledger_id": "p-medium", "title": "video study", "year": "2019", "publishable": "yes", "activity_type": "group_exhibition"},
            {"activity_id": "m2", "ledger_id": "p-medium", "title": "video study two", "year": "2020", "publishable": "yes", "activity_type": "group_exhibition"},
            {"activity_id": "cv1", "ledger_id": "p-cv", "title": "from the cv", "year": "2018", "origin": "cv:src-1", "publishable": "yes", "activity_type": "group_exhibition"},
            {
                "activity_id": "old",
                "ledger_id": "p-cv",
                "title": "replaced",
                "year": "2017",
                "origin": "cv:src-0",
                "reviewer_note": "superseded_by_cv",
                "publishable": "yes",
                "activity_type": "group_exhibition",
            },
        ],
        membership=[
            {"ledger_id": lid, "frame_code": "EXAMPLE-RESIDENCY", "source_url": url}
            for lid in ("p-roster", "p-medium", "p-site", "p-cv", "p-out")
        ],
    )
    write_csv(
        path=tmp_path / "data" / "ledger" / "links.csv",
        fields=LINKS_FIELDS,
        rows=[empty_row(LINKS_FIELDS, link_id="lnk-1", ledger_id="p-site", url="https://haneul.example.org", link_type="website")],
    )
    write_csv(
        path=tmp_path / "data" / "ledger" / "scope.csv",
        fields=["ledger_id", "scope"],
        rows=[{"ledger_id": "p-out", "scope": "out"}],
    )
    snippets = tmp_path / "data" / "work" / "tendency"
    snippets.mkdir(parents=True)
    (snippets / "snippets.jsonl").write_text(
        '{"ledger_id": "p-roster", "class": "has_title_only"}\n{"ledger_id": "p-medium", "class": "has_description"}\n',
        encoding="utf-8",
    )
    result = normalize(load(_config(tmp_path)))
    attrs = read_csv(result.processed / "artist_attributes.csv")
    depth = {row["ledger_id"]: row for row in attrs if row["field"] == "record_depth"}
    assert set(depth) == {"p-roster", "p-medium", "p-site", "p-cv"}
    assert depth["p-roster"]["value"] == "1"
    assert depth["p-roster"]["rule"] == "P6 record depth"
    assert "frame_membership.csv" in depth["p-roster"]["evidence"]
    assert depth["p-medium"]["value"] == "2"
    assert "has_description" in depth["p-medium"]["evidence"]
    assert depth["p-site"]["value"] == "3"
    assert "lnk-1" in depth["p-site"]["evidence"]
    assert depth["p-cv"]["value"] == "4"
    assert "src-1" in depth["p-cv"]["evidence"]
    assert "src-0" not in depth["p-cv"]["evidence"]
    report = (result.processed / "report.md").read_text(encoding="utf-8")
    assert "| 1 | 1 |" in report and "| 4 | 1 |" in report
