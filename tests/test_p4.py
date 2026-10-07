# SPDX-License-Identifier: AGPL-3.0-only
"""P4 edition-only patterns and the three shared tightenings. People are fictitious."""

from __future__ import annotations

import re
from pathlib import Path

from giye.config import load
from giye.field import shipped_field
from giye.ledger.io import read_csv, write_csv
from giye.ledger.schemas import ACTIVITIES_FIELDS, ARTISTS_FIELDS, MEMBERSHIP_FIELDS, empty_row
from giye.normalize.rules import event_links
from giye.normalize.service import normalize
from giye.resolve.evidence import cv_mentions, event_pattern, pattern_table

# Fragments discover() then admission_gate admit. One prefix, joined in admission order.
EDITION_ONLY = {
    "DAVINCI": r"Vinchi\s*Creative",
    "AKL": r"슈퍼테스트베드",
    "ARTIENCE": r"아트언스\s*대전",
    "NJP-RANDOMACCESS": r"랜덤\s*엑세스|랜덤\s*엑세스\s*프로젝트",
    "GMAF": r"Gwangju\s*Media\s*Arts\s*Festival",
    "NEMAF": r"서울국제뉴미디어아트페스티벌",
    "ISEA-KOREA": (
        r"International\s*Symposium\s*on\s*Electronic\s*Art"
        r"|International\s*Symposium\s*of\s*Electronic\s*Arts"
        r"|국\s*제\s*전\s*자\s*예\s*술\s*심\s*포\s*지\s*엄"
    ),
    "OPENCIRCUIT": r"오픈서킷\s*부산",
}


def _write(tmp_path: Path, artists: list[dict], activities: list[dict], membership: list[dict]) -> None:
    ledger = tmp_path / "data" / "ledger"
    write_csv(path=ledger / "artists.csv", fields=ARTISTS_FIELDS, rows=[empty_row(ARTISTS_FIELDS, **row) for row in artists])
    write_csv(
        path=ledger / "activities.csv",
        fields=ACTIVITIES_FIELDS,
        rows=[empty_row(ACTIVITIES_FIELDS, **row) for row in activities],
    )
    write_csv(
        path=ledger / "frame_membership.csv",
        fields=MEMBERSHIP_FIELDS,
        rows=[empty_row(MEMBERSHIP_FIELDS, **row) for row in membership],
    )


def _config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Synthetic media-art field"
id_prefix = "GY"
territory = "KR"
languages = ["ko", "en"]

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "frames.yml"
{body}
""",
        encoding="utf-8",
    )
    return path


def test_shipped_edition_only_is_the_admitted_fragments_and_not_an_e2_table() -> None:
    field = shipped_field()
    assert dict(field.edition_only) == EDITION_ONLY
    shared = pattern_table(field.event_patterns)
    edition = pattern_table(field.edition_only)
    assert "OPENCIRCUIT" not in shared
    for rejected in ("ARKO-ARTTECH", "APE", "ACC-CREATORS", "NJP-AWARD"):
        assert rejected not in edition
    text = "International Symposium on Electronic Art"
    assert cv_mentions([{"title": text, "venue": "", "year": 2019}], "ISEA-KOREA-2019", [2019], shared) is None
    assert event_pattern("OPENCIRCUIT-2026", shared) is None
    links, only = event_links(
        [
            {
                "activity_id": "cv-edition",
                "origin": "cv:s1",
                "year": "2019",
                "title": text,
                "venue": "Example Hall",
            }
        ],
        ["ISEA-KOREA-2019"],
        lambda code: event_pattern(code, shared),
        lambda code: event_pattern(code, edition),
    )
    assert links["cv-edition"] == "ISEA-KOREA-2019"
    assert only == {"cv-edition"}
    shared_hit, shared_only = event_links(
        [
            {
                "activity_id": "cv-shared",
                "origin": "cv:s1",
                "year": "2019",
                "title": "ISEA 2019",
                "venue": "",
            }
        ],
        ["ISEA-KOREA-2019"],
        lambda code: event_pattern(code, shared),
        lambda code: event_pattern(code, edition),
    )
    assert shared_hit["cv-shared"] == "ISEA-KOREA-2019"
    assert shared_only == set()


def test_edition_only_links_a_row_for_p4_and_stays_off_e2(tmp_path: Path) -> None:
    _write(
        tmp_path,
        [{"ledger_id": "p1", "name_en": "Haneul Kim"}],
        [
            {
                "activity_id": "roster",
                "ledger_id": "p1",
                "title": "edition",
                "year": "2024",
                "origin": "EXAMPLE-ONLY-2024",
                "activity_type": "residency",
                "publishable": "yes",
            },
            {
                "activity_id": "cv-only",
                "ledger_id": "p1",
                "title": "edition only phrase",
                "year": "2024",
                "origin": "cv:s1",
                "activity_type": "exhibition",
                "publishable": "yes",
            },
            {
                "activity_id": "cv-shared",
                "ledger_id": "p1",
                "title": "example camp open studio",
                "year": "2024",
                "origin": "cv:s1",
                "activity_type": "exhibition",
                "publishable": "yes",
            },
            {
                "activity_id": "cv-extra",
                "ledger_id": "p1",
                "title": "fictitious strand",
                "year": "2024",
                "origin": "cv:s1",
                "activity_type": "exhibition",
                "publishable": "yes",
            },
            {
                "activity_id": "roster-camp",
                "ledger_id": "p1",
                "title": "camp",
                "year": "2024",
                "origin": "EXAMPLE-CAMP-2024",
                "activity_type": "residency",
                "publishable": "yes",
            },
        ],
        membership=[
            {"ledger_id": "p1", "frame_code": "EXAMPLE-ONLY-2024"},
            {"ledger_id": "p1", "frame_code": "EXAMPLE-CAMP-2024"},
        ],
    )
    config = load(
        _config(
            tmp_path,
            """
