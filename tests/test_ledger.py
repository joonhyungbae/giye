# SPDX-License-Identifier: AGPL-3.0-only
"""Ledger: permanent ids, activity-id stability, retirement, backups, roster upsert.

People in the fixtures are fictitious (김하늘 / Haneul Kim). URLs are example.org.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import date
from pathlib import Path

import pytest
import requests

from giye.collect.base import run_configured
from giye.config import load
from giye.ledger import (
    ACTIVITY_NAMESPACE,
    activity_id_for,
    activity_id_key,
    read_csv,
    take_activity_id,
    write_csv,
)
from giye.ledger.ids import reissue_frame_activity_ids, zip_activity_id_changes
from giye.ledger.io import backup_before_write, prune_backups, start_backup_run
from giye.ledger.ledger import Ledger, cv_row_owner, repoint_cv_activities
from giye.ledger.schemas import CV_SOURCES_FIELDS, empty_row

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo"
FIXTURE = ROOT / "tests" / "fixtures" / "ledger" / "artists.csv"
UA = "GiyeTest/0.1 (+https://example.org/contact)"

# Locked to ACTIVITY_NAMESPACE. A change here changes every activity id.
_FROZEN_KEY = {
    "ledger_id": "LED-abc",
    "source": "https://example.org/a",
    "title": "Hello, World!",
    "year": "2019",
    "activity_type": "other",
    "venue": "Hall",
    "origin": "FRAME",
}


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Synthetic media-art field (demo)"
id_prefix = "GY"
territory = "KR"
languages = ["ko", "en"]

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{(DEMO / "frames.yml").as_posix()}"

[collect]
user_agent = "{UA}"
min_delay_s = 0.0
collector_modules = ["{(DEMO / "collectors.py").as_posix()}"]

[collect.offline_roots]
"https://example.org" = "{(DEMO / "fixtures").as_posix()}"
""",
        encoding="utf-8",
    )
    return path


def _ledger(tmp_path: Path) -> Ledger:
    return Ledger.open(load(_config(tmp_path)))


def _person(ledger_id: str, gy_id: str, name: str) -> dict[str, str]:
    return {"ledger_id": ledger_id, "gy_id": gy_id, "name_ko": name, "name_en": name, "status": "STAGED"}


def test_csv_round_trip_keeps_korean_names_and_quoted_commas(tmp_path: Path):
    original = read_csv(FIXTURE)
    assert original[0]["name_ko"] == "김하늘"
    assert original[0]["name_en"] == "Haneul Kim"
    assert original[0]["aliases"] == "하늘|Haneul K."
    assert original[0]["reviewer_note"] == "kept, see source"
    assert original[0]["source_url"] == "https://example.org/residency/alumni"
    dest = tmp_path / "artists.csv"
    write_csv(path=dest, fields=list(original[0].keys()), rows=original)
    assert read_csv(dest) == original
    with pytest.raises(TypeError):
        write_csv(dest, list(original[0].keys()), original)  # type: ignore[misc]
    ledger = _ledger(tmp_path)
    ledger.write("artists", original, task="round-trip")
    assert ledger.read("artists")[0]["name_ko"] == "김하늘"
    assert ledger.read("artists")[0]["gy_id"] == "GY-000042"


def test_write_csv_keeps_existing_crlf_and_new_files_use_lf(tmp_path: Path):
    path = tmp_path / "artists.csv"
    path.write_bytes("ledger_id,name_ko\r\nLED-1,김하늘\r\n".encode())
    write_csv(path=path, fields=["ledger_id", "name_ko"], rows=[{"ledger_id": "LED-1", "name_ko": "김하늘"}])
    raw = path.read_bytes()
    assert raw.count(b"\r\n") == 2
    assert raw.replace(b"\r\n", b"").count(b"\n") == 0
    assert read_csv(path) == [{"ledger_id": "LED-1", "name_ko": "김하늘"}]
    fresh = tmp_path / "new.csv"
    write_csv(path=fresh, fields=["ledger_id"], rows=[{"ledger_id": "LED-2"}])
    assert b"\r\n" not in fresh.read_bytes()
    assert fresh.read_bytes().endswith(b"\n")


def test_activity_ids_are_stable_and_ordinals_do_not_share_an_id():
    assert str(ACTIVITY_NAMESPACE) == "72269c7b-f42a-5278-8ace-6f316e83e50a"
    key = activity_id_key(**_FROZEN_KEY)
    assert activity_id_for(key, 0) == "90cecb40-9411-5cb8-b1d0-afe9cf4d7f68"
    assert activity_id_for(key, 1) == "a3cc1bd5-1faf-5ace-bc98-49d6c6ab37e1"
    assert activity_id_for(key) == activity_id_for(activity_id_key(**{**_FROZEN_KEY, "title": "hello world"}))
    punctuated = activity_id_key(**{**_FROZEN_KEY, "venue": "Hall"})
    other_stop = activity_id_key(**{**_FROZEN_KEY, "venue": "halla"})
    assert activity_id_for(punctuated) != activity_id_for(other_stop)
    no_origin = activity_id_key(**{**_FROZEN_KEY, "origin": ""})
    assert activity_id_for(no_origin) != activity_id_for(key)
    counter: dict[str, int] = {}
    first = take_activity_id(counter, key)
    second = take_activity_id(counter, key)
    assert first == activity_id_for(key, 0)
    assert second == activity_id_for(key, 1)
    assert take_activity_id({}, key) == first


