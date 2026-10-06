# SPDX-License-Identifier: AGPL-3.0-only
"""Language module: personal names, name keys, glossary, gazetteer, romanisation.

The rules that depend on a language read it through this interface, so a later
archive can supply another script pair without editing the rule code. The
Korean–English module is the default. ``personal_name`` is the bare personal-name
test of A2, A3, A4, A6 and T1 (Hangul surname shape, or two to six Latin
tokens). ``name_keys`` wraps ``giye.resolve.names`` (personal
names, rule X1). The venue rules (V7–V9) read generic words, place names, and
romanisation here. Institution merging uses ``romanise`` (one syllable at a
time, Revised Romanization, no cross-syllable sound change), not those
personal-name keys.
"""

from __future__ import annotations

import importlib
import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import yaml

from giye.normalize.gazetteer import Gazetteer
from giye.resolve.names import hangul_name_keys, latin_letter, latin_name_keys, nfc, syllable_rr

if TYPE_CHECKING:  # pragma: no cover
    from giye.config import Config

_HANGUL = re.compile(r"[가-힣]")
# A Latin-only personal name is two to six tokens of Latin-script letters
# (accents included: ``José García``, ``Đặng Thị Lan``). One token is too
# common to treat as a collision of people (the same floor A2 uses). Up to six
# tokens, because names with particles (``Ana Maria de la Cruz``) are personal
# names too; before 2026-10-06 the bound was four, and such a name lost the
# Latin-only guard of A2. Seven or more tokens are read as a title, not a name.
# Separators are spaces, hyphens, apostrophes, and periods. Group words are not
# listed here: they differ by archive and live in the field file's team list,
# which attachment applies on top of this shape.
_LATIN_SEPARATORS = re.compile(r"[\s.'’\-]+")
LATIN_PERSONAL_TOKENS = (2, 6)
DEFAULT_LANGUAGE = "giye.normalize.lang.ko_en:KoEn"

# A bare 2–4 syllable Korean personal name starts with one of these surnames.
# The test only decides whether a name is the kind that collides across people.
# Rule: every surname with at least 2,000 bearers in the 2015 Population and
# Housing Census (Statistics Korea, surname table; as tabulated in Wikipedia's
# "List of Korean surnames"), the first syllable standing for a compound surname
# (남궁, 황보, 제갈, 사공, 선우, 서문). The rarest listed surname is 갈 (2,086).
# 라 (25,974), 계 (6,641) and 시 (4,354) were missing and are added.
KOREAN_SURNAMES = frozenset(
    "김이박최정강조윤장임한오서신권황안송류유홍전고문양손배백허남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부가복태목형피두감음빈동온호범좌팽승간상갈"
    "라계시"
)
# Two-syllable surnames. Most start with a syllable that is a surname of its
# own (남궁 with 남), but 독고 does not, so the compound list is read as well.
KOREAN_COMPOUND_SURNAMES = frozenset({"남궁", "황보", "제갈", "사공", "선우", "서문", "독고", "동방"})
_HANGUL_SYLLABLES = re.compile(r"[가-힣]+")


def korean_personal_shape(text: str) -> bool:
    """A Hangul personal name: the surname shape, written with or without spaces.

    Unspaced: two to four syllables starting with a listed surname, single
    (``KOREAN_SURNAMES``) or compound (``KOREAN_COMPOUND_SURNAMES``).
    Spaced: two to five syllables in all, starting with a listed surname
    (``김 하늘``, ``독고 영재``) or with a word of one syllable (``알렉스 리``,
    ``마리아 김``: a foreign name in Hangul, given name first, whose one-syllable
    word is the family name; ``리`` and other transliterations are not on the
    census list, so the list is not asked). Why the spaced form: a roster's spacing is not stable, and before
    2026-10-06 ``김 하늘`` failed the test and A3 joined it across programmes on
    the name alone. Why not every short Hangul string: the team test
    (``team_like``) reads a non-personal name's aliases and English name for
    group evidence, and a short group name without a team word would lose it.
    """
    words = nfc(text).split()
    if not words or not all(_HANGUL_SYLLABLES.fullmatch(word) for word in words):
        return False
    compact = "".join(words)
    starts = compact[:2] in KOREAN_COMPOUND_SURNAMES or compact[:1] in KOREAN_SURNAMES
    if len(words) == 1:
        return 2 <= len(compact) <= 4 and starts
    if not 2 <= len(compact) <= 5:
        return False
    return starts or any(len(word) == 1 for word in words)


