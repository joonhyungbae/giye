# SPDX-License-Identifier: AGPL-3.0-only
"""Name rules that join spellings of one institution (P3 V7–V9).

Why: V4 keys are exact and V5 needs a parenthetical pair written by two artists,
so the same place stayed split across spellings (a qualifier, a missing space,
an acronym plus its city, a Hangul name and its English name). Every later
count of institutions inherits that split.

Rules (deterministic; no network, no fuzzy score). Each merge is written to the
audit with its rule id.

V7a  A work title in 《》〈〉<>「」『』 is not part of a venue's name.
V7b  Trailing qualifiers (외·등·일대·일원·etc.·and others) are not the name.
V7c  An edition marker (leading 제N회·Nth, a glued or separate 19xx/20xx year)
     is not the name. Co-presence is year-bound anyway, so a series is one entity.
V7d  A name mostly in the language's script ignores spaces when that language's
     spacing in names is not stable (Korean).
V7e  Two Latin names with the same bag of words (order-free only when a place
     anchors the bag; of/the/and… dropped; centre/center; plural -s on generic
     words only) are one entity when the bag holds a proper word. Generic words
     alone name no place: Museum of Modern Art and Modern Art Museum stay apart.
     The bag is exact words. The romanisation-tolerant skeleton is NOT used
     here: it would join ACC/AAS, BUG/Book, and MMCA/MCA.
V8   A part of a known entity is that entity. A Hangul entity plus a building or
     room word (본관·서울관·창고동·전시실…), or a Latin one plus a Latin part. An acronym entity plus a place that
     is not somewhere else (ZKM Karlsruhe, not a chain whose rows sit in another city).
V9   A Hangul name and a Latin name are one entity when the Hangul name, read
     with the glossary (generic words), the gazetteer (place names), and
     ``romanise`` for the rest, gives the same bag as the Latin name, the bag
     holds a proper word, and the match is one reading per connected component.
     Two different readings in one component are ambiguous and are not merged.

The words of V7b, V7c, V7d, and V8 are the language module's ``venue_words``
(``giye.normalize.language.VenueWords``); the lists above are the Korean–English
module's. The rules here only assemble them.
"""

from __future__ import annotations

import re
from functools import cache
from itertools import product
from types import SimpleNamespace

from giye.normalize.gazetteer import CITY_SUFFIX, place_key
from giye.normalize.language import LanguageModule, VenueWords, default_language

HANGUL_RE = re.compile(r"[가-힣]")
TITLE_RE = re.compile(r"[《〈<「『][^》〉>」』]*[》〉>」』]")
EDITION_GLUED_RE = re.compile(r"(?<=[a-z])(?:19|20)\d{2}\b")
_YEAR = r"(?:19|20)\d{2}"
STOP = {"of", "the", "and", "for", "de", "des", "du", "la", "le", "für", "und", "fur", "at", "in", "&"}
# Municipal and provincial markers have no English counterpart in most house names.
DROP = {"metropolitan", "municipal", "city", "provincial", "county"}
SYN = {"centre": "center", "theatre": "theater", "musuem": "museum", "galerie": "gallery", "galleria": "gallery"}
GENERIC = {
    "art",
    "museum",
    "gallery",
    "center",
    "space",
    "platform",
    "lab",
    "factory",
    "theater",
    "studio",
    "residency",
    "culture",
    "cultural",
    "foundation",
    "university",
    "media",
    "international",
    "festival",
    "biennale",
    "station",
    "contemporary",
    "modern",
    "national",
    "hall",
    "house",
    "project",
    "institute",
    "new",
    "fine",
    "school",
    "college",
    "library",
    "archive",
    "creative",
    "creation",
    "design",
    "plaza",
    "park",
    "complex",
}
DATE_RE = re.compile(r"\d+\s?(?:월|일|년)")
ACRONYM_SPELLING_RE = re.compile(r"(?=.{2,8}$)(?=.*[A-Z])[A-Z0-9.&]+")