def test_reissue_rewrites_frame_rows_and_leaves_cv_rows():
    row = {
        "activity_id": "random",
        "ledger_id": "LED-abc",
        "origin": "FRAME",
        "source_url": "https://example.org/a",
        "title": "Hello, World!",
        "year": "2019",
        "activity_type": "other",
        "venue": "Hall",
    }
    cv_row = {**row, "activity_id": "keep-me", "origin": "cv:SRC"}
    changes = reissue_frame_activity_ids([row, cv_row], {"FRAME"})
    assert row["activity_id"] == "90cecb40-9411-5cb8-b1d0-afe9cf4d7f68"
    assert cv_row["activity_id"] == "keep-me"
    assert changes == [("random", row["activity_id"], "LED-abc", "FRAME")]
    assert reissue_frame_activity_ids([row], {"FRAME"}) == []
    old = [{"activity_id": "old", "ledger_id": "LED-abc", "origin": "FRAME", "title": "Hello"}]
    new = [{"activity_id": "new", "ledger_id": "LED-abc", "origin": "FRAME", "title": "Hello"}]
    paired = zip_activity_id_changes(old, new, lambda item: item["title"])
    assert paired == [("old", "new", "LED-abc", "FRAME")]


def test_gy_id_is_never_reused_or_taken_from_the_row_position(tmp_path: Path):
    ledger = _ledger(tmp_path)
    ledger.write(
        "artists",
        [_person("LED-a", "GY-000005", "김하늘"), _person("LED-b", "GY-000001", "Haneul Kim")],
        task="seed",
    )
    reversed_rows = list(reversed(ledger.read("artists")))
    ledger.write("artists", reversed_rows, task="reorder")
    by_id = {row["ledger_id"]: row["gy_id"] for row in ledger.read("artists")}
    assert by_id == {"LED-a": "GY-000005", "LED-b": "GY-000001"}
    # A gap is not filled, and the next number is not the row count.
    assert ledger.next_gy_id() == "GY-000006"


def test_backup_is_created_before_every_write(tmp_path: Path):
    start_backup_run()
    ledger = _ledger(tmp_path)
    first = [_person("LED-a", "GY-000001", "김하늘")]
    assert ledger.write("artists", first, task="seed") is None
    backups = ledger.config.work / "backups"
    assert list(backups.glob("artists-*-before-seed.csv*")) == []
    second = first + [_person("LED-b", "GY-000002", "Haneul Kim")]
    copied = ledger.write("artists", second, task="edit")
    assert copied is not None
    assert copied.parent == backups
    assert copied.name.startswith("artists-")
    assert copied.name.endswith("-before-edit.csv.gz")
    previous = read_csv(copied)
    assert [row["gy_id"] for row in previous] == ["GY-000001"]
    assert [row["gy_id"] for row in ledger.read("artists")] == ["GY-000001", "GY-000002"]
    # Same file and task in the same run: the copy of the run's starting bytes covers it.
    third = second + [_person("LED-c", "GY-000003", "박서연")]
    again = ledger.write("artists", third, task="edit")
    assert again == copied
    assert len(list(backups.glob("artists-*-before-edit*"))) == 1
    assert [row["gy_id"] for row in read_csv(again)] == ["GY-000001"]
    assert [row["name_ko"] for row in ledger.read("artists")] == ["김하늘", "Haneul Kim", "박서연"]
    # Another task in the same run takes its own copy.
    other = ledger.write("artists", third, task="hide")
    assert other is not None and other.name.endswith("-before-hide.csv.gz")
    assert [row["gy_id"] for row in read_csv(other)] == ["GY-000001", "GY-000002", "GY-000003"]
    # A new run with the same task the same day gets a numbered copy; the first stays.
    start_backup_run()
    later = ledger.write("artists", second, task="edit")
    assert later is not None and later != copied
    assert later.name.endswith("-before-edit-2.csv.gz")
    assert copied.is_file()
    assert [row["gy_id"] for row in read_csv(later)] == ["GY-000001", "GY-000002", "GY-000003"]


def test_backup_is_gzip_and_a_plain_copy_holds_its_slot(tmp_path: Path):
    start_backup_run()
    ledger = _ledger(tmp_path)
    ledger.write("artists", [_person("LED-a", "GY-000001", "김하늘")], task="seed")
    backups = ledger.config.work / "backups"
    backups.mkdir(parents=True)
    copied = backup_before_write(ledger.path("artists"), backups, "edit")
    assert copied is not None
    assert gzip.decompress(copied.read_bytes()) == ledger.path("artists").read_bytes()
    # An uncompressed copy from an older package version is not overwritten.
    start_backup_run()
    plain = copied.with_name(copied.name.replace("-before-edit.csv.gz", "-before-pull.csv"))
    plain.write_text("old", encoding="utf-8")
    nxt = backup_before_write(ledger.path("artists"), backups, "pull")
    assert nxt is not None and nxt.name.endswith("-before-pull-2.csv.gz")
    assert plain.read_text(encoding="utf-8") == "old"