def latin_personal_shape(text: str) -> bool:
    """Two to six tokens of Latin-script letters, separated by spaces, hyphens, apostrophes or periods.

    A token is a run of Latin letters with their combining marks (NFKC first),
    so an accented name has the shape of its plain spelling. Before 2026-10-06
    a token was ``[A-Za-z]+``, and ``José García`` failed the test and lost
    every guard that reads it.
    """
    low, high = LATIN_PERSONAL_TOKENS
    text = unicodedata.normalize("NFKC", text or "").strip()
    tokens = [token for token in _LATIN_SEPARATORS.split(text) if token]
    if not low <= len(tokens) <= high:
        return False
    for token in tokens:
        if not latin_letter(token[0]):
            return False
        if not all(latin_letter(char) or unicodedata.category(char) == "Mn" for char in token):
            return False
    return True


@dataclass(frozen=True)
class VenueWords:
    """Words and markers of one script pair that the venue spelling rules read.

    ``script`` is the character-class body of the pair's own (non-Latin) script.
    V7c reads an edition marker glued to a letter of it, V7d removes spaces in a
    name mostly written in it when ``unstable_spacing`` is set, and V8 reads
    ``building_parts`` after a name mostly written in it. ``qualifiers`` are
    literal trailing words (V7b). ``edition_lead`` and ``edition_tail`` are
    regular-expression alternatives for an edition marker at the start or the
    end of a name (V7c); a four-digit year is handled by the rule itself.
    ``building_parts`` and ``latin_building_parts`` are regular-expression
    alternatives for a V8 part after a name in the pair's script or a Latin name.
    ``admin_offices`` and ``latin_admin_offices`` are the office markers of V8b
    (a district office is not the district). Longer markers are tried first.
    """

    script: str = ""
    qualifiers: tuple[str, ...] = ()
    edition_lead: tuple[str, ...] = ()
    edition_tail: tuple[str, ...] = ()
    unstable_spacing: bool = False
    building_parts: tuple[str, ...] = ()
    latin_building_parts: tuple[str, ...] = ()
    admin_offices: tuple[str, ...] = ()
    latin_admin_offices: tuple[str, ...] = ()


# Korean–English words for V7b–d and V8 (docs/RULES.md). Tuple order is
# alternation order: the first pattern that matches is the one the rule strips.
KO_EN_VENUE_WORDS = VenueWords(
    script="가-힣",
    # 외 / 등 ("and others"), 일대 / 일원 ("around"), and their English forms.
    qualifiers=("외", "등", "일대", "일원", "etc", "and others"),
    # 제12회 ("the 12th"), 12th.
    edition_lead=(r"제\s?\d{1,3}\s?회", r"\d{1,3}(?:st|nd|rd|th)"),
    edition_tail=(r"제?\s?\d{1,3}\s?회",),
    # Korean spacing in names is not stable.
    unstable_spacing=True,
    # Parts inside one site: rooms, halls, floors, wings, and buildings named as
    # parts of that site. 별관 (annex) stays. It is another building on the same
    # site, not a second city. A branch is the entity plus a place and 관
    # (서울관, 과천관, 덕수궁관, 청주관) and is not listed here. 본관, 신관, 구관
    # are the main, new, and old buildings of one site, the same class as 별관.
    # 앞광장 and 야외광장 are the forecourt of a site. A bare 광장 is not listed:
    # a public square is named after a place or landmark (예시문광장 is not
    # 예시문), so it is not a part of an institution (audit m7).
    building_parts=(
        "본관", "별관", "신관", "구관", "창고동", "전시동", "전시관",
        "전시장", r"제?\d*전시실\d*", "멀티프로젝트홀", "대극장", "소극장", "로비", "앞광장", "야외광장",
        "라운지", r"지하\d*층?", r"\d+층",
    ),
    latin_building_parts=(
        "main building", "annex", "lobby", "main hall", "hall [a-z0-9]+", r"gallery \d+", r"\d+(?:st|nd|rd|th)? floor",
    ),
    # V8b. A city hall is not the city. Longer suffixes first when matched.
    admin_offices=("주민센터", "구청", "시청", "군청", "도청"),
    latin_admin_offices=("district office", "city hall"),
)


