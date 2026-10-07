# SPDX-License-Identifier: AGPL-3.0-only
"""Country and region fill (G7–G11). Institutions are fictitious. No person is named."""

from __future__ import annotations

from pathlib import Path

from giye.ledger.io import read_csv, write_csv
from giye.ledger.schemas import ACTIVITIES_FIELDS, ARTISTS_FIELDS, empty_row
from giye.normalize.language import KoreanEnglish
from giye.normalize.service import normalize
from giye.normalize.venues import build

LANG = KoreanEnglish.load()


def _row(index: int, venue: str, origin: str = "") -> dict[str, str]:
    return {
        "activity_id": f"a{index:03d}",
        "ledger_id": "p0",
        "venue": venue,
        "title": "Show",
        "year": "2020",
        "activity_type": "group_exhibition",
        "publishable": "yes",
        "origin": origin,
    }


def _venues(rows: list[dict[str, str]], frames: list[tuple[str, str, str]] | None = None) -> dict[str, dict]:
    result = build(rows, write=False, lang=LANG, frames=frames)
    return {row["name"]: row for row in result.venues}


def _rules(row: dict) -> set[str]:
    return {part for part in (row.get("rules") or "").split("|") if part}


def test_g7_stem_marker_and_bare_national_marker() -> None:
    """A place stem and 한국 fill KR. Bare 국립 does not."""
    found = _venues(
        [
            _row(1, "부산예시미술관"),
            _row(2, "한국예시학회"),
            _row(3, "대한민국예시관"),
            _row(4, "국립예시현대관"),
        ]
    )
    busan = found["부산예시미술관"]
    assert busan["country"] == "KR"
    assert busan["kr_region"] == "부산"
    assert {"G7", "G11"} <= _rules(busan)
    assert found["한국예시학회"]["country"] == "KR"
    assert found["한국예시학회"]["kr_region"] == ""
    assert "G7" in _rules(found["한국예시학회"])
    assert "G11" not in _rules(found["한국예시학회"])
    assert found["대한민국예시관"]["country"] == "KR"
    assert found["대한민국예시관"]["kr_region"] == ""
    assert found["국립예시현대관"]["country"] == ""
    assert "G7" not in _rules(found["국립예시현대관"])


def test_g7_blocks_conjunction_tour_and_disagreed_stem() -> None:
    """A conjunction, a hall word, and a stem of two regions do not invent a region or a country."""
    found = _venues(
        [
            _row(1, "한국 & 일본예시"),
            _row(2, "서울 순회 예시전"),
            _row(3, "광주예시과학관"),
            _row(4, "사천성예시미술관"),
        ]
    )
    assert found["한국 & 일본예시"]["country"] == ""
    assert found["서울 순회 예시전"]["country"] == ""
    gwangju = found["광주예시과학관"]
    assert gwangju["country"] == "KR"
    assert gwangju["kr_region"] == ""
    assert "G11" not in _rules(gwangju)
    assert found["사천성예시미술관"]["country"] == ""


def test_g7_hall_guard_blanks_a_fold_stored_halls_disagree_with() -> None:
    """세종 is 기타 in the place table and 서울 on the stored hall, so the region stays blank."""
    found = _venues(
        [
            _row(1, "세종예시회관, 서울, 한국"),
            _row(2, "세종예시소극장"),
            _row(3, "금산예시관, 서울, 한국"),
            _row(4, "금산예시갤러리"),
        ]
    )
    assert found["세종예시회관"]["country"] == "KR"
    assert found["세종예시회관"]["kr_region"] == "서울"
    assert "G7" not in _rules(found["세종예시회관"])
    small = found["세종예시소극장"]
    assert small["country"] == "KR"
    assert small["kr_region"] == ""
    assert "G7" in _rules(small)
    assert "G11" not in _rules(small)
    gallery = found["금산예시갤러리"]
    assert gallery["country"] == "KR"
    assert gallery["kr_region"] == ""


def test_stored_country_is_not_overwritten_by_a_place_prefix() -> None:
    """서울예시미술관 in Japan keeps JP. The prefix does not replace a country the row stated."""
    found = _venues([_row(1, "서울예시미술관, 일본")])
    row = found["서울예시미술관"]
    assert row["country"] == "JP"
    assert "G7" not in _rules(row)
    assert "G9" not in _rules(row)


def test_g8_is_equality_or_a_long_prefix_not_a_short_token() -> None:
    """Five syllables is the floor. A short token inside a frame name is not a fill."""
    frames = [("EX", "예시문화회관특별전", "Example Culture Hall")]
    found = _venues(
        [
            _row(1, "예시문화회관특별전"),
            _row(2, "예시문화회"),
            _row(3, "예시문화"),
            _row(4, "회관"),
        ],
        frames,
    )
    assert found["예시문화회관특별전"]["country"] == "KR"
    assert "G8" in _rules(found["예시문화회관특별전"])
    assert found["예시문화회"]["country"] == "KR"
    assert "G8" in _rules(found["예시문화회"])
    assert found["예시문화"]["country"] == ""
    assert found["회관"]["country"] == ""