def test_prune_keeps_newest_backup_and_foreign_files(tmp_path: Path):
    backups = tmp_path / "backups"
    backups.mkdir()
    names = [
        "artists-20260101-before-collect.csv.gz",
        "artists-20260102-before-collect-2.csv",
        "artists-20260901-before-collect.csv.gz",
        "frame_membership-20250101-before-merge.csv.gz",  # newest of its file: kept
        "activity_id_map-20250101.csv",  # not a ledger backup name
        "notes.txt",
    ]
    for name in names:
        (backups / name).write_text("x", encoding="utf-8")
    removed = prune_backups(backups, 90, today=date(2026, 10, 6))
    assert [path.name for path in removed] == [
        "artists-20260101-before-collect.csv.gz",
        "artists-20260102-before-collect-2.csv",
    ]
    assert sorted(path.name for path in backups.iterdir()) == sorted(names[2:])
    with pytest.raises(ValueError):
        prune_backups(backups, 0)


def test_keep_backups_days_prunes_once_per_run(tmp_path: Path):
    config = Path(_config(tmp_path))
    config.write_text(config.read_text(encoding="utf-8") + "\n[ledger]\nkeep_backups_days = 30\n", encoding="utf-8")
    ledger = Ledger.open(load(config))
    assert ledger.config.keep_backups_days == 30
    backups = ledger.config.work / "backups"
    backups.mkdir(parents=True)
    stale = backups / "artists-20000101-before-collect.csv.gz"
    stale.write_text("x", encoding="utf-8")
    start_backup_run()
    ledger.write("artists", [_person("LED-a", "GY-000001", "김하늘")], task="seed")
    ledger.write("artists", [_person("LED-a", "GY-000001", "김하늘")], task="edit")
    assert not stale.exists()
    assert len(list(backups.glob("artists-*-before-edit.csv.gz"))) == 1


def test_keep_backups_days_must_be_a_positive_whole_number(tmp_path: Path):
    config = Path(_config(tmp_path))
    config.write_text(config.read_text(encoding="utf-8") + "\n[ledger]\nkeep_backups_days = 0\n", encoding="utf-8")
    with pytest.raises(TypeError):
        load(config)

def test_merge_refuses_without_evidence_and_retires_a_chain(tmp_path: Path):
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError, match="evidence"):
        ledger.merge("LED-a", "LED-b", evidence="", rule="E1")
    with pytest.raises(ValueError, match="evidence"):
        ledger.merge("LED-a", "LED-b", evidence="   ", rule="E1")
    with pytest.raises(ValueError, match="rule"):
        ledger._merge_rows("LED-a", "LED-b", evidence="same website https://example.org/haneul", rule="")
    assert list(ledger.directory.glob("*.csv")) == []

    ledger.write(
        "artists",
        [
            _person("LED-a", "GY-000001", "김하늘"),
            _person("LED-b", "GY-000002", "Haneul Kim"),
            _person("LED-c", "GY-000003", "하늘"),
        ],
        task="seed",
    )
    ledger.write(
        "activities",
        [
            {
                "activity_id": "act-b",
                "ledger_id": "LED-b",
                "title": "Example Residency",
                "year": "2019",
                "source_url": "https://example.org/residency/alumni",
                "collected_at": "2026-10-04",
                "origin": "EXAMPLE-RESIDENCY",
            }
        ],
        task="seed",
    )
    ledger.write(
        "frame_membership",
        [
            {
                "ledger_id": "LED-b",
                "frame_code": "EXAMPLE-RESIDENCY",
                "source_url": "https://example.org/b",
                "collected_at": "2026-10-04",
            },
            {
                "ledger_id": "LED-a",
                "frame_code": "EXAMPLE-RESIDENCY",
                "source_url": "https://example.org/a",
                "collected_at": "2026-10-04",
            },
        ],
        task="seed",
    )
    ledger.write(
        "cv_sources",
        [empty_row(CV_SOURCES_FIELDS, source_id="CV-b", ledger_id="LED-b", url="https://example.org/cv", lang="en")],
        task="seed",
    )
    ledger._merge_rows("LED-a", "LED-b", evidence="same website https://example.org/haneul", rule="E1")
    artists = {row["ledger_id"]: row for row in ledger.read("artists")}
    assert "LED-b" not in artists
    assert "GY-000002" not in {row["gy_id"] for row in artists.values()}
    assert "merge_evidence=same website https://example.org/haneul" in artists["LED-a"]["reviewer_note"]
    assert "rule=E1" in artists["LED-a"]["reviewer_note"]
    assert artists["LED-a"]["aliases"] == "Haneul Kim"
    assert artists["LED-a"]["name_ko"] == "김하늘"
    assert ledger.read("activities")[0]["ledger_id"] == "LED-a"
    assert ledger.read("cv_sources")[0]["ledger_id"] == "LED-a"
    membership = ledger.read("frame_membership")
    assert len(membership) == 1
    assert membership[0]["ledger_id"] == "LED-a"
    assert ledger.redirects() == {"GY-000002": "GY-000001"}

    ledger._merge_rows("LED-c", "LED-a", evidence="cv lists the 2019 residency", rule="E2")
    live = {row["ledger_id"]: row for row in ledger.read("artists")}
    assert set(live) == {"LED-c"}
    assert live["LED-c"]["gy_id"] == "GY-000003"
    assert ledger.redirects() == {"GY-000002": "GY-000003", "GY-000001": "GY-000003"}
    retired = {row["gy_id"]: row["merged_into_ledger_id"] for row in ledger.read("gy_retired")}
    assert retired == {"GY-000002": "LED-c", "GY-000001": "LED-c"}
    assert ledger.read("activities")[0]["ledger_id"] == "LED-c"
    assert ledger.read("cv_sources")[0]["ledger_id"] == "LED-c"
    assert ledger.next_gy_id() == "GY-000004"
    backups = list((ledger.config.work / "backups").glob("gy_retired-*-before-merge*.csv.gz"))
    assert backups
    backed = "\n".join(gzip.decompress(path.read_bytes()).decode("utf-8") for path in backups)
    assert "LED-a" in backed