[resolve.event_patterns]
EXAMPLE-CAMP = "example camp"

[resolve.edition_only]
EXAMPLE-CAMP = "fictitious strand"
EXAMPLE-ONLY = "edition only phrase"
""",
        )
    )
    shared = pattern_table(config.field_config.event_patterns, config.event_patterns)
    edition = pattern_table(config.field_config.edition_only, config.edition_only)
    assert event_pattern("EXAMPLE-ONLY-2024", shared) is None
    assert cv_mentions(
        [{"title": "edition only phrase", "venue": "", "year": 2024}],
        "EXAMPLE-ONLY-2024",
        [2024],
        shared,
    ) is None
    assert cv_mentions(
        [{"title": "fictitious strand", "venue": "", "year": 2024}],
        "EXAMPLE-CAMP-2024",
        [2024],
        shared,
    ) is None
    assert cv_mentions(
        [{"title": "example camp open studio", "venue": "", "year": 2024}],
        "EXAMPLE-CAMP-2024",
        [2024],
        shared,
    )
    # The config replaces a field prefix. Here the field table is empty, so the
    # toml rows are the whole edition-only table, still absent from E2.
    assert "EXAMPLE-ONLY" not in shared
    assert event_pattern("EXAMPLE-ONLY-2024", edition) == "edition only phrase"

    result = normalize(config)
    rows = {row["activity_id"]: row for row in read_csv(result.processed / "activities.csv")}
    assert rows["cv-only"]["event_link"] == "EXAMPLE-ONLY-2024"
    assert "P4" in rows["cv-only"]["rules"].split("|")
    assert "P4a" in rows["cv-only"]["rules"].split("|")
    assert rows["cv-extra"]["event_link"] == "EXAMPLE-CAMP-2024"
    assert "P4a" in rows["cv-extra"]["rules"].split("|")
    assert rows["cv-shared"]["event_link"] == "EXAMPLE-CAMP-2024"
    assert "P4" in rows["cv-shared"]["rules"].split("|")
    assert "P4a" not in rows["cv-shared"]["rules"].split("|")
    assert rows["roster"]["event_link"] == "EXAMPLE-ONLY-2024"
    assert "P4a" not in rows["roster"]["rules"].split("|")


def test_config_edition_only_replaces_the_field_prefix(tmp_path: Path) -> None:
    field = tmp_path / "field.toml"
    field.write_text(
        """
[resolve.events]
EXAMPLE-ONLY = 'from the shared table'
[resolve.edition_only]
EXAMPLE-ONLY = 'from the field'
""",
        encoding="utf-8",
    )
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Synthetic media-art field"
id_prefix = "GY"
languages = ["en"]

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "frames.yml"
field = "{field.as_posix()}"

[resolve.edition_only]
EXAMPLE-ONLY = "from the toml"
""",
        encoding="utf-8",
    )
    config = load(path)
    edition = pattern_table(config.field_config.edition_only, config.edition_only)
    shared = pattern_table(config.field_config.event_patterns, config.event_patterns)
    assert event_pattern("EXAMPLE-ONLY-2024", edition) == "from the toml"
    assert event_pattern("EXAMPLE-ONLY-2024", shared) == "from the shared table"
    assert "from the field" not in shared["EXAMPLE-ONLY"]


def test_shared_patterns_keep_the_three_tightenings_and_not_the_rejected_ones() -> None:
    patterns = dict(shipped_field().event_patterns)

    def hits(key: str, text: str) -> bool:
        return re.search(patterns[key], text, re.IGNORECASE) is not None

    assert hits("DAVINCI", "Unfold X")
    assert hits("DAVINCI", "da vinci")
    assert not hits("DAVINCI", "Unfolds at the gallery")
    assert not hits("DAVINCI", "Unfolding Visions")
    assert hits("MEDIACITY-SEOUL", "Media City Seoul")
    assert hits("MEDIACITY-SEOUL", "미디어 시티")
    assert not hits("MEDIACITY-SEOUL", "Media City Film Festival")
    assert hits("NJP-RANDOMACCESS", "Random Access")
    assert hits("NJP-RANDOMACCESS", "랜덤 액세스")
    assert not hits("NJP-RANDOMACCESS", "Random Access Memory")
    assert hits("PARADISE-ARTLAB", "Paradise")
    assert hits("ACT", "Asia Culture Center")
    assert hits("ACT", "ACT")
    assert hits("PLATFORM-L-PLAP", "Platform-L")
    assert hits("PLATFORM-L-PLAP", "PLAP")
