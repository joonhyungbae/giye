# SPDX-License-Identifier: AGPL-3.0-only
"""Roster attachment A1–A6. People are fictitious. URLs are example.org.

A rule that fires writes its id on the membership row. A same-name pair from
two programmes that no rule attaches stays two people and is queued.
"""

from __future__ import annotations

from pathlib import Path

from giye.config import load
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import ARTISTS_FIELDS, empty_row

ROOT = Path(__file__).resolve().parents[1]


def _ledger(tmp_path: Path) -> Ledger:
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Attachment fixtures"
id_prefix = "GY"
[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{(ROOT / "examples" / "demo" / "frames.yml").as_posix()}"
""",
        encoding="utf-8",
    )
    return Ledger.open(load(path))


def _row(name_ko: str, name_en: str = "", **extra: str) -> dict[str, str]:
    row = {
        "name_ko": name_ko,
        "name_en": name_en,
        "source_url": "https://example.org/roster",
        "collected_at": "2026-01-15",
    }
    row.update(extra)
    return row


def _rules(ledger: Ledger) -> list[tuple[str, str]]:
    return [(row["frame_code"], row["attach_rule"]) for row in ledger.read("frame_membership")]


def test_a1_same_family_records_the_rule_and_beats_english_agreement(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    person = _row("김하늘", "Haneul Kim")
    ledger.apply_roster("NORTH-2019", [person], task="collect")
    ledger.apply_roster("NORTH-2021", [person], task="collect")
    assert len(ledger.read("artists")) == 1
    assert _rules(ledger) == [("NORTH-2019", "first"), ("NORTH-2021", "A1")]
    assert ledger.read("review_queue") == []


def test_a2_agreed_english_names_join_across_programmes(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.apply_roster("NORTH-2019", [_row("김하늘", "Haneul Kim")], task="collect")
    ledger.apply_roster("SOUTH-2021", [_row("김하늘", "Kim Haneul")], task="collect")
    assert len(ledger.read("artists")) == 1
    assert _rules(ledger) == [("NORTH-2019", "first"), ("SOUTH-2021", "A2")]


def test_a3_a_non_personal_name_reuses_the_first_row(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.apply_roster("NORTH-2019", [_row("노을 스튜디오", "Noeul Studio")], task="collect")
    ledger.apply_roster("SOUTH-2021", [_row("노을 스튜디오", "")], task="collect")
    assert len(ledger.read("artists")) == 1
    assert _rules(ledger)[1][1] == "A3"


def test_a4_a_row_with_no_membership_takes_the_first_roster(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write(
        "artists",
        [
            empty_row(
                ARTISTS_FIELDS,
                ledger_id="LED-waiting",
                gy_id="GY-000009",
                name_ko="김하늘",
                name_en="",
                status="STAGED",
            )
        ],
        task="seed",
    )
    ledger.apply_roster("NORTH-2019", [_row("김하늘")], task="collect")
    assert [row["ledger_id"] for row in ledger.read("artists")] == ["LED-waiting"]
    assert _rules(ledger) == [("NORTH-2019", "A4")]


def test_a5_identity_key_joins_and_a_miss_does_not_fall_through(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.apply_roster(
        "NORTH-2019",
        [_row("김하늘", identity="demo:one")],
        task="collect",
    )
    ledger.apply_roster(
        "NORTH-2021",
        [_row("김하늘", "Haneul Kim", identity="demo:one")],
        task="collect",
    )
    ledger.apply_roster(
        "SOUTH-2022",
        [_row("김하늘", "Haneul Kim", identity="demo:two", website="https://haneul.example.org")],
        task="collect",
    )
    artists = ledger.read("artists")
    assert len(artists) == 2
    rules = {code: rule for code, rule in _rules(ledger)}
    assert rules["NORTH-2019"] == "first"
    assert rules["NORTH-2021"] == "A5"
    assert rules["SOUTH-2022"] == "first"
    # The miss does not use the English name or the website. The same Hangul name is queued.
    queued = [row for row in ledger.read("review_queue") if row["status"] == "open"]
    assert queued and queued[0]["reason"] == "possible_same_person"
    assert "SOUTH-2022" in queued[0]["detail"]


def test_a6_a_unique_website_joins_a_different_spelling(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.apply_roster(
        "NORTH-2019",
        [_row("루멘 랩", website="https://lumen.example.org")],
        task="collect",
    )
    ledger.apply_roster(
        "SOUTH-2021",
        [_row("Lumen Lab", website="https://www.lumen.example.org/index")],
        task="collect",
    )
    assert len(ledger.read("artists")) == 1
    assert _rules(ledger)[1][1] == "A6"


def test_same_personal_name_on_another_programme_does_not_attach(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.apply_roster("NORTH-2019", [_row("김하늘")], task="collect")
    ledger.apply_roster("SOUTH-2021", [_row("김하늘")], task="collect")
    assert len(ledger.read("artists")) == 2
    assert [rule for _code, rule in _rules(ledger)] == ["first", "first"]
    queued = ledger.read("review_queue")
    assert len(queued) == 1
    assert queued[0]["reason"] == "possible_same_person"
    assert queued[0]["status"] == "open"
    assert "SOUTH-2021" in queued[0]["detail"]
    assert "LED-" in queued[0]["detail"]
