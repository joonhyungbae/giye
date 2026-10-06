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
V8   A part inside one site is that entity: a room, hall, floor, wing, or a
     building named as part of the site (전시실·창고동·별관, main building, annex,
     lobby). 별관 and annex stay parts because they do not name another city.
     A branch does, and is not joined: Hangul <place>관 (서울관·청주관), or a Latin
     name plus a city (MMCA Seoul). An acronym plus a place is joined to the
     acronym when the acronym is specific and no other spelling of that acronym
     names a different city or branch (ZKM Karlsruhe, CERN Geneva). A second site
     is another spelling that is the acronym plus a different gazetteer place
     (MMCA Cheongju, MMCA 과천, MCA Sydney and MCA Chicago), or a Hangul <place>관
     branch of the same institution (국립현대미술관 청주관). A row that names no
     city is not evidence of a site.
V8b  An administrative office is not the place it administers. <place> plus
     구청·시청·군청·도청·주민센터, or City Hall / District Office, is not joined to
     the bare place by V8 or by any other merge rule. The office stays an
     institution.
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
from collections import defaultdict
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
# N-5: the generic words that name a kind of place (a museum, a hall), not a
# quality of one (art, modern, new). A fragment with one of them looks like a
# venue; a fragment without one, before a fragment with one, is read as a title.
VENUE_NOUNS = GENERIC - {
    "art",
    "culture",
    "cultural",
    "media",
    "international",
    "contemporary",
    "modern",
    "national",
    "new",
    "fine",
    "creative",
    "creation",
    "design",
    "project",
}
DATE_RE = re.compile(r"\d+\s?(?:월|일|년)")
ACRONYM_SPELLING_RE = re.compile(r"(?=.{2,8}$)(?=.*[A-Z])[A-Z0-9.&]+")
# V9: the most reading combinations hangul_bags tries for one name (glossary
# order, so the primary readings come first).
MAX_READINGS_TRIED = 4096
# N-1: an entity key whose name is generic venue words only carries the row's
# place after this separator (``museum of art<sep>busan``). It is a control
# character, so no normalised venue string contains it (P2 removes them).
PLACE_SEP = "\x1d"


def name_part(key: str) -> str:
    """The name of an entity key, without the place a generic name is qualified by."""
    return key.split(PLACE_SEP, 1)[0]


def is_qualified(key: str) -> bool:
    """True for a generic name qualified by its row's place. The name rules V7e, V8 and V9 skip it."""
    return PLACE_SEP in key


def mostly_hangul(text: str) -> bool:
    """True when more than half the letters are Hangul. Used by V5e, V5f, and V9."""
    letters = [char for char in name_part(text) if char.isalpha()]
    return bool(letters) and sum(bool(HANGUL_RE.fullmatch(char)) for char in letters) > len(letters) / 2


@cache
def _patterns(words: VenueWords) -> SimpleNamespace:
    """V7b–d and V8 expressions built from one language's venue words."""

    def either(parts: tuple[str, ...]) -> str:
        """Regex alternation. The first alternative that matches is the one the rule strips."""
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
    """Compiled V7/V8 patterns for ``lang``, or the default Korean–English module."""
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


def _strip_markers(key: str, words: SimpleNamespace) -> str:
    """V7b and V7c without the guard: qualifiers and edition markers removed."""
    text = EDITION_GLUED_RE.sub("", key)
    for _ in range(2):
        if words.qualifier is not None:
            text = words.qualifier.sub("", text).strip()
        text = words.edition_lead.sub("", text).strip()
        text = words.edition_tail.sub("", text).strip()
    return re.sub(r"\s+", " ", text).strip()


def generic_name(key: str, lang: LanguageModule | None = None) -> bool:
    """N-1: every word of the name is a generic venue word (museum, gallery, 미술관, 시립…).

    Function words, municipal markers, qualifiers (V7b) and edition markers
    (V7c, years) do not make a name specific. Another number does (Gallery
    1898). A key with no letters is not a name and is not generic.
    """
    language = lang or default_language()
    text = _strip_markers(name_part(key).casefold(), _words(language))
    tokens = re.findall(r"[a-z]+|\d+|[가-힣]+", text)
    if not any(token[0].isalpha() for token in tokens):
        return False
    for token in tokens:
        if token.isdigit():
            if not re.fullmatch(_YEAR, token):
                return False
        elif HANGUL_RE.match(token):
            if hangul_bags(token, language) or DATE_RE.search(token):
                return False
        elif _latin_word(token) not in GENERIC and token not in STOP and token not in DROP:
            return False
    return True