def packaged_dir() -> Path:
    """Directory of the shipped Korean–English tables (glossary, gazetteer, countries)."""
    return Path(__file__).resolve().parent / "data" / "ko_en"


def load_glossary(path: Path) -> dict[str, tuple[tuple[str, ...], ...]]:
    """Read a glossary YAML file.

    A key is a word in the source script. Each item is one reading: a tuple of
    tokens in the other script, in order. ``[]`` is an empty reading (the word
    is dropped). Order is the file order; V9 tries readings until one matches.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: glossary must be a mapping of word → readings")
    glossary: dict[str, tuple[tuple[str, ...], ...]] = {}
    for word, readings in raw.items():
        if not isinstance(word, str) or not isinstance(readings, list):
            raise TypeError(f"{path}: {word!r} must be a list of readings")
        parsed: list[tuple[str, ...]] = []
        for reading in readings:
            if not isinstance(reading, list) or not all(isinstance(token, str) for token in reading):
                raise TypeError(f"{path}: reading of {word!r} must be a list of strings")
            parsed.append(tuple(reading))
        glossary[word] = tuple(parsed)
    return glossary


def load_generic_titles(path: Path) -> frozenset[str]:
    """Title bases of a generic-title file, in the E3 normal form. A missing file is empty."""
    if not path.is_file():
        return frozenset()
    # Imported here: giye.resolve's package imports this module.
    from giye.resolve.evidence import norm_title, title_base

    lines = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    return frozenset(base for base in (title_base(norm_title(line)) for line in lines if line) if base)


@runtime_checkable
class LanguageModule(Protocol):
    """What the language-dependent rules ask of one script pair."""

    @property
    def name(self) -> str:
        """Short id, for example ``ko-en``."""

    def personal_name(self, name: str) -> bool:
        """True for a bare personal name of the kind that collides across people (A2, A3, A4, A6, T1)."""

    @property
    def venue_words(self) -> VenueWords:
        """Qualifier, edition, spacing, and building-part words of the venue rules (V7b–d, V8)."""

    def name_keys(self, name: str) -> set[str]:
        """Romanized matching keys of a personal name. Empty when the name does not yield any."""

    @property
    def glossary(self) -> dict[str, tuple[tuple[str, ...], ...]]:
        """Generic institution words → readings in the other script."""

    @property
    def gazetteer(self) -> Gazetteer:
        """Place names loaded from a file (or from a GeoNames tree, when configured)."""

    def romanise(self, token: str) -> str:
        """Reading of one token in the other script. Hangul is one syllable at a time."""


class KoreanEnglish:
    """Korean–English module. Glossary and gazetteer are files; romanisation is Revised Romanization."""

    name = "ko-en"
    venue_words = KO_EN_VENUE_WORDS

    def __init__(
        self,
        glossary: dict[str, tuple[tuple[str, ...], ...]],
        gazetteer: Gazetteer,
        generic_titles: frozenset[str] = frozenset(),
    ) -> None:
        """Glossary readings, the place index, and the generic work titles this module serves."""
        self._glossary = glossary
        self._gazetteer = gazetteer
        self._generic_titles = generic_titles

    @property
    def generic_titles(self) -> frozenset[str]:
        """Work-title bases that name no particular work (E3), from ``generic_titles.txt``.

        Optional for other language modules: the resolver reads this attribute
        when a module has it.
        """
        return self._generic_titles

    @property
    def glossary(self) -> dict[str, tuple[tuple[str, ...], ...]]:
        """Generic institution words → readings in the other script."""
        return self._glossary

    @property
    def gazetteer(self) -> Gazetteer:
        """Place names loaded for this module."""
        return self._gazetteer

    def personal_name(self, name: str) -> bool:
        """A bare personal name: Hangul surname shape, or two to six Latin tokens.

        Hangul is :func:`korean_personal_shape` (a listed surname, spaced or not).
        Latin is :func:`latin_personal_shape` (two to six Latin-script tokens,
        no Hangul). A field file's group words are not
        applied here; attachment treats a team-word hit as a group, not as
        this name.
        """
        text = nfc(name).strip()
        if korean_personal_shape(text):
            return True
        if not text or _HANGUL.search(text):
            return False
        return latin_personal_shape(text)

    def name_keys(self, name: str) -> set[str]:
        """Personal-name keys (rule X1). Hangul uses ``hangul_name_keys``; otherwise Latin keys.

        A string with no Hangul personal-name keys falls through to the Latin
        keys. Institution names do not use this; they use ``romanise``.
        """
        keys = hangul_name_keys(name)
        if keys:
            return keys
        return latin_name_keys(name)

    def romanise(self, token: str) -> str:
        """Revised Romanization of each Hangul syllable, concatenated. Other characters stay.

        Sound changes across syllables are not applied. This is the reading V9
        uses for the part of a name the glossary and the gazetteer did not take.
        """
        return "".join(syllable_rr(char) if _HANGUL.fullmatch(char) else char for char in token)

    @classmethod
    def load(
        cls,
        *,
        glossary: Path | None = None,
        cities: Path | None = None,
        reference: Path | None = None,
    ) -> KoreanEnglish:
        """Load the packaged tables, or the files ``glossary`` / ``cities`` / ``reference`` name.

        ``reference``, when set, is a directory of GeoNames dumps (``geonames/``)
        and country tables (``countries/``). It replaces the compact city table
        so a place that table omits can still resolve.
        """
        data = packaged_dir()
        glossary_path = glossary or (data / "glossary.yaml")
        if reference is not None:
            gazetteer = Gazetteer.from_geonames(reference)
        else:
            gazetteer = Gazetteer.from_tables(
                cities_path=cities or (data / "cities.tsv"),
                countries_dir=data / "countries",
                admin1_path=data / "admin1.tsv",
                postal_path=data / "us_postal.txt",
                places_path=data / "kr_places.tsv",
            )
        return cls(load_glossary(glossary_path), gazetteer, load_generic_titles(data / "generic_titles.txt"))


@cache
def _cached(spec: str, glossary: Path | None, cities: Path | None, reference: Path | None) -> LanguageModule:
    """One loaded module per ``(spec, glossary, cities, reference)`` for the process."""
    return load_language(spec, glossary=glossary, cities=cities, reference=reference)


def default_language() -> LanguageModule:
    """The Korean–English module with its packaged tables. Loaded once per process."""
    return _cached(DEFAULT_LANGUAGE, None, None, None)


def language_for(config: Config) -> LanguageModule:
    """The archive's configured module (``[normalize] language_module`` and its tables). Cached."""
    return _cached(
        config.language_module,
        config.normalize_glossary,
        config.normalize_gazetteer,
        config.normalize_reference,
    )


def load_language(
    spec: str,
    *,
    glossary: Path | None = None,
    cities: Path | None = None,
    reference: Path | None = None,
) -> LanguageModule:
    """Import ``package.module:Class`` and call its ``load`` classmethod.

    The default spec is ``giye.normalize.lang.ko_en:KoEn``. A test selects a
    toy module the same way.
    """
    module_name, separator, qualname = spec.partition(":")
    if not separator or not module_name or not qualname:
        raise ValueError(f"language_module must be 'package.module:Class', got {spec!r}")
    module = importlib.import_module(module_name)
    try:
        cls = getattr(module, qualname)
    except AttributeError as exc:
        raise ImportError(f"{spec} has no {qualname}") from exc
    loaded = cls.load(glossary=glossary, cities=cities, reference=reference)
    if not isinstance(loaded, LanguageModule):
        raise TypeError(f"{spec} did not return a LanguageModule")
    return loaded
