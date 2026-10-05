# SPDX-License-Identifier: AGPL-3.0-only
"""Language module: personal names, name keys, glossary, gazetteer, romanisation.

The rules that depend on a language read it through this interface, so a later
archive can supply another script pair without editing the rule code. The
Korean–English module is the default. ``personal_name`` is the bare personal-name
test of A3, A4, A6 and T1. ``name_keys`` wraps ``giye.resolve.names`` (personal
names, rule X1). The venue rules (V7–V9) read generic words, place names, and
romanisation here. Institution merging uses ``romanise`` (one syllable at a
time, Revised Romanization, no cross-syllable sound change), not those
personal-name keys.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import yaml

from giye.normalize.gazetteer import Gazetteer
from giye.resolve.names import hangul_name_keys, latin_name_keys, syllable_rr

if TYPE_CHECKING:  # pragma: no cover
    from giye.config import Config

_HANGUL = re.compile(r"[가-힣]")
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
_KOREAN_PERSONAL_NAME = re.compile(r"[가-힣]{2,4}")


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
    """

    script: str = ""
    qualifiers: tuple[str, ...] = ()
    edition_lead: tuple[str, ...] = ()
    edition_tail: tuple[str, ...] = ()
    unstable_spacing: bool = False
    building_parts: tuple[str, ...] = ()
    latin_building_parts: tuple[str, ...] = ()


# Korean–English venue words. The lists are the production rules' words, in their order.
KO_EN_VENUE_WORDS = VenueWords(
    script="가-힣",
    # 외 / 등 ("and others"), 일대 / 일원 ("around"), and their English forms.
    qualifiers=("외", "등", "일대", "일원", "etc", "and others"),
    # 제12회 ("the 12th"), 12th.
    edition_lead=(r"제\s?\d{1,3}\s?회", r"\d{1,3}(?:st|nd|rd|th)"),
    edition_tail=(r"제?\s?\d{1,3}\s?회",),
    # Korean spacing in names is not stable.
    unstable_spacing=True,
    # Buildings (본관 main, 별관 annex, …관 a branch), halls, rooms, floors, lobbies, squares.
    building_parts=(
        "본관", "별관", "신관", "구관", "서울관", "과천관", "덕수궁관", "청주관", "창고동", "전시동", "전시관",
        "전시장", r"제?\d*전시실\d*", "멀티프로젝트홀", "대극장", "소극장", "로비", "앞광장", "야외광장", "광장",
        "라운지", r"지하\d*층?", r"\d+층",
    ),
    latin_building_parts=(
        "main building", "annex", "lobby", "main hall", "hall [a-z0-9]+", r"gallery \d+", r"\d+(?:st|nd|rd|th)? floor",
    ),
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


@runtime_checkable
class LanguageModule(Protocol):
    """What the language-dependent rules ask of one script pair."""

    @property
    def name(self) -> str:
        """Short id, for example ``ko-en``."""

    def personal_name(self, name: str) -> bool:
        """True for a bare personal name of the kind that collides across people (A3, A4, A6, T1)."""

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

    def __init__(self, glossary: dict[str, tuple[tuple[str, ...], ...]], gazetteer: Gazetteer) -> None:
        self._glossary = glossary
        self._gazetteer = gazetteer

    @property
    def glossary(self) -> dict[str, tuple[tuple[str, ...], ...]]:
        return self._glossary

    @property
    def gazetteer(self) -> Gazetteer:
        return self._gazetteer

    def personal_name(self, name: str) -> bool:
        """Two to four Hangul syllables starting with a listed Korean surname."""
        text = name or ""
        return bool(_KOREAN_PERSONAL_NAME.fullmatch(text)) and text[:1] in KOREAN_SURNAMES

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

        ``reference``, when set, is a directory with the production layout
        (``geonames/``, ``countries/``) and replaces the compact city table.
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
            )
        return cls(load_glossary(glossary_path), gazetteer)


@cache
def _cached(spec: str, glossary: Path | None, cities: Path | None, reference: Path | None) -> LanguageModule:
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