def normalize_key(key: str, lang: LanguageModule | None = None) -> str:
    """V7b–d on a V4 key (already casefolded, quotes and periods removed).

    V7b and V7c never reduce a name to generic words only (N-1): ``Space 1957``
    stays ``space 1957`` and is not joined to every bare ``Space``. The V4 key
    is kept (V7d still applies).
    """
    words = _words(lang)
    text = _strip_markers(key, words)
    if len(words.letters.findall(text)) < 2:
        return key  # nothing name-like left: keep the V4 key
    if text != key.strip() and generic_name(text, lang):
        text = re.sub(r"\s+", " ", key).strip()
    if (lang or default_language()).venue_words.unstable_spacing and _mostly_script(text, words.script):
        text = text.replace(" ", "")  # V7d
    return text


def _latin_word(word: str) -> str:
    """Bag spelling: centre/center, and a trailing s only on a generic word."""
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
    """Word bag for V7e or V9. ``None`` when every kept token is generic."""
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
    key = name_part(key)
    if HANGUL_RE.search(key):
        return None
    return _canon([_latin_word(word) for word in re.findall(r"[a-z0-9]+", key)], lang, cross_script)


def _hangul_pieces(key: str, lang: LanguageModule) -> list[tuple[str, tuple[tuple[str, ...], ...]]]:
    """V9 segmentation: ``(Hangul segment, its readings)`` in order, or [] when ``key`` is not read.

    A place token that ends in 시·도·군·구 is not consumed when the next character is 립
    (서울시립 = 서울 + 시립, not 서울시 + 립).
    """
    glossary = lang.glossary
    key = name_part(key)
    if not mostly_hangul(key) or DATE_RE.search(key) or re.search(r"[a-z]", key):
        return []
    text = key.replace(" ", "")
    gloss_max = max((len(word) for word in glossary), default=1)
    pieces: list[tuple[str, tuple[tuple[str, ...], ...]]] = []
    rest = ""
    rest_source = ""
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
                pieces.append((rest_source, ((rest,),)))
                rest = rest_source = ""
            pieces.append((text[index : index + hit[0]], hit[1]))
            index += hit[0]
        else:
            char = text[index]
            rest += lang.romanise(char) if HANGUL_RE.fullmatch(char) else char
            rest_source += char
            index += 1
    if rest:
        pieces.append((rest_source, ((rest,),)))
    return pieces


def hangul_bags(key: str, lang: LanguageModule) -> tuple[tuple[str, ...], ...]:
    """V9: every reading of a Hangul name, glossary order, so the first reading is the primary one."""
    pieces = [readings for _segment, readings in _hangul_pieces(key, lang)]
    if not pieces:
        return ()
    out: list[tuple[str, ...]] = []
    for tried, combo in enumerate(product(*pieces), 1):
        bag = _canon([token for part in combo for token in part], lang, cross_script=True)
        if bag and bag not in out:
            out.append(bag)
        # At most 17 readings, and at most MAX_READINGS_TRIED combinations: a long
        # name of generic words with two readings each has 2^n combinations and
        # none gives a bag, so the first bound alone never stopped (audit m9).
        if len(out) > 16 or tried >= MAX_READINGS_TRIED:
            break
    return tuple(out)


def hangul_signature(key: str, lang: LanguageModule) -> tuple[str, ...]:
    """N-3: the primary reading of a Hangul name with every word it drops kept as itself.

    V9 reads 시립 as nothing, so 예시미술관 and 예시시립미술관 give one bag.
    Their signatures differ (the second keeps ``=시립``): they are two
    institutions that read alike, and V9 joins neither to the Latin name.
    """
    tokens: list[str] = []
    for segment, readings in _hangul_pieces(key, lang):
        primary = readings[0] if readings else ()
        tokens.extend(primary if primary else ("=" + segment,))
    return tuple(sorted(tokens))