def test_survivor_adopts_the_lowest_dropped_gy_id(tmp_path: Path):
    ledger = _ledger(tmp_path)
    ledger.write(
        "artists",
        [
            _person("LED-a", "", "김하늘"),
            _person("LED-b", "GY-000004", "Other"),
            _person("LED-c", "GY-000002", "Haneul Kim"),
        ],
        task="seed",
    )
    ledger._merge_rows("LED-a", ["LED-b", "LED-c"], evidence="same website https://example.org/haneul", rule="E1")
    survivor = next(row for row in ledger.read("artists") if row["ledger_id"] == "LED-a")
    assert survivor["gy_id"] == "GY-000002"
    assert ledger.redirects() == {"GY-000004": "GY-000002"}
    assert ledger.next_gy_id() == "GY-000005"


def test_cv_row_belongs_to_the_owner_of_its_source(tmp_path: Path):
    sources = [empty_row(CV_SOURCES_FIELDS, source_id="CV-1", ledger_id="LED-owner", url="https://example.org/cv")]
    activity = {"ledger_id": "LED-old", "origin": "cv:CV-1", "title": "Show", "activity_id": "keep"}
    other = {"ledger_id": "LED-old", "origin": "EXAMPLE-RESIDENCY", "title": "Roster", "activity_id": "stay"}
    assert cv_row_owner(other, sources) is None
    assert cv_row_owner(activity, sources) == "LED-owner"
    assert repoint_cv_activities([activity, other], sources) == 1
    assert activity["ledger_id"] == "LED-owner"
    assert activity["activity_id"] == "keep"
    assert other["ledger_id"] == "LED-old"

    ledger = _ledger(tmp_path)
    ledger.write(
        "artists",
        [
            _person("LED-owner", "GY-000001", "김하늘"),
            _person("LED-old", "GY-000002", "Haneul Kim"),
            _person("LED-drop", "GY-000003", "drop"),
        ],
        task="seed",
    )
    ledger.write("cv_sources", sources, task="seed")
    ledger.write(
        "activities",
        [
            {
                "activity_id": "keep",
                "ledger_id": "LED-old",
                "origin": "cv:CV-1",
                "title": "Show",
                "source_url": "https://example.org/cv",
                "collected_at": "2026-10-04",
            }
        ],
        task="seed",
    )
    # The dropped row is not the activity's person. The CV source already names the owner.
    ledger._merge_rows("LED-owner", "LED-drop", evidence="cv lists the residency https://example.org/cv", rule="E2")
    moved = ledger.read("activities")[0]
    assert moved["ledger_id"] == "LED-owner"
    assert moved["activity_id"] == "keep"


def test_merge_collapses_two_registrations_of_one_cv(tmp_path: Path):
    ledger = _ledger(tmp_path)
    ledger.write(
        "artists",
        [_person("LED-a", "GY-000001", "김하늘"), _person("LED-b", "GY-000002", "Haneul Kim")],
        task="seed",
    )
    ledger.write(
        "cv_sources",
        [
            empty_row(
                CV_SOURCES_FIELDS,
                source_id="CV-plain",
                ledger_id="LED-a",
                url="https://example.org/cv",
                lang="en",
            ),
            empty_row(
                CV_SOURCES_FIELDS,
                source_id="CV-snap",
                ledger_id="LED-a",
                url="https://example.org/cv",
                lang="en",
                snapshot_path="raw/cv.txt",
            ),
        ],
        task="seed",
    )
    ledger.write(
        "activities",
        [
            {
                "activity_id": "from-plain",
                "ledger_id": "LED-a",
                "origin": "cv:CV-plain",
                "title": "Show",
                "source_url": "https://example.org/cv",
                "collected_at": "2026-10-04",
            }
        ],
        task="seed",
    )
    extract = ledger.config.work / "cv_extract" / "LED-a.json"
    extract.parent.mkdir(parents=True, exist_ok=True)
    extract.write_text(
        json.dumps(
            {
                "ledger_id": "LED-a",
                "sources": [{"source_id": "CV-plain"}, {"source_id": "CV-snap"}],
                "activities": [{"source_id": "CV-plain", "title": "Show"}],
            }
        ),
        encoding="utf-8",
    )
    ledger._merge_rows("LED-a", "LED-b", evidence="same website https://example.org/haneul", rule="E1")
    sources = ledger.read("cv_sources")
    assert [row["source_id"] for row in sources] == ["CV-snap"]
    assert ledger.read("activities")[0]["origin"] == "cv:CV-snap"
    saved = json.loads(extract.read_text(encoding="utf-8"))
    assert [item["source_id"] for item in saved["sources"]] == ["CV-snap"]
    assert saved["activities"][0]["source_id"] == "CV-snap"


