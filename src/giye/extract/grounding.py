# SPDX-License-Identifier: AGPL-3.0-only
"""Grounding check: a CV row's year and venue must occur in the CV text it cites.

Why: the schema check (``giye.extract.schema``) validates the shape of a model
reading, not its content. A schema-valid row with an invented venue would be
published with the CV as its source, which is the claim "every fact keeps its
source" made false. This rule is deterministic and checks two fields a reader
can verify against the document:

- the year: the four digits occur in the text, not inside a longer number;
- a non-empty venue (rule G-V, docs/RULES.md), first match wins:
  1. the whole venue occurs in the text, both sides normalised the same way
     (NFC, case-folded, whitespace collapsed; failing that, punctuation and
     brackets replaced by a space in both);
  2. its institution part occurs: the venue is split as the venue rules split
     it (V2: commas, slashes, round brackets, " - "), the parts are classified
     with the gazetteer (V3), and the first part that is not a place is the
     institution (the first part when every part is a place). City and
     country parts need not occur, and part order does not matter. The part
     is matched in its normalised form, then with spaces removed on both sides
     (a CV that writes "아르코미술관" against a reading "아르코 미술관");
  3. cross-script: a Latin institution part is grounded when some run of one
     to six Hangul words in the text has the same V9 reading (glossary and
     romanisation, ``venue_names.hangul_bags``); a Hangul part when one of its
     V9 readings equals the bag of some run of one to eight Latin words in
     the text. This is the reading the venue rules already trust to join the
     Korean and the English name of one institution.

Why the institution part: a model reading "Venue (City, Country)" or a CV
line that puts the city on another line writes "Venue, City, Country". Those
venues are correct, and the city is not the claim a reader checks.

The title is not part of this rule; it is the field a model most often
rewrites (brackets, line breaks, a second language), and whether a title test
can be added without hiding correct rows has not been measured.

A row that fails is never deleted. It gets ``ungrounded=year``,
``ungrounded=venue`` or ``ungrounded=year+venue`` in its note and
``publishable=no``, and the apply summary counts it. A row whose CV text is not
on disk cannot be checked and is left as it is (counted as ``unchecked``). Only
``cv:`` rows pass through here; roster rows are not model output.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from giye.normalize import venue_names
from giye.normalize.language import LanguageModule
from giye.normalize.rules import norm_text
from giye.normalize.venues import classify_fragments, institution_key, split_venue

_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]|_")
_HANGUL_RUN = re.compile(r"[가-힣]+(?:[ \t]+[가-힣]+)*")
_LATIN_RUN = re.compile(r"[^\n가-힣]+")
NOTE_KEY = "ungrounded"
# Longest text run compared with a venue part in rule 3, in words. The bound
# keeps the cost linear in the text; a longer name is still matched literally
# by rule 2.
HANGUL_SPAN = 6
LATIN_SPAN = 8


def collapse(text: str) -> str:
    """NFC, whitespace collapsed to one space, case-folded."""
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip().casefold()


def strip_punct(text: str) -> str:
    """Punctuation and brackets replaced by a space, then :func:`collapse`."""
    return collapse(_PUNCT.sub(" ", unicodedata.normalize("NFC", text)))


@dataclass
class CvText:
    """One CV text in the forms the check compares against.

    The cross-script readings of the text's word runs are computed only when a
    venue part needs them, once per text.
    """

    collapsed: str
    bare: str
    nospace: str = ""
    raw: str = ""
    lang: LanguageModule | None = None
    _hangul: set[tuple[str, ...]] | None = None
    _latin: set[tuple[str, ...]] | None = None

    @classmethod
    def of(cls, text: str, lang: LanguageModule | None = None) -> CvText:
        bare = strip_punct(text)
        return cls(
            collapsed=collapse(text),
            bare=bare,
            nospace=bare.replace(" ", ""),
            raw=unicodedata.normalize("NFC", text),
            lang=lang,
        )

    def hangul_readings(self) -> set[tuple[str, ...]]:
        """V9 readings of every run of one to ``HANGUL_SPAN`` Hangul words."""
        if self._hangul is None:
            self._hangul = set()
            if self.lang is not None:
                for run in _HANGUL_RUN.findall(self.raw):
                    words = run.split()
                    for start in range(len(words)):
                        for end in range(start + 1, min(start + HANGUL_SPAN, len(words)) + 1):
                            self._hangul.update(venue_names.hangul_bags(" ".join(words[start:end]), self.lang))
        return self._hangul

    def latin_readings(self) -> set[tuple[str, ...]]:
        """V9 bags of every run of one to ``LATIN_SPAN`` Latin words (a line or a Hangul word ends a run)."""
        if self._latin is None:
            self._latin = set()
            if self.lang is not None:
                for run in _LATIN_RUN.findall(self.raw.casefold()):
                    words = re.findall(r"[a-z0-9]+", run)
                    for start in range(len(words)):
                        for end in range(start + 1, min(start + LATIN_SPAN, len(words)) + 1):
                            bag = venue_names.latin_bag(" ".join(words[start:end]), self.lang, cross_script=True)
                            if bag:
                                self._latin.add(bag)
        return self._latin


def year_in(year: str, text: CvText) -> bool:
    """The year occurs as a number of its own (not inside ``120190``)."""
    year = str(year).strip()
    return bool(year) and re.search(rf"(?<!\d){re.escape(year)}(?!\d)", text.collapsed) is not None


def institution_part(venue: str, lang: LanguageModule | None) -> str:
    """The first V2 part of ``venue`` that V3 does not classify as a place; the first part if all are places."""
    parts, _aliases = split_venue(norm_text(venue))
    if not parts:
        return ""
    if lang is None:
        return parts[0]
    for fragment in classify_fragments(parts, lang):
        if fragment.kind != "place":
            return fragment.text
    return parts[0]


def _cross_script(part: str, text: CvText) -> bool:
    """Rule 3: the part's V9 reading equals the reading of some word run of the text."""
    lang = text.lang
    if lang is None:
        return False
    key = institution_key(part, lang)
    if venue_names.mostly_hangul(key):
        readings = venue_names.hangul_bags(key, lang)
        return bool(readings) and any(bag in text.latin_readings() for bag in readings)
    bag = venue_names.latin_bag(key, lang, cross_script=True)
    return bag is not None and bag in text.hangul_readings()


