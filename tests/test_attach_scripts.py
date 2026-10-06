# SPDX-License-Identifier: AGPL-3.0-only
"""Attachment across scripts: accented Latin names, spaced Hangul, contradicting names.

People are fictitious. URLs are example.org. Each case applies roster rows to a
fresh ledger and reads the membership rules and the review queue.
"""

from __future__ import annotations

from pathlib import Path

from giye.config import load
from giye.ledger.ledger import Ledger
from giye.normalize.language import default_language
from giye.resolve.attach import latin_tokens, name_keys

ROOT = Path(__file__).resolve().parents[1]
SITE = "https://duo.example.org/"


def _ledger(tmp_path: Path) -> Ledger:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Script fixtures"
id_prefix = "GY"
[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{(ROOT / "examples" / "demo" / "frames.yml").as_posix()}"
""",
        encoding="utf-8",
    )
    return Ledger.open(load(path))


def _row(name_ko: str, name_en: str = "", **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "name_ko": name_ko,
        "name_en": name_en,
        "source_url": "https://example.org/roster",
        "collected_at": "2026-01-15",
    }
    row.update(extra)
    return row


def _rules(ledger: Ledger) -> list[str]:
    return [row["attach_rule"] for row in ledger.read("frame_membership")]


def _two_programmes(tmp_path: Path, first: dict, second: dict) -> Ledger:
    ledger = _ledger(tmp_path)
    ledger.apply_roster("NORTH-2019", [first], task="collect")
    ledger.apply_roster("SOUTH-2021", [second], task="collect")
    return ledger


# --- Latin script beyond A–Z -------------------------------------------------


def test_accented_latin_name_is_a_latin_only_personal_name() -> None:
    language = default_language()
    for name in ("José García", "Zoë Müller", "Đặng Thị Lan", "Søren Kjær", "Łukasz Nowak"):
        assert language.personal_name(name), name


def test_accented_latin_names_are_not_joined_across_programmes(tmp_path: Path) -> None:
    for name in ("José García", "Zoë Müller", "Đặng Thị Lan", "Søren Kjær"):
        ledger = _two_programmes(tmp_path / name.replace(" ", "_"), _row("", name), _row("", name))
        assert len(ledger.read("artists")) == 2, name
        assert _rules(ledger) == ["first", "first"], name
        [item] = ledger.read("review_queue")
        assert item["detail"].endswith("(latin name only)")


def test_name_keys_keep_accented_letters_apart() -> None:
    assert latin_tokens("José García") == ["jose", "garcia"]
    assert name_keys("", "José García", "") != name_keys("", "José Garcés", "")
    assert name_keys("", "Łukasz Nowak", "") != name_keys("", "Tukasz Nowak", "")
    assert name_keys("", "Ana Núñez", "") != name_keys("", "Ana Nú", "")


def test_name_keys_fold_accents_so_one_spelling_meets_its_plain_form() -> None:
    # Folding is deliberate: a roster in English often drops the accents of the
    # same name. Folding removes marks only; it never drops a letter.
    assert name_keys("", "José García", "") == name_keys("", "Jose Garcia", "")
    assert name_keys("", "Søren Kjær", "") == name_keys("", "Soren Kjaer", "")


def test_different_accented_names_do_not_meet(tmp_path: Path) -> None:
    for left, right in (("José García", "José Garcés"), ("Zoë Müller", "Zoë Mürz"), ("Łukasz Nowak", "Tukasz Nowak")):
        ledger = _two_programmes(tmp_path / left.replace(" ", "_"), _row("", left), _row("", right))
        artists = ledger.read("artists")
        assert len(artists) == 2, (left, right)
        assert all(not row["aliases"] for row in artists)


def test_accented_name_joins_its_plain_spelling_within_a_series(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.apply_roster("NORTH-2019", [_row("", "José García")], task="collect")
    ledger.apply_roster("NORTH-2021", [_row("", "Jose Garcia")], task="collect")
    assert len(ledger.read("artists")) == 1
    assert _rules(ledger) == ["first", "A1"]


def test_shared_website_does_not_join_two_accented_latin_names(tmp_path: Path) -> None:
    ledger = _two_programmes(
        tmp_path, _row("", "José García", websites=[SITE]), _row("", "Zoë Müller", websites=[SITE])
    )
    assert len(ledger.read("artists")) == 2
    assert _rules(ledger) == ["first", "first"]


# --- Hangul names outside the unspaced surname shape, and the group branch ---


def test_spaced_and_compound_surname_hangul_names_are_personal() -> None:
    language = default_language()
    for name in ("김 하늘", "박 서연", "독고영재", "독고 영재", "남궁민수", "선우정아", "알렉스 리", "마리아 김"):
        assert language.personal_name(name), name


def test_spaced_hangul_name_is_not_joined_to_the_unspaced_one_on_another_programme(tmp_path: Path) -> None:
    for left, right in (("김하늘", "김 하늘"), ("김 하늘", "김 하늘")):
        ledger = _two_programmes(tmp_path / f"{left}-{right}".replace(" ", "_"), _row(left), _row(right))
        assert len(ledger.read("artists")) == 2, (left, right)
        assert _rules(ledger) == ["first", "first"]
        assert len(ledger.read("review_queue")) == 1


def test_names_not_recognised_as_groups_do_not_take_a3(tmp_path: Path) -> None:
    for name_ko, name_en in (("독고영재", ""), ("알렉스 리", ""), ("山田太郎", ""), ("", "Ana Maria de la Cruz")):
        ledger = _two_programmes(
            tmp_path / (name_ko or name_en).replace(" ", "_"), _row(name_ko, name_en), _row(name_ko, name_en)
        )
        assert len(ledger.read("artists")) == 2, name_ko or name_en
        assert "A3" not in _rules(ledger)
        assert len(ledger.read("review_queue")) == 1


def test_a3_still_joins_a_name_with_a_team_word(tmp_path: Path) -> None:
    ledger = _two_programmes(tmp_path, _row("빛소리 콜렉티브"), _row("빛소리 콜렉티브"))
    assert len(ledger.read("artists")) == 1
    assert _rules(ledger) == ["first", "A3"]


def test_a3_joins_a_stored_team_row_with_the_same_name(tmp_path: Path) -> None:
    ledger = _two_programmes(
        tmp_path, _row("빛과소리", reviewer_note="members=김하늘|박서연"), _row("빛과소리")
    )
    assert len(ledger.read("artists")) == 1
    assert _rules(ledger) == ["first", "A3"]


def test_shared_website_needs_meeting_names_unless_a_side_is_a_group(tmp_path: Path) -> None:
    for left, right in (
        (_row("김 하늘"), _row("박 서연")),
        (_row("알렉스 리"), _row("마리아 김")),
        (_row("독고영재"), _row("", "Haneul Kim")),
        (_row("山田太郎"), _row("", "Haneul Kim")),
        (_row("", "Ana Maria de la Cruz"), _row("", "Haneul Kim")),
    ):
        left["websites"] = [SITE]
        right["websites"] = [SITE]
        ledger = _two_programmes(tmp_path / str(left["name_ko"] or left["name_en"]).replace(" ", "_"), left, right)
        assert _rules(ledger) == ["first", "first"], (left, right)
    ledger = _two_programmes(
        tmp_path / "group", _row("루멘 랩", websites=[SITE]), _row("", "Lumen Lab", websites=[SITE])
    )
    assert _rules(ledger) == ["first", "A6"]


def test_hangul_romanisation_keys_ignore_spaces() -> None:
    language = default_language()
    assert language.name_keys("김 하늘") == language.name_keys("김하늘") != set()


# --- A shared English name does not override differing Hangul names ----------


def test_a2_does_not_join_two_different_hangul_names_with_one_english_name(tmp_path: Path) -> None:
    ledger = _two_programmes(tmp_path, _row("윤서정", "Seojung Yoon"), _row("윤서중", "Seojung Yoon"))
    artists = ledger.read("artists")
    assert sorted(row["name_ko"] for row in artists) == ["윤서정", "윤서중"]
    assert all(not row["aliases"] for row in artists)
    [item] = ledger.read("review_queue")
    assert item["detail"].endswith("(hangul names differ)")


def test_a1_does_not_join_two_different_hangul_names_in_one_series(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.apply_roster("NORTH-2019", [_row("이하늘", "Haneul Lee")], task="collect")
    ledger.apply_roster("NORTH-2021", [_row("리하늘", "Haneul Lee")], task="collect")
    assert len(ledger.read("artists")) == 2
    assert _rules(ledger) == ["first", "first"]


def test_a1_still_joins_a_stored_hangul_alias(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.apply_roster("NORTH-2019", [_row("김하늘", "Haneul Kim", aliases="김하눌")], task="collect")
    ledger.apply_roster("NORTH-2021", [_row("김하눌", "Haneul Kim")], task="collect")
    assert len(ledger.read("artists")) == 1
    assert _rules(ledger) == ["first", "A1"]


def test_a6_does_not_join_two_different_hangul_names_with_one_english_name(tmp_path: Path) -> None:
    ledger = _two_programmes(
        tmp_path,
        _row("윤서정", "Seojung Yoon", websites=[SITE]),
        _row("윤서중", "Seojung Yoon", websites=[SITE]),
    )
    assert len(ledger.read("artists")) == 2
    assert _rules(ledger) == ["first", "first"]


# --- Two lines of one edition are two people ---------------------------------


def test_two_identical_lines_of_one_edition_stay_two_records_and_are_queued(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    roster = [_row("김지우", "Jiwoo Kim"), _row("김지우", "Jiwoo Kim")]
    ledger.apply_roster("NORTH-2019", roster, task="collect")
    assert len(ledger.read("artists")) == 2
    assert _rules(ledger) == ["first", "first"]
    [item] = ledger.read("review_queue")
    assert item["detail"].endswith("(same edition)")
    # A re-run puts each line back on its own record and queues nothing new.
    ledger.apply_roster("NORTH-2019", roster, task="collect")
    assert len(ledger.read("artists")) == 2
    assert len(ledger.read("frame_membership")) == 2
    assert len(ledger.read("review_queue")) == 1


def test_a_second_line_of_one_edition_is_not_swallowed_as_an_alias(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.apply_roster("NORTH-2019", [_row("", "Jonas Berg"), _row("", "Jonas  Berg")], task="collect")
    artists = ledger.read("artists")
    assert len(artists) == 2
    assert len(ledger.read("frame_membership")) == 2


def test_a_later_edition_still_joins_by_a1(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    roster = [_row("김지우", "Jiwoo Kim"), _row("박서연", "Seoyeon Park")]
    ledger.apply_roster("NORTH-2019", roster, task="collect")
    ledger.apply_roster("NORTH-2021", roster, task="collect")
    assert len(ledger.read("artists")) == 2
    assert _rules(ledger) == ["first", "first", "A1", "A1"]


# --- Team words match whole Latin words ---------------------------------------


def test_latin_team_words_match_whole_words_only() -> None:
    from giye.field import shipped_field
    from giye.resolve.teams import team_like

    words = shipped_field().compiled_team_words()
    for name in ("Mina Groupe", "Sora Crewes", "Seo Projectionist", "Ari Movementova", "Jun Studiola"):
        assert team_like({"name_ko": name}, words=words) == "", name
    for name in ("Lumen Lab", "Noeul Studios", "Night Project", "Blue Crew", "Sea Collective", "Wave Ensemble"):
        assert team_like({"name_ko": name}, words=words) == "team_name", name


def test_latin_team_words_still_match_glued_capitals() -> None:
    from giye.field import shipped_field
    from giye.resolve.teams import team_like

    words = shipped_field().compiled_team_words()
    for name in ("NoeulCollectiveB", "BADACOLLECTIVE", "NOEULLABS", "BD_collective"):
        assert team_like({"name_ko": name}, words=words) == "team_name", name