def test_identical_appearances_in_one_edition_collapse_into_one_row(tmp_path: Path):
    ledger = _ledger(tmp_path)
    rows = [
        {
            "name_ko": "김하늘",
            "name_en": "",
            "year": "2019",
            "source_url": "https://example.org/residency/alumni",
            "collected_at": "2026-10-04",
        },
        {
            "name_ko": "김하늘",
            "name_en": "",
            "year": "2019",
            "source_url": "https://example.org/residency/alumni",
            "collected_at": "2026-10-04",
        },
    ]
    ledger.apply_roster("EXAMPLE-RESIDENCY", rows, task="collect")
    activities = ledger.read("activities")
    # Same person, same edition, same credit: one fact, one row.
    assert len(activities) == 1
    assert {row["gy_id"] for row in ledger.read("artists")} == {"GY-000001"}
    first_ids = [row["activity_id"] for row in activities]
    ledger.apply_roster("EXAMPLE-RESIDENCY", rows, task="collect")
    assert [row["activity_id"] for row in ledger.read("activities")] == first_ids
    assert len(ledger.read("artists")) == 1
    assert len(ledger.read("frame_membership")) == 1


def test_demo_collectors_fill_the_ledger_and_keep_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom(self, url, *args, **kwargs):
        raise AssertionError(f"network used: {url}")

    monkeypatch.setattr(requests.Session, "get", boom)
    config = load(_config(tmp_path))
    run_configured(config, collected_at="2026-10-04", run_id="2026-10-04T00:00:00Z")
    ledger = Ledger.open(config)
    artists = ledger.read("artists")
    # A1 joins the two Lee/Haru spellings in one programme. The forum's Kim Haneul is a
    # Latin-only personal name in another programme, so it is not joined on name alone
    # and opens its own record. 23 roster rows, 22 records before resolve.
    assert len(artists) == 22
    assert [row["gy_id"] for row in artists] == [f"GY-{number:06d}" for number in range(1, 23)]
    assert {row["source_url"] for row in artists} <= {
        "https://example.org/residency/alumni",
        "https://example.org/workshop/fellows",
        "https://example.org/forum/guests",
    }
    assert {row["collected_at"] for row in artists} == {"2026-10-04"}
    names = {(row["name_ko"], row["name_en"]) for row in artists}
    assert ("김하늘", "") in names
    assert ("Haneul Kim", "Haneul Kim") in names
    assert ("Lee Haru", "Lee Haru") in names
    assert ("이하루", "") in names
    assert ("Kim Haneul", "Kim Haneul") in names
    membership = ledger.read("frame_membership")
    assert len(membership) == 23
    edition_codes = {
        "EXAMPLE-RESIDENCY-2019",
        "EXAMPLE-RESIDENCY-2020",
        "EXAMPLE-WORKSHOP-2020",
        "EXAMPLE-WORKSHOP-2021",
        "EXAMPLE-WORKSHOP-2022",
        "EXAMPLE-FORUM-2023",
    }
    assert {row["frame_code"] for row in membership} == edition_codes
    activities = ledger.read("activities")
    assert sorted(row["year"] for row in activities) == (
        ["2019"] * 10 + ["2020"] * 2 + ["2021"] * 8 + ["2022"] * 2 + ["2023"]
    )
    assert {row["origin"] for row in activities} == edition_codes
    assert {row["title"] for row in activities} == edition_codes
    assert all(row["source_url"].startswith("https://example.org/") for row in activities)
    gy_ids = [row["gy_id"] for row in artists]
    ledger_ids = [row["ledger_id"] for row in artists]
    activity_ids = [row["activity_id"] for row in activities]
    run_configured(config, collected_at="2026-10-04", run_id="2026-10-04T00:00:01Z")
    again = Ledger.open(config)
    assert [row["gy_id"] for row in again.read("artists")] == gy_ids
    assert [row["ledger_id"] for row in again.read("artists")] == ledger_ids
    assert [row["activity_id"] for row in again.read("activities")] == activity_ids
    assert len(again.read("frame_membership")) == 23
    lock = (config.ledger / ".ledger.lock").read_text(encoding="utf-8")
    assert f"pid {os.getpid()}" in lock


_SRC = "https://example.org/residency/alumni"


def _roster_row(name_ko: str = "김하늘", name_en: str = "", **extra: object) -> dict:
    return {"name_ko": name_ko, "name_en": name_en, "year": "2019", "source_url": _SRC,
            "collected_at": "2026-10-04", **extra}


