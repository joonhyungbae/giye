# SPDX-License-Identifier: MIT
"""Institution rules V1–V9, the audit, and the language-module interface.

People in the surrounding ledger tests are fictitious. Institution names here are
public places (서울시립미술관, ZKM) or invented labels (ACC, 하얀집). No network.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import pytest

from giye.normalize.gazetteer import Gazetteer
from giye.normalize.language import KoreanEnglish, LanguageModule, load_glossary
from giye.normalize.rules import venue_place
from giye.normalize.venue_names import hangul_bags, skeleton
from giye.normalize.venues import NAME_RULES, UnionFind, _name_rule_merges, build, institution_key
from giye.resolve.names import hangul_name_keys

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "normalize" / "activities.csv"


def _load(path: Path = FIXTURE) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _ids(rows: list[dict], result) -> dict[str, str]:
    by_activity = {row["activity_id"]: row["venue"] for row in rows}
    return {by_activity[activity_id]: annotation["venue_id"] for activity_id, annotation in result.annotations.items()}


def _row(activity_id: str, venue: str, ledger_id: str = "p1") -> dict:
    return {
        "activity_id": activity_id,
        "ledger_id": ledger_id,
        "venue": venue,
        "title": "Show",
        "year": "2020",
        "activity_type": "group_exhibition",
        "publishable": "yes",
        "origin": "",
    }


def test_v1_place_is_the_whole_fragment() -> None:
    gazetteer = KoreanEnglish.load().gazetteer
    assert venue_place("Nam June Paik Art Center, Yongin", gazetteer) == ("KR", "경기")
    assert venue_place("Nam June Paik Art Center", gazetteer) == ("", "")
    # G1 reads a country code. G3 lets a following abbreviation inherit the city.
    # G6 reads a US postal code that is not itself a country.
    assert gazetteer.resolve_fragments(["KR"])[0][1] == "KR"
    assert gazetteer.resolve_fragments(["CA"])[0][1] == "CA"
    los_angeles = gazetteer.resolve_fragments(["Los Angeles", "CA"])
    assert los_angeles[0][1] == "US"
    assert los_angeles[1][1] == "US"
    assert gazetteer.resolve_fragments(["NY"])[0][1] == "US"


def test_v2_splits_on_separators_and_parentheses() -> None:
    # FBC is not an ISO country code. NG would be read as Nigeria (G1) and would not be an institution.
    result = build(
        [_row("a", "Foo Bar Center (FBC), London", "p1"), _row("b", "Foo Bar Center (FBC)", "p2")],
        write=False,
    )
    # Two artists stating the same parenthetical pair: V5d joins the acronym to the name.
    assert result.annotations["a"]["venue_id"] == result.annotations["b"]["venue_id"]
    assert result.annotations["a"]["venue_kind"] == "institution"
    assert any(rule == "V5d" for rule, _left, _right in result.merges)
    london = next(row for row in result.venues if row["venue_id"] == result.annotations["a"]["venue_id"])
    assert london["country"] == "GB"


def test_v7_strips_title_qualifier_edition_and_spaces() -> None:
    assert institution_key("서울시립미술관 《빛》") == institution_key("서울시립미술관")
    assert institution_key("서울시립미술관 외") == institution_key("서울시립미술관")
    assert institution_key("제12회 광주비엔날레") == institution_key("광주비엔날레")
    assert institution_key("탈영역 우정국") == institution_key("탈영역우정국")
    bare = build([_row("a", "서울시립미술관"), _row("b", "서울시립미술관 외")], write=False)
    assert bare.annotations["a"]["venue_id"] == bare.annotations["b"]["venue_id"]
    ablated = build(
        [_row("a", "서울시립미술관"), _row("b", "서울시립미술관 외")],
        name_rules=frozenset(),
        write=False,
    )
    assert ablated.annotations["a"]["venue_id"] != ablated.annotations["b"]["venue_id"]


def test_demo_v7_v8_v9_and_false_merges(tmp_path: Path) -> None:
    rows = _load()
    result = build(rows, tmp_path, write=True)
    ids = _ids(rows, result)
    # V7 strips the title and the qualifier; V9 joins the English name.
    assert ids["서울시립미술관 《빛》"] == ids["서울시립미술관 외"] == ids["Seoul Museum of Art"]
    # V8: a building part, and an acronym plus the city its rows already name.
    assert ids["국립현대미술관 서울관"] == ids["국립현대미술관"]
    assert ids["ZKM Karlsruhe"] == ids["ZKM, Karlsruhe"]
    # V7e folds centre/center. V9 joins the Hangul name and the other word order.
    nabi = ids["Art Center Nabi"]
    assert ids["Art Centre Nabi"] == nabi == ids["아트센터나비"] == ids["Nabi Art Center"]
    assert any(rule == "V7e" for rule, _a, _b in result.merges)
    assert any(rule == "V8" for rule, _a, _b in result.merges)
    assert any(rule == "V9" for rule, _a, _b in result.merges)
    # These pairs must not merge. The skeleton would equate the acronyms; V7e does not use it.
    assert skeleton("acc") == skeleton("aas")
    assert skeleton("bug") == skeleton("book")
    assert skeleton("mmca") == skeleton("mca")
    apart = [
        ("ACC", "AAS"),
        ("BUG", "Book"),
        ("MMCA", "MCA"),
        ("New York University", "City University of New York"),
        ("Museum of Modern Art", "Modern Art Museum"),
        ("국립현대미술관", "서울시립미술관 《빛》"),
    ]
    for left, right in apart:
        assert ids[left] != ids[right], (left, right)
    # 현대 = contemporary before modern, so only the first reading that matches is used.
    assert ids["현대나비"] == ids["Contemporary Nabi"]
    assert ids["현대나비"] != ids["Modern Nabi"]
    audit = (tmp_path / "venue_audit.md").read_text(encoding="utf-8")
    section = audit.split("## 7. Merges")[1]
    bullets = [line for line in section.splitlines() if line.startswith("- ") and not line.startswith("- none")]
    assert len(bullets) == len(result.merges)
    assert all(line.split()[1] in {"V5a", "V5d", "V5f", "V7e", "V8", "V9"} for line in bullets)


def test_v5e_blocks_two_hangul_names_and_v5f_allows_one_artist() -> None:
    blocked = build(
        [
            _row("a1", "가나미술관 (Alpha Museum)", "p1"),
            _row("a2", "가나미술관 (Alpha Museum)", "p2"),
            _row("b1", "다라미술관 (Alpha Museum)", "p1"),
            _row("b2", "다라미술관 (Alpha Museum)", "p2"),
        ],
        write=False,
    )
    # The activity's venue_id is its first institution. The two Hangul names stay apart (V5e).
    assert blocked.annotations["a1"]["venue_id"] != blocked.annotations["b1"]["venue_id"]
    assert blocked.stats["blocked_alias_components"] >= 1
    assert not any(rule in {"V5a", "V5d"} for rule, _a, _b in blocked.merges)

    single = build([_row("s", "하얀집 (Blanc House)", "p1")], write=False)
    assert any(rule == "V5f" for rule, _a, _b in single.merges)
    assert len(single.venues) == 1


def test_v9_two_readings_in_one_component_are_not_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    """One reading per component. Two bags on one connected group are ambiguous."""

    def bags(key: str, lang: object) -> tuple:
        return {"h-one": (("~aaa",),), "h-two": (("~bbb",),)}.get(key, ())

    def latin(key: str, lang: object, cross_script: bool = False):
        if not cross_script:
            return None
        return {"l-one": ("~aaa",), "l-two": ("~bbb",)}.get(key)

    monkeypatch.setattr("giye.normalize.venue_names.hangul_bags", bags)
    monkeypatch.setattr("giye.normalize.venue_names.latin_bag", latin)
    keys = {"h-one", "h-two", "l-one", "l-two"}
    union = UnionFind(keys)
    union.union("l-one", "l-two")
    merges = _name_rule_merges(union, keys, defaultdict(dict), [], KoreanEnglish.load(), NAME_RULES)
    assert not any(rule == "V9" for rule, _left, _right in merges)


def test_language_module_loads_files_and_a_toy_pair(tmp_path: Path) -> None:
    korean = KoreanEnglish.load()
    assert isinstance(korean, LanguageModule)
    assert korean.name == "ko-en"
    assert korean.name_keys("김하늘") == hangul_name_keys("김하늘")
    assert korean.name_keys("Haneul Kim")
    assert korean.romanise("나비") == "nabi"
    assert "미술관" in korean.glossary
    assert korean.glossary["시립"] == ((),)
    bags = hangul_bags("서울시립미술관", korean)
    assert bags and bags[0] == hangul_bags("서울시립미술관", korean)[0]
    assert any("art" in bag and "museum" in bag for bag in bags)

    glossary_path = tmp_path / "glossary.yaml"
    glossary_path.write_text("qx:\n- [kwa]\n", encoding="utf-8")
    glossary = load_glossary(glossary_path)
    gazetteer = Gazetteer.from_records(
        cities=[("Qxville", 1000, "QQ", "01", "Qxville")],
        countries={"qqland": "QQ"},
        country_codes={"QQ"},
        alpha3={"QQL": "QQ"},
        admin1={},
        us_postal=set(),
    )

    class ToyLanguage:
        name = "toy-qx"

        def __init__(self) -> None:
            self._glossary = glossary
            self._gazetteer = gazetteer

        @property
        def glossary(self):
            return self._glossary

        @property
        def gazetteer(self):
            return self._gazetteer

        def name_keys(self, name: str) -> set[str]:
            return {name.casefold()} if name else set()

        def romanise(self, token: str) -> str:
            return token.casefold().replace("q", "k")

    toy = ToyLanguage()
    assert isinstance(toy, LanguageModule)
    assert toy.glossary["qx"] == (("kwa",),)
    assert toy.romanise("Qart") == "kart"
    assert toy.name_keys("Qx") == {"qx"}
    assert toy.gazetteer.resolve("Qxville") == [("QQ", "")]
    # The merge reads the glossary file. Without 미술관 the Seoul pair does not join.
    stripped = tmp_path / "no-museum.yaml"
    stripped.write_text("센터:\n- [center]\n", encoding="utf-8")
    altered = KoreanEnglish.load(glossary=stripped)
    result = build(
        [_row("a", "서울시립미술관"), _row("b", "Seoul Museum of Art", "p2")],
        write=False,
        lang=altered,
    )
    assert result.annotations["a"]["venue_id"] != result.annotations["b"]["venue_id"]


def test_glossary_and_gazetteer_have_no_person_names() -> None:
    data = Path(__file__).resolve().parents[1] / "src" / "giye" / "normalize" / "data" / "ko_en"
    names = ("glossary.yaml", "cities.tsv", "admin1.tsv", "us_postal.txt")
    text = "\n".join((data / name).read_text(encoding="utf-8") for name in names)
    for needle in ("김하늘", "Haneul", "example.org", "@", "http://", "https://"):
        assert needle not in text
    korean = KoreanEnglish.load()
    # The production glossary, and nothing else. These are institution words.
    assert set(korean.glossary) == {
        "아트센터", "예술센터", "문화센터", "미술관", "박물관", "뮤지엄", "갤러리", "화랑",
        "스페이스", "공간", "플랫폼", "아트", "예술", "미술", "센터", "랩", "연구소",
        "팩토리", "공장", "극장", "스튜디오", "창작스튜디오", "레지던시", "문화", "재단",
        "대학교", "대학", "미디어", "코리아", "한국", "국제", "페스티벌", "축제", "비엔날레",
        "역", "현대", "국립", "홀", "하우스", "프로젝트", "디자인", "광장", "도서관",
        "아카이브", "크리에이티브", "시립", "도립", "군립", "구립",
    }


def test_missing_geonames_tree_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Gazetteer.from_geonames(tmp_path)