def ambiguous_readings(keys: dict[str, str], lang: LanguageModule) -> set[tuple[str, ...]]:
    """N-3: V9 bags that two different Hangul institutions read alike.

    ``keys`` maps each Hangul key to its component (the entity it already
    belongs to). A bag is ambiguous when keys in two or more components read
    it with different signatures (:func:`hangul_signature`). Keys with one
    signature are spellings of one name and do not make a bag ambiguous.
    """
    seen: dict[tuple[str, ...], dict[tuple[str, ...], set[str]]] = defaultdict(lambda: defaultdict(set))
    for key, component in keys.items():
        bags = hangul_bags(key, lang)
        if not bags:
            continue
        signature = hangul_signature(key, lang)
        for bag in bags:
            seen[bag][signature].add(component)
    return {
        bag
        for bag, by_signature in seen.items()
        if len(by_signature) >= 2 and len(set().union(*by_signature.values())) >= 2
    }


def has_venue_word(text: str, lang: LanguageModule) -> bool:
    """N-5: ``text`` holds a word that names a kind of venue (museum, gallery, 미술관, 극장…).

    Latin: a word in ``VENUE_NOUNS``. Hangul: a glossary word of two or more
    syllables whose reading holds one, anywhere in the name (예시미술관), or a
    one-syllable glossary word (역, 홀) at the end of the name.
    """
    folded = text.casefold()
    if any(_latin_word(word) in VENUE_NOUNS for word in re.findall(r"[a-z]+", folded)):
        return True
    compact = re.sub(r"\s+", "", folded)
    if not HANGUL_RE.search(compact):
        return False
    for word, readings in lang.glossary.items():
        if not any(token in VENUE_NOUNS for reading in readings for token in reading):
            continue
        if (len(word) >= 2 and word in compact) or compact.endswith(word):
            return True
    return False


def specific(key: str, lang: LanguageModule) -> bool:
    """A name with at least one proper word (not generic words only, not a date)."""
    return bool(hangul_bags(key, lang) or latin_bag(key, lang))