def test_roster_row_states_its_activity_note_and_extra_rows(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = _roster_row(
        role="artist",
        note="listed under residents",
        activity={"title": "Night Garden", "venue": "Example Hall", "activity_type": "residency"},
        extra_activities=[{"title": "Open Studio", "activity_type": "exhibition", "venue": "Studio 3"}],
    )
    ledger.apply_roster("EXAMPLE-RESIDENCY-2019", [row], task="collect")
    acts = {a["title"]: a for a in ledger.read("activities")}
    assert set(acts) == {"Night Garden", "Open Studio"}
    main = acts["Night Garden"]
    assert (main["venue"], main["activity_type"], main["role"], main["reviewer_note"]) == (
        "Example Hall", "residency", "artist", "listed under residents")
    assert main["origin"] == acts["Open Studio"]["origin"] == "EXAMPLE-RESIDENCY-2019"
    assert acts["Open Studio"]["year"] == "2019" and acts["Open Studio"]["source_url"] == _SRC
    ids = sorted(a["activity_id"] for a in ledger.read("activities"))
    ledger.apply_roster("EXAMPLE-RESIDENCY-2019", [row], task="collect")
    assert sorted(a["activity_id"] for a in ledger.read("activities")) == ids
    with pytest.raises(ValueError):
        ledger.apply_roster("EXAMPLE-RESIDENCY-2019", [_roster_row(activity={"titel": "x"})], task="collect")
    with pytest.raises(ValueError):
        ledger.apply_roster("EXAMPLE-RESIDENCY-2019", [_roster_row(extra_activities=[{"venue": "x"}])], task="collect")


def test_recollection_never_deletes_or_impoverishes_activity_rows(tmp_path: Path):
    ledger = _ledger(tmp_path)
    code = "EXAMPLE-RESIDENCY-2019"
    ledger.apply_roster(code, [_roster_row()], task="collect")
    lid = ledger.read("artists")[0]["ledger_id"]
    # Rows an older collector wrote: a richer appearance row, a career row under the
    # same origin, and a row of another edition. None of them is this run's output.
    old = [
        empty_row(ledger.fields("activities"), activity_id="old-rich", ledger_id=lid, title="Night Garden",
                  venue="Example Hall", year="2019", activity_type="residency", role="artist", source_url=_SRC,
                  source_type="PUBLIC_RECORD", collected_at="2026-09-01", publishable="yes",
                  reviewer_note="from the 2019 booklet", origin=code),
        empty_row(ledger.fields("activities"), activity_id="old-career", ledger_id=lid, title="Solo show",
                  venue="Gallery", year="2018", activity_type="exhibition", source_url=_SRC, origin=code),
        empty_row(ledger.fields("activities"), activity_id="old-other", ledger_id=lid, title="Talk",
                  year="2017", activity_type="talk", source_url=_SRC, origin="EXAMPLE-ARCHIVE-1"),
    ]
    ledger.write("activities", old, task="test")
    # A run that states nothing (title = edition code) keeps all three as they are.
    ledger.apply_roster(code, [_roster_row()], task="collect")
    assert ledger.read("activities") == old
    # A poorer row for the same work only fills the empty column; nothing is blanked.
    ledger.apply_roster(code, [_roster_row(activity={"title": "Night Garden", "venue": "Example Hall 2",
                                                     "activity_type": "residency"})], task="collect")
    rich = next(a for a in ledger.read("activities") if a["activity_id"] == "old-rich")
    assert rich["venue"] == "Example Hall" and rich["reviewer_note"] == "from the 2019 booklet"
    assert len(ledger.read("activities")) == 3
    # Even a row that fills every column the old one fills changes nothing: stored values win.
    ledger.apply_roster(code, [_roster_row(role="artist", note="checked again",
                                           activity={"title": "Night Garden", "venue": "Example Hall 2",
                                                     "activity_type": "residency"})], task="collect")
    assert ledger.read("activities") == old


def test_titled_row_fills_an_earlier_placeholder_without_retitling_it(tmp_path: Path):
    ledger = _ledger(tmp_path)
    code = "EXAMPLE-RESIDENCY-2019"
    ledger.apply_roster(code, [_roster_row()], task="collect")
    before = ledger.read("activities")
    assert [a["title"] for a in before] == [code]
    ledger.apply_roster(code, [_roster_row(activity={"title": "Night Garden", "venue": "Example Hall"})], task="collect")
    after = ledger.read("activities")
    # The appearance is already recorded: no second row, the id and title stay, the empty venue is filled.
    assert [(a["activity_id"], a["title"], a["venue"]) for a in after] == [
        (before[0]["activity_id"], code, "Example Hall")]


def test_recollecting_an_existing_person_changes_only_what_is_missing(tmp_path: Path):
    ledger = _ledger(tmp_path)
    code = "EXAMPLE-RESIDENCY-2019"
    team = _roster_row("", "Studio Example", members="Jun Seo, Ara Lim", reviewer_note="collective")
    ledger.apply_roster(code, [_roster_row(), team], task="collect")
    artists = ledger.read("artists")
    for row in artists:
        row["updated_at"] = "2000-01-01T00:00:00Z"
        if row["name_en"] == "Studio Example":
            # An older collector's spelling of the list, and a person with no native-script name.
            row["reviewer_note"] = "collective; members=Jun Seo, Ara Lim"
            row["name_ko"] = ""
    ledger.write("artists", artists, task="test")
    pipe_team = _roster_row("", "Studio Example", members=["Jun Seo", "Ara Lim"], reviewer_note="collective")
    ledger.apply_roster(code, [_roster_row(), pipe_team], task="collect")
    after = {row["ledger_id"]: row for row in ledger.read("artists")}
    assert len(after) == 2
    person = next(row for row in after.values() if row["name_ko"] == "김하늘")
    studio = next(row for row in after.values() if row["name_en"] == "Studio Example")
    assert person["updated_at"] == "2000-01-01T00:00:00Z"
    assert studio["name_ko"] == ""
    # The list gains no name, so its older spelling stays byte-identical.
    assert studio["reviewer_note"] == "collective; members=Jun Seo, Ara Lim"
    assert studio["updated_at"] == "2000-01-01T00:00:00Z"
    ledger.apply_roster(code, [_roster_row(), pipe_team], task="collect")
    again = {row["ledger_id"]: row for row in ledger.read("artists")}
    assert again[studio["ledger_id"]] == studio
    assert again[person["ledger_id"]] == person


def test_roster_row_with_several_websites_writes_each_link_once(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = _roster_row(website="https://example.org/a", websites=["https://example.org/b", "https://example.org/a"])
    ledger.apply_roster("EXAMPLE-RESIDENCY-2019", [row], task="collect")
    ledger.apply_roster("EXAMPLE-RESIDENCY-2019", [row], task="collect")
    assert sorted(link["url"] for link in ledger.read("links")) == ["https://example.org/a", "https://example.org/b"]


def test_recollection_keeps_dates_ids_and_curated_values(tmp_path: Path):
    """Re-collection adds and fills only: no re-dating, no re-keying, no overwrite of a curated value."""
    ledger = _ledger(tmp_path)
    code = "EXAMPLE-RESIDENCY-2019"
    first = _roster_row(collected_at="2026-09-01", activity={"title": "Night Garden"})
    ledger.apply_roster(code, [first], task="collect")
    acts = ledger.read("activities")
    acts[0]["publishable"] = "no"
    acts[0]["reviewer_note"] = "kept by hand; superseded_by_cv"
    ledger.write("activities", acts, task="test")
    before = ledger.read("activities"), ledger.read("frame_membership")
    # The replayed page has another date, another citation and a note: none of it is new content.
    replay = _roster_row(collected_at="2026-10-04", source_url="https://example.org/residency/2019",
                         note="from the replay", activity={"title": "Night Garden", "venue": "Example Hall"})
    ledger.apply_roster(code, [replay], task="collect")
    acts, membership = ledger.read("activities"), ledger.read("frame_membership")
    assert len(acts) == 1 and membership == before[1]
    row = acts[0]
    assert row["activity_id"] == before[0][0]["activity_id"]
    assert (row["collected_at"], row["publishable"], row["source_url"]) == ("2026-09-01", "no", _SRC)
    assert row["reviewer_note"] == "kept by hand; superseded_by_cv"
    assert row["venue"] == "Example Hall"  # the one empty field is filled
    ledger.apply_roster(code, [replay], task="collect")
    assert ledger.read("activities") == acts


def test_second_appearance_merges_into_the_existing_row_by_title(tmp_path: Path):
    ledger = _ledger(tmp_path)
    code = "EXAMPLE-RESIDENCY-2019"
    ledger.apply_roster(code, [_roster_row()], task="collect")
    lid = ledger.read("artists")[0]["ledger_id"]
    # A row an older collector keyed differently (another source URL, its own id).
    old = empty_row(ledger.fields("activities"), activity_id="old-1", ledger_id=lid, title="Night Garden",
                    year="2019", activity_type="residency", source_url="https://example.org/old",
                    collected_at="2026-09-01", origin=code)
    ledger.write("activities", [old], task="test")
    twice = [
        _roster_row(role="artist", activity={"title": "Night Garden"}),
        _roster_row(role="team lead", activity={"title": "Night  Garden"}),
    ]
    ledger.apply_roster(code, twice, task="collect")
    acts = ledger.read("activities")
    assert [row["activity_id"] for row in acts] == ["old-1"]
    assert acts[0]["role"] == "artist" and acts[0]["collected_at"] == "2026-09-01"


def test_appearance_without_an_activity_row(tmp_path: Path):
    ledger = _ledger(tmp_path)
    code = "EXAMPLE-RESIDENCY"
    ledger.apply_roster(code, [_roster_row(year="", activity=False)], task="collect")
    assert [row["frame_code"] for row in ledger.read("frame_membership")] == [code]
    assert ledger.read("activities") == []
    extra = _roster_row("박서연", year="", activity=False, extra_activities=[{"title": "Open Studio", "year": "2021"}])
    ledger.apply_roster(code, [extra], task="collect")
    assert [row["title"] for row in ledger.read("activities")] == ["Open Studio"]


def test_extra_activity_with_a_stated_empty_source_has_no_source(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = _roster_row(extra_activities=[{"title": "Open Studio", "source_url": ""}, {"title": "Talk"}])
    ledger.apply_roster("EXAMPLE-RESIDENCY-2019", [row], task="collect")
    acts = {a["title"]: a for a in ledger.read("activities")}
    assert (acts["Open Studio"]["source_url"], acts["Open Studio"]["publishable"]) == ("", "no")
    assert acts["Talk"]["source_url"] == _SRC
    before = ledger.read("activities")
    ledger.apply_roster("EXAMPLE-RESIDENCY-2019", [row], task="collect")
    assert ledger.read("activities") == before


def test_extra_activity_with_a_stated_empty_year_stays_undated(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = _roster_row(extra_activities=[{"title": "Career line", "year": ""}, {"title": "Dated line"}])
    ledger.apply_roster("EXAMPLE-RESIDENCY-2019", [row], task="collect")
    acts = {a["title"]: a for a in ledger.read("activities")}
    assert acts["Career line"]["year"] == ""
    assert acts["Dated line"]["year"] == _roster_row()["year"]


def test_person_note_goes_on_a_new_person_and_on_an_existing_one_only_on_request(tmp_path: Path):
    ledger = _ledger(tmp_path)
    code = "EXAMPLE-RESIDENCY-2019"
    ledger.apply_roster(code, [_roster_row(reviewer_note="example_2019; raw=김하늘")], task="collect")
    assert ledger.read("artists")[0]["reviewer_note"] == "example_2019; raw=김하늘"
    artists = ledger.read("artists")
    artists[0]["reviewer_note"] = "curated  text; raw=김하늘"
    artists[0]["updated_at"] = "2000-01-01T00:00:00Z"
    ledger.write("artists", artists, task="test")
    # Default: an existing person's note is not touched, even by a team marker.
    ledger.apply_roster(code, [_roster_row(reviewer_note="example_2020; team")], task="collect")
    assert ledger.read("artists") == artists
    # On request: only the segments not already there (whitespace-normalised) are appended.
    asked = _roster_row(reviewer_note="curated text; raw=김하늘; example_2020", reviewer_note_existing=True)
    ledger.apply_roster(code, [asked], task="collect")
    assert ledger.read("artists")[0]["reviewer_note"] == "curated  text; raw=김하늘; example_2020"
    ledger.apply_roster(code, [asked], task="collect")
    assert ledger.read("artists")[0]["reviewer_note"] == "curated  text; raw=김하늘; example_2020"


def test_members_list_gains_new_names_only(tmp_path: Path):
    ledger = _ledger(tmp_path)
    code = "EXAMPLE-RESIDENCY-2019"
    ledger.apply_roster(code, [_roster_row("", "Studio Example", members="Jun Seo, Ara Lim")], task="collect")
    artists = ledger.read("artists")
    artists[0]["reviewer_note"] = "members=Jun Seo, Ara Lim"
    ledger.write("artists", artists, task="test")
    ledger.apply_roster(code, [_roster_row("", "Studio Example", members="Ara Lim")], task="collect")
    assert ledger.read("artists")[0]["reviewer_note"] == "members=Jun Seo, Ara Lim"
    ledger.apply_roster(code, [_roster_row("", "Studio Example", members="Ara Lim|Min Cho")], task="collect")
    assert ledger.read("artists")[0]["reviewer_note"] == "members=Jun Seo|Ara Lim|Min Cho"


def test_members_note_is_not_started_on_an_existing_record(tmp_path: Path):
    ledger = _ledger(tmp_path)
    code = "EXAMPLE-RESIDENCY-2019"
    ledger.apply_roster(code, [_roster_row("", "Studio Example")], task="collect")
    before = ledger.read("artists")
    ledger.apply_roster(code, [_roster_row("", "Studio Example", members="Jun Seo|Ara Lim")], task="collect")
    assert ledger.read("artists") == before
    # A record this apply creates gets the list.
    ledger.apply_roster(code, [_roster_row("", "Studio Other", members="Min Cho")], task="collect")
    other = next(row for row in ledger.read("artists") if row["name_en"] == "Studio Other")
    assert other["reviewer_note"] == "members=Min Cho"


def test_roster_sites_are_typed_like_the_production_collectors(tmp_path: Path):
    from giye.ledger.ledger import roster_link_type

    assert roster_link_type("https://www.instagram.com/example") == "social"
    assert roster_link_type("https://x.com/example") == "social"
    assert roster_link_type("https://examplex.com/") == "website"
    assert roster_link_type("https://vimeo.com/example") == "video"
    assert roster_link_type("https://github.com/example") == "repository"
    ledger = _ledger(tmp_path)
    row = _roster_row(website="https://example.org/haneul", websites=["https://www.instagram.com/example"])
    ledger.apply_roster("EXAMPLE-RESIDENCY-2019", [row], task="collect")
    kinds = {link["url"]: link["link_type"] for link in ledger.read("links")}
    assert kinds == {"https://example.org/haneul": "website", "https://www.instagram.com/example": "social"}


def test_write_csv_is_atomic_and_keeps_the_old_file_on_failure(tmp_path: Path):
    from giye.ledger.io import read_csv, write_csv

    path = tmp_path / "table.csv"
    write_csv(path=path, fields=["a", "b"], rows=[{"a": "1", "b": "2"}])
    path.chmod(0o640)

    def rows():
        yield {"a": "3", "b": "4"}
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        write_csv(path=path, fields=["a", "b"], rows=rows())
    assert read_csv(path) == [{"a": "1", "b": "2"}]
    assert [item.name for item in tmp_path.iterdir()] == ["table.csv"]
    write_csv(path=path, fields=["a", "b"], rows=[{"a": "5", "b": "6"}])
    assert read_csv(path) == [{"a": "5", "b": "6"}]
    assert path.stat().st_mode & 0o777 == 0o640