def venue_in(venue: str, text: CvText) -> bool:
    """An empty venue holds. Otherwise rule 1, 2 or 3 of the module docstring."""
    if not venue.strip():
        return True
    if collapse(venue) in text.collapsed:
        return True
    bare = strip_punct(venue)
    if bare and bare in text.bare:
        return True
    part = strip_punct(institution_part(venue, text.lang))
    if part and (part in text.bare or part.replace(" ", "") in text.nospace):
        return True
    return _cross_script(institution_part(venue, text.lang), text)
def failures(row: dict[str, str], text: CvText) -> list[str]:
    """``["year"]``, ``["venue"]``, both, or ``[]`` for a grounded row."""
    missing = []
    if not year_in(row.get("year") or "", text):
        missing.append("year")
    if not venue_in(row.get("venue") or "", text):
        missing.append("venue")
    return missing


@dataclass
class GroundingStats:
    """Rows marked by the grounding check in one apply, by reason."""

    year: int = 0
    venue: int = 0
    both: int = 0
    unchecked: int = 0
    marked_ids: list[str] = field(default_factory=list)

    @property
    def marked(self) -> int:
        return self.year + self.venue + self.both


def mark(row: dict[str, str], missing: list[str], stats: GroundingStats) -> None:
    """Hide the row and record why. The row stays in the ledger."""
    reason = "+".join(missing)
    if reason == "year":
        stats.year += 1
    elif reason == "venue":
        stats.venue += 1
    else:
        stats.both += 1
    stats.marked_ids.append(row.get("activity_id") or "")
    row["publishable"] = "no"
    note = row.get("reviewer_note") or ""
    row["reviewer_note"] = f"{note}; {NOTE_KEY}={reason}".strip("; ")
