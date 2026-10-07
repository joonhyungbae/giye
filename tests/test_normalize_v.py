# SPDX-License-Identifier: AGPL-3.0-only
"""Institution rules V1–V9, the audit, and the language-module interface.

People in the surrounding ledger tests are fictitious. Institution names here are
public places (서울시립미술관, ZKM) or invented labels (ACC, 하얀집). No network.
"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from pathlib import Path

import pytest

from giye.normalize.gazetteer import Gazetteer
from giye.normalize.language import KoreanEnglish, LanguageModule, VenueWords, load_glossary
from giye.normalize.rules import venue_place
from giye.normalize.venue_names import hangul_bags, hangul_part_parent, latin_part_parent, skeleton
from giye.normalize.venues import NAME_RULES, UnionFind, _name_rule_merges, build, institution_key
from giye.resolve.names import hangul_name_keys

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "normalize" / "activities.csv"
PLACE_FIXTURE = ROOT / "tests" / "fixtures" / "normalize" / "place_names.tsv"
PACKAGED_CITIES = ROOT / "src" / "giye" / "normalize" / "data" / "ko_en" / "cities.tsv"


def _load(path: Path = FIXTURE) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _ids(rows: list[dict], result) -> dict[str, str]:
    by_activity = {row["activity_id"]: row["venue"] for row in rows}
    return {by_activity[activity_id]: annotation["venue_id"] for activity_id, annotation in result.annotations.items()}


def _language_with_fixture_places(tmp_path: Path) -> KoreanEnglish:
    """Packaged cities plus the place names that live only in the test fixture."""
    lines = [line for line in PACKAGED_CITIES.read_text(encoding="utf-8").splitlines() if line.strip()]
    for line in PLACE_FIXTURE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith(("#", "name\t")):
            continue
        lines.append(line)
    dest = tmp_path / "cities.tsv"
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return KoreanEnglish.load(cities=dest)


def _geoname_row(
    geoname_id: str,
    name: str,
    ascii_name: str,
    alternates: str,
    feature_class: str,
    feature_code: str,
    admin1: str,
    population: int,
) -> str:
    """One GeoNames dump row. Empty columns stay empty; the loader reads by index."""
    columns = [""] * 19
    columns[0] = geoname_id
    columns[1] = name
    columns[2] = ascii_name
    columns[3] = alternates
    columns[4] = "0"
    columns[5] = "0"
    columns[6] = feature_class
    columns[7] = feature_code
    columns[8] = "KR"
    columns[10] = admin1
    columns[14] = str(population)
    columns[17] = "Asia/Seoul"
    columns[18] = "2020-01-01"
    return "\t".join(columns)


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
    # V8: 서울관 is a branch, so it stays apart. ZKM Karlsruhe is the acronym's only city.
    assert ids["국립현대미술관 서울관"] != ids["국립현대미술관"]
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
    # Section 8 is the country fill. The merge bullets are only section 7.
    section = audit.split("## 7. Merges")[1].split("\n## ")[0]
    bullets = [line for line in section.splitlines() if line.startswith("- ") and not line.startswith("- none")]
    assert len(bullets) == len(result.merges)
    assert all(line.split()[1] in {"V5a", "V5d", "V5f", "V7e", "V8", "V9", "V4n", "V7f", "V9u"} for line in bullets)


def test_v3c_place_v8_branch_and_v8b_office(tmp_path: Path) -> None:
    """Branches, offices, and bare places stay apart from the institution they were joined to."""
    lang = _language_with_fixture_places(tmp_path)
    assert hangul_part_parent("국립현대미술관전시실", lang) == "국립현대미술관"
    assert hangul_part_parent("국립현대미술관창고동", lang) == "국립현대미술관"
    assert hangul_part_parent("국립현대미술관별관", lang) == "국립현대미술관"
    assert hangul_part_parent("국립현대미술관청주관", lang) == ""
    assert hangul_part_parent("국립현대미술관서울관", lang) == ""
    assert hangul_part_parent("국립현대미술관과천관", lang) == ""
    assert hangul_part_parent("국립현대미술관덕수궁관", lang) == ""
    assert hangul_part_parent("노원구청", lang) == ""
    assert hangul_part_parent("노원구청주관", lang) == ""
    # "hall annex" is itself a Latin part (hall N), so the parent example has no hall.
    assert latin_part_parent("white cube annex", lang) == "white cube"
    assert latin_part_parent("white cube lobby", lang) == "white cube"
    assert latin_part_parent("seoul city hall", lang) == ""

    rows = [
        _row("mmca", "국립현대미술관"),
        _row("hall", "국립현대미술관 전시실"),
        _row("store", "국립현대미술관 창고동"),
        _row("annex", "국립현대미술관 별관"),
        _row("cheongju-branch", "국립현대미술관 청주관"),
        _row("seoul-branch", "국립현대미술관 서울관"),
        _row("zkm-city", "ZKM, Karlsruhe", "p2"),
        _row("zkm-name", "ZKM Karlsruhe"),
        _row("mmca-seoul-city", "MMCA, Seoul"),
        _row("mmca-cheongju-city", "MMCA, Cheongju", "p2"),
        _row("mmca-seoul", "MMCA Seoul"),
        _row("mmca-cheongju", "MMCA Cheongju"),
        _row("nowon", "노원구"),
        _row("nowon-office", "노원구청"),
        _row("nowon-hosted", "노원구청 주관"),
        _row("nowon-en", "Nowon District Office"),
        _row("yongin", "용인"),
        _row("yongin-en", "Yongin"),
        _row("yongin-qual", "용인 외"),
        _row("namwon", "남원"),
        _row("namwon-en", "Namwon"),
        _row("myeongdong", "명동"),
        _row("myeongdong-en", "Myeongdong"),
        _row("yongin-hall", "용인시청"),
        _row("namwon-museum", "남원시립김병종미술관"),
        _row("white", "White Cube"),
        _row("white-annex", "White Cube Annex", "p2"),
        _row("white-lobby", "White Cube Lobby"),
        _row("seoul-hall", "Seoul City Hall"),
    ]
    result = build(rows, write=False, lang=lang)
    ann = result.annotations

    def kind(activity_id: str) -> str:
        return ann[activity_id]["venue_kind"]

    def vid(activity_id: str) -> str:
        return ann[activity_id]["venue_id"]

    museum = vid("mmca")
    assert vid("hall") == vid("store") == vid("annex") == museum
    assert vid("cheongju-branch") != museum
    assert vid("seoul-branch") != museum
    assert vid("zkm-city") == vid("zkm-name")
    acronym = vid("mmca-seoul-city")
    assert vid("mmca-cheongju-city") == acronym
    assert vid("mmca-seoul") != acronym
    assert vid("mmca-cheongju") != acronym
    assert kind("nowon") == "place_only" and vid("nowon") == ""
    assert kind("nowon-office") == "institution"
    assert vid("nowon-hosted") != vid("nowon")
    assert kind("nowon-en") == "institution"
    for activity_id in ("yongin", "yongin-en", "yongin-qual", "namwon", "namwon-en", "myeongdong", "myeongdong-en"):
        assert kind(activity_id) == "place_only", activity_id
        assert vid(activity_id) == ""
    assert kind("yongin-hall") == "institution"
    assert kind("namwon-museum") == "institution"
    assert vid("yongin-hall") != ""
    assert vid("white") == vid("white-annex") == vid("white-lobby")
    assert kind("seoul-hall") == "institution"
    names = {row["name"] for row in result.venues}
    assert "용인" not in names and "Yongin" not in names and "노원구" not in names
    joined = {(rule, left, right) for rule, left, right in result.merges}
    assert ("V8", "국립현대미술관", "국립현대미술관청주관") not in joined
    assert ("V8", "국립현대미술관", "국립현대미술관서울관") not in joined
    assert ("V8", "mmca", "mmca seoul") not in joined
    assert ("V8", "zkm", "zkm karlsruhe") in joined
    assert not any("용인" in f"{left} {right}" or "yongin" in f"{left} {right}" for _rule, left, right in joined)


def test_v8_acronym_city_joins_only_without_a_second_site(tmp_path: Path) -> None:
    """Join only when the bare acronym's own rows sit mostly in that city and no spelling names a second site.

    A second site is another acronym+place spelling, or a Hangul branch of the same institution.
    A place fragment beside a bare acronym is not that spelling, but it does say where the
    acronym's own rows are: one Meyrin row beside two Geneva rows leaves CERN in Geneva.
    A word with no rows of its own in the city (Loop before Barcelona) is not joined.
    """
    lang = _language_with_fixture_places(tmp_path)
    rows = [
        _row("zkm", "ZKM, Karlsruhe"),
        _row("zkm-name", "ZKM Karlsruhe"),
        _row("cern", "CERN, Geneva"),
        _row("cern-2", "CERN, Geneva"),
        _row("cern-geneva", "CERN Geneva"),
        _row("cern-meyrin", "CERN, Meyrin"),
        _row("home", "HOME, Manchester"),
        _row("home-name", "HOME Manchester"),
        _row("kadist", "Kadist, San Francisco"),
        _row("kadist-2", "Kadist, San Francisco"),
        _row("kadist-sf", "KADIST San Francisco"),
        _row("kadist-paris", "Kadist, Paris"),
        _row("loop", "Loop"),
        _row("loop-sf", "Loop San Francisco"),
        _row("mmca", "MMCA"),
        _row("mmca-seoul", "MMCA Seoul"),
        _row("mmca-cheongju", "MMCA Cheongju"),
        _row("mca", "MCA"),
        _row("mca-sydney", "MCA Sydney"),
        _row("mca-chicago", "MCA Chicago"),
        _row("suny", "SUNY"),
        _row("suny-buffalo", "SUNY Buffalo"),
        _row("suny-purchase", "SUNY Purchase"),
        _row("other-branch", "하얀집 청주관"),
    ]
    result = build(rows, write=False, lang=lang)
    ann = result.annotations
    assert ann["zkm"]["venue_id"] == ann["zkm-name"]["venue_id"]
    assert ann["cern"]["venue_id"] == ann["cern-geneva"]["venue_id"]
    assert ann["home"]["venue_id"] == ann["home-name"]["venue_id"]
    assert ann["kadist"]["venue_id"] == ann["kadist-sf"]["venue_id"]
    assert ann["loop"]["venue_id"] != ann["loop-sf"]["venue_id"]
    assert ann["mmca"]["venue_id"] != ann["mmca-seoul"]["venue_id"]
    assert ann["mmca-seoul"]["venue_id"] != ann["mmca-cheongju"]["venue_id"]
    assert ann["mca"]["venue_id"] != ann["mca-sydney"]["venue_id"]
    assert ann["mca-sydney"]["venue_id"] != ann["mca-chicago"]["venue_id"]
    assert ann["suny"]["venue_id"] != ann["suny-buffalo"]["venue_id"]
    assert ann["suny-buffalo"]["venue_id"] != ann["suny-purchase"]["venue_id"]
    # 하얀집 청주관 is a branch of another institution. It does not split ZKM.
    assert ann["zkm"]["venue_id"] != ann["other-branch"]["venue_id"]
    joined = {(rule, left, right) for rule, left, right in result.merges}
    assert ("V8", "zkm", "zkm karlsruhe") in joined
    assert ("V8", "cern", "cern geneva") in joined
    assert ("V8", "mmca", "mmca seoul") not in joined
    assert ("V8", "mca", "mca sydney") not in joined

    # The Hangul branch is enough on its own when the parent and the acronym are one entity.
    linked = build(
        [
            _row("alias-1", "국립현대미술관 (MMCA)", "p1"),
            _row("alias-2", "국립현대미술관 (MMCA)", "p2"),
            _row("seoul", "MMCA Seoul"),
            _row("branch", "국립현대미술관 청주관"),
        ],
        write=False,
        lang=lang,
    )
    assert linked.annotations["alias-1"]["venue_id"] == linked.annotations["alias-2"]["venue_id"]
    assert linked.annotations["seoul"]["venue_id"] != linked.annotations["alias-1"]["venue_id"]
    assert linked.annotations["branch"]["venue_id"] != linked.annotations["alias-1"]["venue_id"]
    linked_joins = {(rule, left, right) for rule, left, right in linked.merges}
    assert ("V8", "mmca", "mmca seoul") not in linked_joins

    # Two acronym+city spellings, even without a bare row's place fragment.
    both = build(
        [_row("sf", "KADIST San Francisco"), _row("paris", "Kadist Paris"), _row("bare", "Kadist")],
        write=False,
        lang=lang,
    )
    assert both.annotations["sf"]["venue_id"] != both.annotations["bare"]["venue_id"]
    assert both.annotations["paris"]["venue_id"] != both.annotations["bare"]["venue_id"]


def test_institutions_do_not_depend_on_row_order() -> None:
    """Shuffling the input rows gives the same entities, ids, names and merges.

    XYZ has one bare row in Seoul and one in Busan, so neither city is its own
    and XYZ Seoul stays apart (V8 tie rule) in every order.
    """
    lang = KoreanEnglish.load()
    venues = ["XYZ, Seoul", "XYZ, Busan", "XYZ Seoul", "XYZ", "Foo Gallery, Seoul", "Foo Gallery, Busan"]
    tie = [_row(str(index), venue, f"p{index % 3}") for index, venue in enumerate(venues)]

    def snapshot(rows: list[dict]) -> tuple:
        result = build(rows, write=False, lang=lang)
        return result.annotations, result.venues, sorted(result.merges)

    for base in (tie, _load()):
        expected = snapshot(base)
        for seed in range(20):
            rows = list(base)
            random.Random(seed).shuffle(rows)
            assert snapshot(rows) == expected, seed
    annotations = build(tie, write=False, lang=lang).annotations
    assert annotations["2"]["venue_id"] != annotations["3"]["venue_id"]


def test_written_venue_files_do_not_depend_on_row_order(tmp_path: Path) -> None:
    """venues.csv and venue_audit.md, including the audit's random sample, survive a shuffle."""
    lang = KoreanEnglish.load()
    rows = _load()

    def written(order: list[dict], name: str) -> tuple[str, str]:
        out = tmp_path / name
        out.mkdir()
        build(order, out, lang=lang)
        return (out / "venues.csv").read_text(encoding="utf-8"), (out / "venue_audit.md").read_text(encoding="utf-8")

    expected = written(rows, "base")
    for seed in range(5):
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        assert written(shuffled, f"seed{seed}") == expected, seed


