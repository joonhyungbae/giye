# SPDX-License-Identifier: AGPL-3.0-only
"""Grounding check: a CV row's year and venue must occur in the CV text it cites.

Why: the schema check (``giye.extract.schema``) validates the shape of a model
reading, not its content. A schema-valid row with an invented venue would be
published with the CV as its source, which is the claim "every fact keeps its
source" made false. This rule is deterministic and checks two fields a reader
can verify against the document:

- the year: the four digits occur in the text, not inside a longer number;
- a non-empty venue (rule G-V, docs/RULES.md), first match wins. Every
  occurrence below is on word boundaries (``giye.normalize.match``): a Latin
  name may not start or end inside a word, a Hangul name may not start inside
  one. Before 2026-10-06 these were substring tests (software review, round 6).
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
  4. bracketed other-script name: when the institution part and a round-bracket
     partner form one V2 bracket pair (``예시관 (Example Hall), City`` or the
     reverse), one is mostly Hangul and the other Latin, and the partner is
     not a place, the partner is tested by rules 2 and 3 as well. Either name
     occurring grounds the venue.

Generic words: an institution part of generic venue words only (``Museum of
Art``, ``Art``, ``Residency``; the V7e/V9 test ``venue_names.specific``) names
no particular place and grounds nothing by rules 2–4. Such a venue holds only
when the whole venue occurs where a venue starts in the text (after a line
start, punctuation or a number), so ``Residency, Seoul`` is not grounded by
``Example Residency, Seoul`` and ``Museum of Art, Busan`` is not grounded by
``Seoul Museum of Art``.

What the rule cannot catch, by design: an invented same-script bracket next
to an institution that occurs (``Example Culture Center (Imaginary Hall)``),
because a same-script bracket is not a second form of the name (rule 4) and
the institution part is the claim; an invented city next to an institution
that occurs (``Example Art Space, Daegu``), because the city is not checked;
and an empty venue, which claims nothing. These are properties of the
institution-part rule, not oversights.

Why the institution part: a model reading "Venue (City, Country)" or a CV
line that puts the city on another line writes "Venue, City, Country". Those
venues are correct, and the city is not the claim a reader checks.

Why rule 4 (author's delegate, 2026-10-06): a reading that gives one
institution in two scripts, one in brackets, wrote both forms of one name; a
CV that writes only one of them grounds it. A bracketed place ("Hall (Seoul)")
a same-script bracket (an acronym, a branch) and a second script other than
Latin (Hanja) are not a second form of the name and are not used.

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
from giye.normalize.language import LanguageModule, default_language
from giye.normalize.match import occurrences, occurs
from giye.normalize.rules import norm_text
from giye.normalize.venues import bracketed_pairs, classify_fragments, institution_key, split_venue

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


def _latin(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and all("LATIN" in unicodedata.name(char, "") for char in letters)


def _other_script(left: str, right: str) -> bool:
    """One side mostly Hangul, the other all Latin letters."""
    return (venue_names.mostly_hangul(left) and _latin(right)) or (venue_names.mostly_hangul(right) and _latin(left))


def bracket_alias(venue: str, part: str, lang: LanguageModule | None) -> str:
    """Rule 4: the other-script name bracketed with the institution ``part``, or ``""``.

    The partner must form one V2 bracket pair with the part, be in the other
    script, and not be a V3 place.
    """
    if not part or lang is None:
        return ""
    for left, right in bracketed_pairs(norm_text(venue)):
        if part not in (left, right):
            continue
        other = right if part == left else left
        if not _other_script(part, other):
            continue
        if classify_fragments([other], lang)[0].kind == "place":
            continue
        return other
    return ""


def names_something(part: str, lang: LanguageModule | None) -> bool:
    """The institution part holds a proper word, not generic venue words only (V7e/V9 ``specific``).

    ``Museum of Art``, ``Art`` and ``Residency`` name no particular place, so
    finding them in a CV that writes ``Seoul Museum of Art`` or ``Example
    Residency`` does not ground a reading (software review, round 6).
    """
    if not part.strip():
        return False
    lang = lang or default_language()
    return venue_names.specific(institution_key(part, lang), lang)


def _part_in(part: str, text: CvText) -> bool:
    """Rules 2 and 3 for one institution name. A part of generic words only grounds nothing."""
    if not names_something(part, text.lang):
        return False
    bare = strip_punct(part)
    if bare and occurs(bare, text.bare, loose_spaces=True):
        return True
    return _cross_script(part, text)


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


def _segment_start(text: str, start: int) -> bool:
    """Nothing but a line start, punctuation or a number comes before ``start``."""
    before = text[:start].rstrip()
    return not before or not before[-1].isalpha()


def venue_in(venue: str, text: CvText) -> bool:
    """An empty venue holds. Otherwise rule 1, 2, 3 or 4 of the module docstring."""
    if not venue.strip():
        return True
    part = institution_part(venue, text.lang)
    if not names_something(part, text.lang):
        # Generic words and a place ("Residency, Seoul"): the whole venue must
        # occur where a venue starts, not as the tail of a longer name.
        return any(_segment_start(text.collapsed, at) for at in occurrences(collapse(venue), text.collapsed))
    if occurs(collapse(venue), text.collapsed):
        return True
    bare = strip_punct(venue)
    if bare and occurs(bare, text.bare):
        return True
    if _part_in(part, text):
        return True
    alias = bracket_alias(venue, part, text.lang)
    return bool(alias) and _part_in(alias, text)


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