def test_g9_unanimous_placed_kr_has_a_floor_and_a_second_country_abstains() -> None:
    """Ten unanimous KR rows fill. Nine do not. One foreign row blocks a ten-vote majority."""
    under = [_row(index, "무명예시랩, 서울") for index in range(6)]
    under += [_row(index, "무명예시랩") for index in range(6, 13)]
    assert _venues(under)["무명예시랩"]["country"] == ""

    enough = [_row(index, "무명예시랩, 서울") for index in range(10)]
    enough += [_row(index, "무명예시랩") for index in range(10, 21)]
    filled = _venues(enough)["무명예시랩"]
    assert filled["country"] == "KR"
    assert filled["kr_region"] == "서울"
    assert {"G9", "G11"} <= _rules(filled)

    mixed = [_row(index, "다른예시랩, 서울") for index in range(10)]
    mixed.append(_row(10, "다른예시랩, 일본"))
    mixed += [_row(index, "다른예시랩") for index in range(11, 23)]
    assert _venues(mixed)["다른예시랩"]["country"] == ""


def test_g9_attaches_a_place_run_to_the_neighbour_and_abstains_on_two_countries() -> None:
    """A foreign first place does not stick to the next institution. A two-country parenthesis is cleared."""
    found = _venues(
        [
            _row(1, "멕시코예시관 (멕시코) / 서울예시페스티벌 (한국)"),
            _row(2, "예시순회전(이탈리아, 프랑스)"),
        ]
    )
    assert found["멕시코예시관"]["country"] == "MX"
    festival = found["서울예시페스티벌"]
    assert festival["country"] == "KR"
    assert "G9" in _rules(festival)
    assert "G7" not in _rules(festival)
    touring = found["예시순회전"]
    assert touring["country"] == ""
    assert touring["city"] == ""
    assert "G9" in _rules(touring)
    assert "G7" not in _rules(touring)


def test_g10_parenthetical_guest_copies_a_unanimous_host() -> None:
    """Five rows inside one host are enough. The host's blank region stays blank."""
    frames = [("EX", "예시문화회관", "Example Culture Hall")]
    rows = [_row(index, "예시문화회관 (예시게스트센터)") for index in range(5)]
    found = _venues(rows, frames)
    assert found["예시문화회관"]["country"] == "KR"
    assert "G8" in _rules(found["예시문화회관"])
    guest = found["예시게스트센터"]
    assert guest["country"] == "KR"
    assert guest["kr_region"] == ""
    assert "G10" in _rules(guest)
    assert "G11" not in _rules(guest)


def test_cv_row_keeps_a_stored_country_and_does_not_copy_an_entity(tmp_path: Path) -> None:
    """V1 wins. An empty CV row is filled from the rule, not from a neighbour's stored country."""
    ledger = tmp_path / "data" / "ledger"
    write_csv(
        path=ledger / "artists.csv",
        fields=ARTISTS_FIELDS,
        rows=[empty_row(ARTISTS_FIELDS, ledger_id="p0", name_en="Example Person")],
    )
    rows = [
        _row(1, "서울예시미술관", origin="cv:s1"),
        _row(2, "서울다른미술관, 일본", origin="cv:s1"),
        _row(3, "멕시코예시관 (멕시코) / 서울예시페스티벌 (한국)", origin="cv:s1"),
    ]
    # Distinct people are not required. Distinct activity ids are.
    rows[1]["activity_id"] = "a002"
    rows[2]["activity_id"] = "a003"
    write_csv(
        path=ledger / "activities.csv",
        fields=ACTIVITIES_FIELDS,
        rows=[empty_row(ACTIVITIES_FIELDS, **row) for row in rows],
    )
    config = tmp_path / "giye.toml"
    config.write_text(
        f"""
[archive]
name = "Synthetic media-art field"
id_prefix = "GY"
territory = "KR"
languages = ["ko", "en"]

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "frames.yml"
""",
        encoding="utf-8",
    )
    from giye.config import load

    normalize(load(config))
    activities = {row["activity_id"]: row for row in read_csv(tmp_path / "data" / "processed" / "activities.csv")}
    filled = activities["a001"]
    assert filled["venue_country"] == "KR"
    assert filled["venue_region"] == "서울"
    assert "G7" in filled["rules"]
    assert "G7" in filled["venue_rule"]
    assert "G11" in filled["rules"]
    kept = activities["a002"]
    assert kept["venue_country"] == "JP"
    assert "G7" not in kept["rules"]
    assert "G9" not in kept["rules"]
    leaked = activities["a003"]
    assert leaked["venue_country"] == ""
    assert leaked["venue_region"] == ""