def _office_words(lang: LanguageModule | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    words = (lang or default_language()).venue_words
    return words.admin_offices, words.latin_admin_offices


def office_stem(key: str, lang: LanguageModule | None = None) -> str:
    """V8b: the place before an office marker, or "" when ``key`` is not an office.

    Hangul markers are matched with spaces removed (V7d). Latin markers keep a
    space: ``nowon district office`` → ``nowon``. The marker is not a building
    part, so V8 must not strip it and keep the place.
    """
    hangul_offices, latin_offices = _office_words(lang)
    compact = re.sub(r"\s+", "", key)
    for office in sorted(hangul_offices, key=len, reverse=True):
        if office and compact.endswith(office) and len(compact) > len(office):
            return compact[: -len(office)]
    folded = re.sub(r"\s+", " ", key.strip().casefold())
    for office in sorted(latin_offices, key=len, reverse=True):
        tail = " " + office.casefold()
        if folded.endswith(tail) and len(folded) > len(tail):
            return folded[: -len(tail)].strip()
    return ""


def is_admin_office(key: str, lang: LanguageModule | None = None) -> bool:
    """V8b: a district, city, county, or province office, not the place itself."""
    return bool(office_stem(key, lang))


def _canonical_place(text: str, lang: LanguageModule) -> str:
    """Lower-cased canonical city when ``text`` is a whole place name, else ""."""
    hit = lang.gazetteer.cities.get(place_key(text))
    if hit:
        return CITY_SUFFIX.sub("", hit[3]).casefold()
    found = lang.gazetteer.resolve_fragments([text.strip()])
    if found and found[0] and found[0][0]:
        return found[0][0].casefold()
    return ""


def is_place_name(text: str, lang: LanguageModule) -> bool:
    """True when the gazetteer resolves ``text`` as a whole place (V3c, V8 branches)."""
    if len(place_key(text)) < 2:
        return False
    if lang.gazetteer.cities.get(place_key(text)):
        return True
    found = lang.gazetteer.resolve_fragments([text.strip()])
    return bool(found and found[0])


def _same_place(left: str, right: str, lang: LanguageModule) -> bool:
    if place_key(left) == place_key(right):
        return True
    left_city, right_city = _canonical_place(left, lang), _canonical_place(right, lang)
    if left_city and (left_city == right_city or left_city == place_key(right)):
        return True
    return bool(right_city and right_city == place_key(left))


def forbids_place_office_merge(left: str, right: str, lang: LanguageModule) -> bool:
    """V8b: one key is a bare place and the other is that place's office."""
    for office_key, place in ((left, right), (right, left)):
        stem = office_stem(office_key, lang)
        if stem and _same_place(stem, place, lang):
            return True
    return False


def hangul_branch_site(key: str, lang: LanguageModule) -> tuple[str, str]:
    """V8: ``(<entity>, <canonical place>)`` for a Hangul <place>관 branch.

    별관 is not a branch: 별 is not a place name. An annex is another building
    on the same site, so the building-part list still matches it. The place
    marker has to leave at least two characters of entity in front of it.
    The longest place suffix wins, so 덕수궁관 is 덕수궁 and not a shorter tail.
    """
    if not key.endswith("관"):
        return "", ""
    head = key[:-1]
    for length in range(len(head) - 2, 1, -1):
        place = head[-length:]
        if is_place_name(place, lang):
            parent = head[:-length]
            if len(parent) >= 2:
                return parent, city_name(place, lang)
    return "", ""


def is_hangul_branch(key: str, lang: LanguageModule) -> bool:
    """V8: ``key`` is an entity plus <place>관 (서울관, 청주관), a separate site."""
    parent, place = hangul_branch_site(key, lang)
    return bool(parent and place)


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
    """V8: the entity before a room or same-site building word.

    A branch (<place>관) and an administrative office return "" so they are not
    stripped down to the parent institution or the bare place.
    """
    language = lang or default_language()
    words = _words(language)
    if not _mostly_script(key, words.script):
        return ""
    if is_admin_office(key, language) or is_hangul_branch(key, language):
        return ""
    match = words.part.match(key) if words.part is not None else None
    if not match:
        return ""
    suffix = key[len(match.group("parent")) :]
    if suffix.endswith("관") and is_place_name(suffix[:-1], language):
        return ""
    return match.group("parent")


def latin_part_parent(key: str, lang: LanguageModule | None = None) -> str:
    """V8: the entity before a Latin room or same-site building word.

    An office (City Hall, District Office) is not a part of the place name.
    """
    language = lang or default_language()
    words = _words(language)
    if is_admin_office(key, language):
        return ""
    native = words.script is not None and words.script.search(key)
    match = words.latin_part.match(key) if words.latin_part is not None and not native else None
    return match.group("parent") if match else ""


def known_acronyms(spellings_of: dict[str, dict[str, set[str]]]) -> set[str]:
    """Case-folded tokens that some spelling writes as an acronym (``KADIST``, ``MMCA``).

    Title case (``Kadist Paris``) is the same acronym. The token is known because
    another spelling of it is all capitals, not because the title-case word
    itself matches the acronym pattern.
    """
    found: set[str] = set()
    for spellings in spellings_of.values():
        for spelling in spellings:
            first = spelling.split(" ", 1)[0]
            if ACRONYM_SPELLING_RE.fullmatch(first):
                found.add(first.casefold())
    return found


def acronym_place_parent(
    key: str,
    spellings: list[str],
    lang: LanguageModule,
    acronyms: set[str] | None = None,
) -> tuple[str, str]:
    """``ZKM Karlsruhe`` → (``zkm``, ``karlsruhe``) when the first word is an acronym and the rest is a place.

    The caller joins this only when no other spelling of the acronym names a
    different city or branch. ``MMCA Seoul`` stays apart when ``MMCA Cheongju``
    (or a Hangul <place>관 of the same institution) is also present.
    ``acronyms`` is the set from :func:`known_acronyms`, so ``Kadist Paris``
    counts when ``KADIST`` is written somewhere.
    """
    parts = key.split(" ", 1)
    if len(parts) != 2:
        return "", ""
    own = any(ACRONYM_SPELLING_RE.fullmatch(spelling.split(" ", 1)[0]) for spelling in spellings)
    if not own and (not acronyms or parts[0] not in acronyms):
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