def mostly_hangul(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and sum(bool(HANGUL_RE.fullmatch(char)) for char in letters) > len(letters) / 2


@cache
def _patterns(words: VenueWords) -> SimpleNamespace:
    """V7b–d and V8 expressions built from one language's venue words."""

    def either(parts: tuple[str, ...]) -> str:
        return "|".join(parts)

    script = re.compile(f"[{words.script}]") if words.script else None
    qualifiers = either(tuple(re.escape(word) for word in words.qualifiers))
    lead = either((*words.edition_lead, _YEAR))
    tail = either((_YEAR, *words.edition_tail))
    parts = either(words.building_parts)
    latin_parts = either(words.latin_building_parts)
    return SimpleNamespace(
        script=script,
        qualifier=re.compile(rf"\s*(?:{qualifiers})\.?$", re.IGNORECASE) if qualifiers else None,
        edition_lead=re.compile(rf"^(?:{lead})\s+", re.IGNORECASE),
        edition_tail=re.compile(rf"(?:\s+|(?<=[a-z{words.script}]))(?:{tail})$", re.IGNORECASE),
        letters=re.compile(f"[{words.script}a-z]"),
        part=re.compile(rf"^(?P<parent>.{{2,}}?)(?:{parts})$") if parts else None,
        latin_part=re.compile(rf"^(?P<parent>.+?)\s+(?:{latin_parts})$") if latin_parts else None,
    )


def _words(lang: LanguageModule | None) -> SimpleNamespace:
    return _patterns((lang or default_language()).venue_words)


def _mostly_script(text: str, script: re.Pattern[str] | None) -> bool:
    if script is None:
        return False
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and sum(bool(script.fullmatch(char)) for char in letters) > len(letters) / 2


def strip_titles(text: str) -> str:
    """V7a, on the raw fragment: V4 drops the bracket characters but would keep the title inside them."""
    stripped = TITLE_RE.sub(" ", text).strip()
    return stripped if len(re.findall(r"[가-힣A-Za-z]", stripped)) >= 2 else text


def normalize_key(key: str, lang: LanguageModule | None = None) -> str:
    """V7b–d on a V4 key (already casefolded, quotes and periods removed)."""
    words = _words(lang)
    text = EDITION_GLUED_RE.sub("", key)
    for _ in range(2):
        if words.qualifier is not None:
            text = words.qualifier.sub("", text).strip()
        text = words.edition_lead.sub("", text).strip()
        text = words.edition_tail.sub("", text).strip()
    text = re.sub(r"\s+", " ", text).strip()
    if len(words.letters.findall(text)) < 2:
        return key  # nothing name-like left: keep the V4 key
    if (lang or default_language()).venue_words.unstable_spacing and _mostly_script(text, words.script):
        text = text.replace(" ", "")  # V7d
    return text


def _latin_word(word: str) -> str:
    word = SYN.get(word, word)
    if word.endswith("s") and word[:-1] in GENERIC:  # plural only for generic words: "Cais" is not "Cai"
        word = word[:-1]
    return word


def skeleton(token: str) -> str:
    """V9 only: romanisation-tolerant form of a proper word.

    Vowel and consonant pairs that two romanisations disagree on are folded.
    Never used between two Latin names. There it would join ACC/AAS (both
    collapse to the same skeleton), BUG/Book, and MMCA/MCA.
    """
    text = re.sub(r"[^a-z0-9]", "", token.lower())
    for left, right in (
        ("eo", "o"),
        ("eu", "u"),
        ("oo", "u"),
        ("ae", "e"),
        ("ee", "i"),
        ("wo", "o"),
        ("yu", "u"),
        ("k", "g"),
        ("t", "d"),
        ("p", "b"),
        ("ch", "j"),
        ("r", "l"),
        ("c", "s"),
        ("x", "gs"),
    ):
        text = text.replace(left, right)
    return re.sub(r"(.)\1+", r"\1", text)


def _canon(tokens: list[str], lang: LanguageModule, cross_script: bool) -> tuple[str, ...] | None:
    kept = [token for token in tokens if token and token not in STOP and not (cross_script and token in DROP)]
    if not any(token not in GENERIC for token in kept):
        return None  # generic words only: a bag of them names no particular place
    if cross_script:
        return tuple(sorted(token if token in GENERIC else "~" + skeleton(token) for token in kept))
    if any(lang.gazetteer.is_place_token(token) for token in kept):
        return tuple(sorted(kept))  # order-free only when a place anchors it
    return tuple(kept)  # otherwise word order counts: "Museum Ludwig" ≠ "Ludwig Museum"


def latin_bag(key: str, lang: LanguageModule, cross_script: bool = False) -> tuple[str, ...] | None:
    """V7e (exact words) or, with ``cross_script``, the V9 form compared with Hangul readings."""
    if HANGUL_RE.search(key):
        return None
    return _canon([_latin_word(word) for word in re.findall(r"[a-z0-9]+", key)], lang, cross_script)


def hangul_bags(key: str, lang: LanguageModule) -> tuple[tuple[str, ...], ...]:
    """V9: every reading of a Hangul name, glossary order, so the first reading is the primary one.

    A place token that ends in 시·도·군·구 is not consumed when the next character is 립
    (서울시립 = 서울 + 시립, not 서울시 + 립).
    """
    glossary = lang.glossary
    if not mostly_hangul(key) or DATE_RE.search(key) or re.search(r"[a-z]", key):
        return ()
    text = key.replace(" ", "")
    gloss_max = max((len(word) for word in glossary), default=1)
    pieces: list[tuple[tuple[str, ...], ...]] = []
    rest = ""
    index = 0
    while index < len(text):
        hit: tuple[int, tuple[tuple[str, ...], ...]] | None = None
        for length in range(min(gloss_max, len(text) - index), 1, -1):
            segment = text[index : index + length]
            if segment in glossary:
                hit = (length, glossary[segment])
                break
            place = lang.gazetteer.place_en(segment)
            if place and segment[-1] in "시도군구" and text[index + length : index + length + 1] == "립":
                continue  # 서울시립 = 서울 + 시립, not 서울시 + 립
            if place:
                hit = (length, (tuple(place.split()),))
                break
        if hit is None and text[index] in glossary:
            hit = (1, glossary[text[index]])
        if hit:
            if rest:
                pieces.append(((rest,),))
                rest = ""
            pieces.append(hit[1])
            index += hit[0]
        else:
            char = text[index]
            rest += lang.romanise(char) if HANGUL_RE.fullmatch(char) else char
            index += 1
    if rest:
        pieces.append(((rest,),))
    out: list[tuple[str, ...]] = []
    for combo in product(*pieces):
        bag = _canon([token for part in combo for token in part], lang, cross_script=True)
        if bag and bag not in out:
            out.append(bag)
        if len(out) > 16:
            break
    return tuple(out)


def specific(key: str, lang: LanguageModule) -> bool:
    """A name with at least one proper word (not generic words only, not a date)."""
    return bool(hangul_bags(key, lang) or latin_bag(key, lang))


def part_parent_ok(key: str, lang: LanguageModule) -> bool:
    """V8 parent: a specific name, or a Hangul name longer than one glossary word."""
    if specific(key, lang):
        return True
    return mostly_hangul(key) and key not in lang.glossary and not DATE_RE.search(key) and len(key) >= 4


def trimmed(spelling: str, lang: LanguageModule | None = None) -> bool:
    """True when V7a/V7b had to cut a work title or a qualifier out of this spelling."""
    qualifier = _words(lang).qualifier
    return bool(TITLE_RE.search(spelling) or (qualifier is not None and qualifier.search(spelling.strip())))


def hangul_part_parent(key: str, lang: LanguageModule | None = None) -> str:
    """V8: the entity before a building or room word, for a name mostly in the language's script."""
    words = _words(lang)
    match = words.part.match(key) if words.part is not None and _mostly_script(key, words.script) else None
    return match.group("parent") if match else ""


def latin_part_parent(key: str, lang: LanguageModule | None = None) -> str:
    """V8: the entity before a Latin building or room word, for a name with no letter of the language's script."""
    words = _words(lang)
    native = words.script is not None and words.script.search(key)
    match = words.latin_part.match(key) if words.latin_part is not None and not native else None
    return match.group("parent") if match else ""


def acronym_place_parent(key: str, spellings: list[str], lang: LanguageModule) -> tuple[str, str]:
    """``ZKM Karlsruhe`` → (``zkm``, ``karlsruhe``) when the first word is an acronym and the rest is a place."""
    parts = key.split(" ", 1)
    if len(parts) != 2 or not any(ACRONYM_SPELLING_RE.fullmatch(spelling.split(" ", 1)[0]) for spelling in spellings):
        return "", ""
    if not lang.gazetteer.cities.get(place_key(parts[1])):
        return "", ""
    return parts[0], parts[1]


def city_name(place: str, lang: LanguageModule) -> str:
    """Canonical city used by V8, lower-cased, or the place token when it is not in the index."""
    hit = lang.gazetteer.cities.get(place_key(place))
    if not hit:
        return place
    return CITY_SUFFIX.sub("", hit[3]).lower()