def test_geonames_admin_and_neighbourhood_resolve_as_places(tmp_path: Path) -> None:
    """cities15000 drops admin divisions and neighbourhoods. A country extract puts them back.

    Synthetic names: the populated-place row has no Hangul and its Latin alternate is
    below the million-person gate. The administrative row carries both names. A
    hyphenated neighbourhood matches the hyphenless spelling. A small populated
    place that is not an administrative division or a neighbourhood stays out.
    """
    geonames = tmp_path / "geonames"
    countries = tmp_path / "countries"
    geonames.mkdir()
    countries.mkdir()
    packaged_countries = ROOT / "src" / "giye" / "normalize" / "data" / "ko_en" / "countries"
    for name in ("codes.json", "en.json", "ko.json"):
        (countries / name).write_text((packaged_countries / name).read_text(encoding="utf-8"), encoding="utf-8")
    (geonames / "admin1CodesASCII.txt").write_text(
        "KR.13\tGyeonggi-do\tGyeonggi-do\t1\nKR.11\tSeoul\tSeoul\t2\nKR.03\tJeollabuk-do\tJeollabuk-do\t3\n",
        encoding="utf-8",
    )
    (geonames / "cities15000.txt").write_text(
        _geoname_row("1", "Nargen", "Nargen", "Narvon,Obscura", "P", "PPL", "03", 40000) + "\n",
        encoding="utf-8",
    )
    (geonames / "KR.txt").write_text(
        "\n".join(
            (
                _geoname_row("2", "Ficton-si", "Ficton-si", "Ficton,픽톤,픽톤시", "A", "ADM2", "13", 200000),
                _geoname_row("3", "Narvon", "Narvon", "나르본,나르본시", "A", "ADM2", "03", 50000),
                _geoname_row("4", "Myo-dong", "Myo-dong", "묘동", "A", "ADM3", "11", 0),
                _geoname_row("5", "Quar-ton", "Quar-ton", "쿼톤", "P", "PPLX", "11", 0),
                _geoname_row("6", "Tinyville", "Tinyville", "타이니", "P", "PPL", "11", 10),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    gazetteer = Gazetteer.from_geonames(tmp_path)

    def resolved(text: str) -> bool:
        found = gazetteer.resolve_fragments([text])
        return bool(found and found[0])

    assert resolved("픽톤") and resolved("Ficton")
    assert resolved("나르본") and resolved("Narvon")
    assert resolved("묘동") and resolved("Myodong")
    assert resolved("쿼톤") and resolved("Quarton")
    assert resolved("Nargen")
    assert not resolved("Obscura")
    assert not resolved("타이니") and not resolved("Tinyville")

    lang = KoreanEnglish(KoreanEnglish.load().glossary, gazetteer)
    result = build(
        [
            _row("ko", "픽톤"),
            _row("en", "Ficton"),
            _row("hall", "픽톤시청"),
            _row("dong", "묘동"),
            _row("dong-en", "Myodong"),
        ],
        write=False,
        lang=lang,
    )
    for activity_id in ("ko", "en", "dong", "dong-en"):
        assert result.annotations[activity_id]["venue_kind"] == "place_only", activity_id
        assert result.annotations[activity_id]["venue_id"] == ""
    assert result.annotations["hall"]["venue_kind"] == "institution"
    assert result.annotations["hall"]["venue_id"] != ""
    assert not any("픽톤" in f"{left} {right}" or "ficton" in f"{left} {right}" for _rule, left, right in result.merges)


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

        venue_words = VenueWords()

        def personal_name(self, name: str) -> bool:
            return False

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


def test_venue_words_come_from_the_language_module() -> None:
    """V7b, V7c, V7d and V8 read their words from the module: a toy module changes the keys."""
    from tests.toy_language import Toy

    toy = Toy()
    korean = KoreanEnglish.load()
    # V7b: the toy qualifier is trimmed only under the toy module; 외 only under Korean.
    assert institution_key("north hall zz", toy) == institution_key("north hall", toy)
    assert institution_key("north hall zz", korean) != institution_key("north hall", korean)
    assert institution_key("서울시립미술관 외", toy) != institution_key("서울시립미술관", toy)
    # V7c: a toy edition marker.
    assert institution_key("vol 3 north hall", toy) == "north hall"
    assert institution_key("제12회 광주비엔날레", toy) != institution_key("광주비엔날레", toy)
    # V7d: the toy language declares no unstable spacing.
    assert institution_key("탈영역 우정국", toy) != institution_key("탈영역우정국", toy)
    # V8: building parts.
    assert latin_part_parent("north museum wing", toy) == "north museum"
    assert latin_part_parent("north museum wing", korean) == ""
    assert hangul_part_parent("서울시립미술관본관", korean) == "서울시립미술관"
    assert hangul_part_parent("서울시립미술관본관", toy) == ""


def _without(*rules: str) -> frozenset[str]:
    return NAME_RULES - set(rules)


def test_v4n_joins_same_country_and_fold_and_refuses_the_rest(tmp_path: Path) -> None:
    """V4n: one national museum per country when the display fold matches, article kept."""
    rows = [
        _row("seoul-a", "National Art Museum, Seoul", "p1"),
        _row("seoul-b", "National Art Museum, Seoul", "p2"),
        _row("busan", "National Art Museum, Busan", "p3"),
        _row("berlin", "National Art Museum, Berlin", "p4"),
        _row("bare", "National Art Museum", "p5"),
        _row("kr-a", "국립미술관, 서울", "p6"),
        _row("kr-b", "국립미술관, 부산", "p7"),
    ]
    result = build(rows, tmp_path, write=True)
    seoul = result.annotations["seoul-a"]["venue_id"]
    assert result.annotations["seoul-b"]["venue_id"] == seoul
    assert result.annotations["busan"]["venue_id"] == seoul
    assert result.annotations["berlin"]["venue_id"] != seoul
    assert result.annotations["bare"]["venue_id"] != seoul
    hangul = result.annotations["kr-a"]["venue_id"]
    assert result.annotations["kr-b"]["venue_id"] == hangul
    assert hangul != seoul
    assert any(rule == "V4n" for rule, _kept, _joined in result.merges)
    assert "V4n" in result.annotations["busan"]["venue_rule"]
    keeper = next(row for row in result.venues if row["venue_id"] == seoul)
    assert keeper["n_rows"] == 3
    # Seoul and Busan are one entity. Berlin and the row with no city stay apart.
    same_name = [row for row in result.venues if row["name"] == "National Art Museum"]
    assert {row["venue_id"] for row in same_name} == {
        seoul,
        result.annotations["bare"]["venue_id"],
        result.annotations["berlin"]["venue_id"],
    }
    audit = (tmp_path / "venue_audit.md").read_text(encoding="utf-8")
    assert "### V4n — " in audit
    merges = (tmp_path / "venue_merges.csv").read_text(encoding="utf-8")
    assert "V4n," in merges

    folded = build(
        [
            _row("the", "The National Art Museum, Seoul"),
            _row("plain", "National Art Museum, Busan"),
        ],
        write=False,
    )
    assert folded.annotations["the"]["venue_id"] != folded.annotations["plain"]["venue_id"]
    assert not any(rule == "V4n" for rule, _kept, _joined in folded.merges)

    # Outside the module's national_countries two cities' national museums are
    # two institutions (measured on the reference archive: two Polish cities).
    abroad = build(
        [
            _row("ny", "National Art Museum, New York", "p1"),
            _row("la", "National Art Museum, Los Angeles", "p2"),
        ],
        write=False,
    )
    assert abroad.annotations["ny"]["venue_id"] != abroad.annotations["la"]["venue_id"]
    assert not any(rule == "V4n" for rule, _kept, _joined in abroad.merges)

    bare_module = build(rows, lang=KoreanEnglish.load(), write=False)
    assert bare_module.annotations["seoul-a"]["venue_id"] != bare_module.annotations["busan"]["venue_id"]
    assert not any(rule == "V4n" for rule, _kept, _joined in bare_module.merges)


def test_v7f_strips_a_role_on_a_token_boundary_only_when_the_key_exists() -> None:
    """V7f: the role leaves the display name only when the stripped entity is already there.

    A spaced Hangul name and the same name without spaces are already one entity
    under V7d, so the glued spelling is tested on its own. The display name is
    then the glued text, and the role is not a token.
    """
    rows = [
        _row("base", "예시문화재단", "p1"),
        _row("host", "예시문화재단 주최", "p2"),
        _row("lead", "주최 예시문화재단", "p3"),
        _row("collab", "예시문화재단 협력", "p4"),
        _row("partner", "in collaboration with 예시문화재단", "p5"),
        _row("missing", "예시창작재단 후원", "p6"),
        _row("split", "예시국립미술관, 예시문화재단 주최", "p7"),
        _row("gallery", "Yesi Gallery", "p8"),
        _row("english", "supported by Yesi Gallery", "p9"),
    ]
    result = build(rows, write=False)
    base = result.annotations["base"]["venue_id"]
    assert result.annotations["host"]["venue_id"] == base
    assert result.annotations["host"]["funder_id"] == base
    assert result.annotations["lead"]["venue_id"] == base
    assert result.annotations["english"]["venue_id"] == result.annotations["gallery"]["venue_id"]
    for activity_id in ("collab", "partner", "missing"):
        assert result.annotations[activity_id]["venue_id"] != base, activity_id
    museum = result.annotations["split"]["venue_id"]
    assert museum != base
    assert result.annotations["split"]["funder_id"] == base
    assert any(rule == "V7f" for rule, _kept, _joined in result.merges)
    assert not any(rule == "V7f" and "예시창작재단" in joined for rule, _kept, joined in result.merges)

    for glued in ("예시문화재단주최", "주최예시문화재단"):
        alone = build([_row("base", "예시문화재단"), _row("glued", glued)], write=False)
        assert alone.annotations["base"]["venue_id"] != alone.annotations["glued"]["venue_id"], glued
        assert not any(rule == "V7f" for rule, _kept, _joined in alone.merges)


def test_v9u_joins_only_a_unique_pair_that_passes_the_spelling_gate() -> None:
    """V9u: unique exact bag, one reading, same country. V9 already takes a clean pair."""
    # A clean pair is one entity under V9, so the V9u join is observed with V9 off:
    # that is the situation of a pair V9 left as two entities.
    rules = _without("V9")
    joined = build(
        [_row("h", "예시갤러리", "p1"), _row("l", "Yesi Gallery", "p2")],
        name_rules=rules,
        write=False,
    )
    assert joined.annotations["h"]["venue_id"] == joined.annotations["l"]["venue_id"]
    assert any(rule == "V9u" for rule, _kept, _joined in joined.merges)
    assert len(joined.venues) == 1

    with_v9 = build(
        [_row("h", "예시갤러리", "p1"), _row("l", "Yesi Gallery", "p2")],
        write=False,
    )
    assert with_v9.annotations["h"]["venue_id"] == with_v9.annotations["l"]["venue_id"]
    assert any(rule == "V9" for rule, _kept, _joined in with_v9.merges)
    assert not any(rule == "V9u" for rule, _kept, _joined in with_v9.merges)

    ambiguous = build(
        [
            _row("h", "예시갤러리", "p1"),
            _row("a", "Yesi Gallery", "p2"),
            _row("b", "Gallery Yesi", "p3"),
        ],
        name_rules=rules,
        write=False,
    )
    assert len({ambiguous.annotations[key]["venue_id"] for key in ("h", "a", "b")}) == 3
    assert not any(rule == "V9u" for rule, _kept, _joined in ambiguous.merges)

    abroad = build(
        [_row("h", "예시갤러리, Seoul", "p1"), _row("l", "Yesi Gallery, Berlin", "p2")],
        name_rules=rules,
        write=False,
    )
    assert abroad.annotations["h"]["venue_id"] != abroad.annotations["l"]["venue_id"]

    two_readings = build(
        [_row("h", "예시문화재단", "p1"), _row("l", "Yesi Culture Foundation", "p2")],
        name_rules=rules,
        write=False,
    )
    assert two_readings.annotations["h"]["venue_id"] != two_readings.annotations["l"]["venue_id"]

    stored = build(
        [
            _row("h", "예시갤러리 (Yesi Art Hall)", "p1"),
            _row("l", "Yesi Gallery", "p2"),
        ],
        name_rules=rules,
        write=False,
    )
    assert stored.annotations["h"]["venue_id"] != stored.annotations["l"]["venue_id"]
    assert any(rule == "V5f" for rule, _kept, _joined in stored.merges)


def test_v4n_v7f_v9u_ablation_leaves_the_entities_apart() -> None:
    """``--venue-name-rules`` can turn the three rules off without turning V7–V9 off."""
    from giye.normalize.service import parse_name_rules

    assert parse_name_rules("v4n,V7F,V9U") == frozenset({"V4n", "V7f", "V9u"})
    rows = [
        _row("seoul", "National Art Museum, Seoul", "p1"),
        _row("busan", "National Art Museum, Busan", "p2"),
        _row("base", "예시문화재단", "p3"),
        _row("host", "예시문화재단 주최", "p4"),
        _row("h", "예시갤러리", "p5"),
        _row("l", "Yesi Gallery", "p6"),
    ]
    off = build(rows, name_rules=_without("V4n", "V7f", "V9u"), write=False)
    assert off.annotations["seoul"]["venue_id"] != off.annotations["busan"]["venue_id"]
    assert off.annotations["base"]["venue_id"] != off.annotations["host"]["venue_id"]
    # V9 still joins the clean bilingual pair. V9u does not add a second join.
    assert off.annotations["h"]["venue_id"] == off.annotations["l"]["venue_id"]
    assert not any(rule in {"V4n", "V7f", "V9u"} for rule, _kept, _joined in off.merges)
