# SPDX-License-Identifier: AGPL-3.0-only
"""Ledger: permanent ids, activity-id stability, retirement, backups, roster upsert.

People in the fixtures are fictitious (김하늘 / Haneul Kim). URLs are example.org.
"""

from __future__ import annotations

import csv
import json
import os
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
from giye.ledger.ledger import Ledger, cv_row_owner, repoint_cv_activities
from giye.ledger.schemas import CV_SOURCES_FIELDS, empty_row

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo"
FIXTURE = ROOT / "tests" / "fixtures" / "ledger" / "artists.csv"
UA = "GiyeTest/0.1 (+https://example.org/contact)"

# Locked to the production namespace string. A change here changes every activity id.
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
    ledger = _ledger(tmp_path)
    first = [_person("LED-a", "GY-000001", "김하늘")]
    assert ledger.write("artists", first, task="seed") is None
    backups = ledger.config.work / "backups"
    assert list(backups.glob("artists-*-before-seed.csv")) == []
    second = first + [_person("LED-b", "GY-000002", "Haneul Kim")]
    copied = ledger.write("artists", second, task="edit")
    assert copied is not None
    assert copied.parent == backups
    assert copied.name.startswith("artists-")
    assert "-before-edit.csv" in copied.name
    previous = list(csv.DictReader(copied.open(encoding="utf-8")))
    assert [row["gy_id"] for row in previous] == ["GY-000001"]
    assert [row["gy_id"] for row in ledger.read("artists")] == ["GY-000001", "GY-000002"]
    third = second + [_person("LED-c", "GY-000003", "박서연")]
    again = ledger.write("artists", third, task="edit")
    assert again is not None and again != copied
    saved = list(csv.DictReader(again.open(encoding="utf-8")))
    assert [row["gy_id"] for row in saved] == ["GY-000001", "GY-000002"]
    assert [row["name_ko"] for row in ledger.read("artists")] == ["김하늘", "Haneul Kim", "박서연"]


def test_merge_refuses_without_evidence_and_retires_a_chain(tmp_path: Path):
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError, match="evidence"):
        ledger.merge("LED-a", "LED-b", evidence="", rule="E1")
    with pytest.raises(ValueError, match="evidence"):
        ledger.merge("LED-a", "LED-b", evidence="   ", rule="E1")
    with pytest.raises(ValueError, match="rule"):
        ledger.merge("LED-a", "LED-b", evidence="same website https://example.org/haneul", rule="")
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
    ledger.merge("LED-a", "LED-b", evidence="same website https://example.org/haneul", rule="E1")
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

    ledger.merge("LED-c", "LED-a", evidence="cv lists the 2019 residency", rule="E2")
    live = {row["ledger_id"]: row for row in ledger.read("artists")}
    assert set(live) == {"LED-c"}
    assert live["LED-c"]["gy_id"] == "GY-000003"
    assert ledger.redirects() == {"GY-000002": "GY-000003", "GY-000001": "GY-000003"}
    retired = {row["gy_id"]: row["merged_into_ledger_id"] for row in ledger.read("gy_retired")}
    assert retired == {"GY-000002": "LED-c", "GY-000001": "LED-c"}
    assert ledger.read("activities")[0]["ledger_id"] == "LED-c"
    assert ledger.read("cv_sources")[0]["ledger_id"] == "LED-c"
    assert ledger.next_gy_id() == "GY-000004"
    backups = list((ledger.config.work / "backups").glob("gy_retired-*-before-merge*.csv"))
    assert backups
    backed = "\n".join(path.read_text(encoding="utf-8") for path in backups)
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
    ledger.merge("LED-a", ["LED-b", "LED-c"], evidence="same website https://example.org/haneul", rule="E1")
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
    ledger.merge("LED-owner", "LED-drop", evidence="cv lists the residency https://example.org/cv", rule="E2")
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
    ledger.merge("LED-a", "LED-b", evidence="same website https://example.org/haneul", rule="E1")
    sources = ledger.read("cv_sources")
    assert [row["source_id"] for row in sources] == ["CV-snap"]
    assert ledger.read("activities")[0]["origin"] == "cv:CV-snap"
    saved = json.loads(extract.read_text(encoding="utf-8"))
    assert [item["source_id"] for item in saved["sources"]] == ["CV-snap"]
    assert saved["activities"][0]["source_id"] == "CV-snap"


def test_roster_ordinals_stay_stable_for_the_same_appearance(tmp_path: Path):
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
    assert len(activities) == 2
    assert activities[0]["activity_id"] != activities[1]["activity_id"]
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
    # A1 joins the two Lee/Haru spellings in one programme; A2 joins the forum
    # row whose English tokens agree. 23 roster rows, 21 people.
    assert len(artists) == 21
    assert [row["gy_id"] for row in artists] == [f"GY-{number:06d}" for number in range(1, 22)]
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
    assert ("Kim Haneul", "Kim Haneul") not in names
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
