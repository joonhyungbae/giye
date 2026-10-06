# SPDX-License-Identifier: AGPL-3.0-only
"""Grounding check: a CV row's year and venue (or, with no venue, its title) must occur in the CV text it cites.

Why: the schema check (``giye.extract.schema``) validates the shape of a model
reading, not its content. A schema-valid row with an invented venue would be
published with the CV as its source, which is the claim "every fact keeps its
source" made false. This rule is deterministic and checks two fields a reader
can verify against the document:

- the year: the four digits occur in the text, not inside a longer number;
- a non-empty venue (rule G-V, docs/RULES.md), first match wins. Every
  occurrence below is on word boundaries (``giye.normalize.match``): a Latin
  name may not start or end inside a word, a Hangul name may not start inside
  one. Before 2026-10-06 these were substring tests.
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
and an invented title at a venue and year that occur (G-T reads the title of
an empty-venue row only). These are properties of the institution-part rule,
not oversights.

Why the institution part: a model reading "Venue (City, Country)" or a CV
line that puts the city on another line writes "Venue, City, Country". Those
venues are correct, and the city is not the claim a reader checks.

Why rule 4 (author's delegate, 2026-10-06): a reading that gives one
institution in two scripts, one in brackets, wrote both forms of one name; a
CV that writes only one of them grounds it. A bracketed place ("Hall (Seoul)")
a same-script bracket (an acronym, a branch) and a second script other than
Latin (Hanja) are not a second form of the name and are not used.

G-T, the title of an empty-venue row: an empty venue claims nothing, so a
wholly invented row with no venue was published. Such a row holds only when
some part of its title (split at `` / ``, `` | ``, `` - ``, and brackets)
occurs in the text with punctuation and spaces removed (:func:`title_in`).
Why only empty-venue rows: the title is the field a model most often
rewrites. On the production copy of 2026-10-06 (57,529 publishable CV rows
with a text on disk) the part test fails 636 rows; the sampled failures are
correct entries the model recomposed (a programme and its host joined, a
translation added), so a title test on every row would hide correct rows.
Of the 4,472 empty-venue rows it fails 51 (1.1%).

A row that fails is never deleted. It gets ``ungrounded=year``,
``ungrounded=venue``, ``ungrounded=title`` or a ``+`` combination in its note and
``publishable=no``, and the apply summary counts it. A row whose CV text is not
on disk cannot be checked and is left as it is (counted as ``unchecked``). Only
``cv:`` rows pass through here; roster rows are not model output.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

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


# Control characters other than line breaks and tabs. A text snapshot can carry
# them (a backspace inside 大津橋（\x08愛知）), and they hid a venue the CV writes
# from the whole-venue test (production copy, 2026-10-06).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def collapse(text: str) -> str:
    """NFC, control characters removed, whitespace collapsed to one space, case-folded."""
    return _SPACE.sub(" ", _CONTROL.sub("", unicodedata.normalize("NFC", text))).strip().casefold()


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
    relative: frozenset[str] = frozenset()
    _hangul: set[tuple[str, ...]] | None = None
    _latin: set[tuple[str, ...]] | None = None

    @classmethod
    def of(cls, text: str, lang: LanguageModule | None = None, *, taken: date | None = None) -> CvText:
        """``taken`` is the snapshot's date, for years stated as "N days ago"."""
        text = _CONTROL.sub("", text)
        bare = strip_punct(text)
        return cls(
            collapsed=collapse(text),
            bare=bare,
            nospace=bare.replace(" ", ""),
            raw=unicodedata.normalize("NFC", text),
            lang=lang,
            relative=relative_years(text, taken),
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


_DAYS_AGO = re.compile(r"(?<!\d)(\d{1,5})\s+days?\s+ago\b")


def relative_years(text: str, taken: date | None) -> frozenset[str]:
    """Years a page states as "N days ago", counted back from the day the snapshot was taken.

    Why: a portfolio page that prints "3089 days ago" instead of a date names
    a year only relative to the day it was read, and a reading that converts
    it was a miss of the year test (production copy, 2026-10-06).
    """
    if taken is None:
        return frozenset()
    found = set()
    for days in _DAYS_AGO.findall(text.casefold()):
        try:
            found.add(str((taken - timedelta(days=int(days))).year))
        except OverflowError:
            continue
    return frozenset(found)


def year_in(year: str, text: CvText) -> bool:
    """The year occurs as a number of its own (not inside ``120190``), or as "N days ago" (:func:`relative_years`)."""
    year = str(year).strip()
    if not year:
        return False
    return year in text.relative or re.search(rf"(?<!\d){re.escape(year)}(?!\d)", text.collapsed) is not None


# A country code written after a place without a comma ("Seoul KR", "Berlin DE").
_TRAILING_CODE = re.compile(r"^(.+?)\s+([A-Z]{2,3})$")


def venue_parts(venue: str, lang: LanguageModule | None) -> list[str]:
    """V2 parts of ``venue``, with a trailing country code split off as its own part.

    Why the code: a reading "Seoul KR" is one V2 part, which V3 does not read
    as a place, so it was tested as an institution and failed against a CV
    that writes "Seoul South Korea" (production copy, 2026-10-06). The code is
    split off only when V3 reads it as a place (G1 of the venue rules).
    """
    parts, _aliases = split_venue(norm_text(venue))
    if lang is None:
        return parts
    out: list[str] = []
    for part in parts:
        matched = _TRAILING_CODE.match(part)
        if matched and classify_fragments([matched.group(2)], lang)[0].kind == "place":
            out.extend((matched.group(1), matched.group(2)))
        else:
            out.append(part)
    return out


def _non_place_parts(venue: str, lang: LanguageModule | None) -> list[str]:
    parts = venue_parts(venue, lang)
    if lang is None:
        return parts[:1]
    return [fragment.text for fragment in classify_fragments(parts, lang) if fragment.kind != "place"]


def institution_part(venue: str, lang: LanguageModule | None) -> str:
    """The first V2 part of ``venue`` that V3 does not classify as a place; the first part if all are places."""
    parts = venue_parts(venue, lang)
    if not parts:
        return ""
    found = _non_place_parts(venue, lang)
    return found[0] if found else parts[0]


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
    Residency`` does not ground a reading.
    """
    if not part.strip():
        return False
    letters = [char for char in part if char.isalpha()]
    if letters and not any(_latin(char) or "HANGUL" in unicodedata.name(char, "") for char in letters):
        # Another script (Japanese, Chinese): the generic-word lists are Korean
        # and English only, so such a name is read as naming a place, not as
        # generic words (production copy, 2026-10-06: アートラボあいち大津橋).
        return True
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


def _segment_end(text: str, end: int) -> bool:
    """Nothing but a line end, punctuation or a number comes after ``end``."""
    after = text[end:].lstrip()
    return not after or not after[0].isalpha()


def venue_in(venue: str, text: CvText) -> bool:
    """An empty venue holds. Otherwise rule 1, 2, 3 or 4 of the module docstring."""
    if not venue.strip():
        return True
    part = institution_part(venue, text.lang)
    if not names_something(part, text.lang):
        # Generic words and a place ("Residency, Seoul"): the whole venue must
        # occur where a venue starts, not as the tail of a longer name.
        if any(_segment_start(text.collapsed, at) for at in occurrences(collapse(venue), text.collapsed)):
            return True
        # The generic part as a whole segment of the text ("…,Gallery Gallery,"):
        # nothing but punctuation, a number or a line edge on either side.
        needle = collapse(part)
        if needle and any(
            _segment_start(text.collapsed, at) and _segment_end(text.collapsed, at + len(needle))
            for at in occurrences(needle, text.collapsed)
        ):
            return True
        # A later part of two words or more that names something ("biennale,
        # curated by …, 2nd Moscow Biennal"): that part is the claim, tested by
        # rules 2 and 3. One word is not enough: a city the gazetteer does not
        # list ("Gallery, Antwerp") would otherwise ground a generic venue.
        return any(
            len(other.split()) >= 2 and names_something(other, text.lang) and _part_in(other, text)
            for other in _non_place_parts(venue, text.lang)[1:]
        )
    if occurs(collapse(venue), text.collapsed):
        return True
    bare = strip_punct(venue)
    if bare and occurs(bare, text.bare):
        return True
    if _part_in(part, text):
        return True
    alias = bracket_alias(venue, part, text.lang)
    return bool(alias) and _part_in(alias, text)


# Title parts for G-T: " / ", " | ", " - " (and dashes) and ", " between parts,
# and brackets of every kind around one. The comma since 2026-10-06: a reading
# that adds a description after the title ("TESTIGOS, documental sobre …")
# failed although the CV writes the title.
_TITLE_SPLIT = re.compile(r"\s[/|│–—-]\s|,\s|[|│()\[\]{}〈〉《》<>「」『』【】“”\"]")


def title_in(title: str, text: CvText) -> bool:
    """G-T: some part of ``title`` occurs in the text, punctuation and spaces removed on both sides.

    Parts are split at ``_TITLE_SPLIT``; a part shorter than two characters is
    not read. One part is enough, because a model often joins a show and a work,
    or a programme and its host, into one title (the extraction prompt asks for
    ``show / work``).
    """
    parts = [strip_punct(part).replace(" ", "") for part in _TITLE_SPLIT.split(title or "")]
    return any(len(part) >= 2 and part in text.nospace for part in parts)


def failures(row: dict[str, str], text: CvText) -> list[str]:
    """``["year"]``, ``["venue"]``, ``["title"]``, a combination, or ``[]`` for a grounded row.

    The title is read only when the venue is empty (G-T): an empty venue
    claims nothing, so a wholly invented row with no venue was published.
    """
    missing = []
    if not year_in(row.get("year") or "", text):
        missing.append("year")
    venue = row.get("venue") or ""
    if not venue_in(venue, text):
        missing.append("venue")
    title = (row.get("title") or "").strip()
    if not venue.strip() and title and not title_in(title, text):
        missing.append("title")
    return missing


@dataclass
class GroundingStats:
    """Rows marked by the grounding check in one apply, by reason."""

    year: int = 0
    venue: int = 0
    both: int = 0
    # An empty-venue row whose title is not in the CV (G-T), with or without the year.
    title: int = 0
    unchecked: int = 0
    marked_ids: list[str] = field(default_factory=list)
    # Rows checked while their extraction file was applied.
    checked_ids: set[str] = field(default_factory=set)
    # Rows marked by the pass over CV rows no file wrote in this apply (a stale or missing extraction).
    marked_outside_apply: int = 0

    @property
    def marked(self) -> int:
        return self.year + self.venue + self.both + self.title


def mark(row: dict[str, str], missing: list[str], stats: GroundingStats) -> None:
    """Hide the row and record why. The row stays in the ledger."""
    reason = "+".join(missing)
    if "title" in missing:
        stats.title += 1
    elif reason == "year":
        stats.year += 1
    elif reason == "venue":
        stats.venue += 1
    else:
        stats.both += 1
    stats.marked_ids.append(row.get("activity_id") or "")
    row["publishable"] = "no"
    note = row.get("reviewer_note") or ""
    row["reviewer_note"] = f"{note}; {NOTE_KEY}={reason}".strip("; ")


# The review item a row that fails grounding opens. A person checks the row
# against its CV: a reading the CV does not bear out is an invention, a
# reading it does bear out is a miss of this rule (and a reason to tune it).
REVIEW_REASON = "ungrounded_extraction"
_ROW_KEY = re.compile(r"(?:^|;)\s*row=([0-9a-f]+)")
_NOTE_REASON = re.compile(rf"(?:^|;)\s*{NOTE_KEY}=([^;]+)")


def row_key(row: dict[str, str]) -> str:
    """Stable id of one CV reading: source, year, type, title, venue (16 hex digits of SHA-256).

    Not the activity id, which carries the ledger id and changes on a merge.
    """
    origin = row.get("origin") or ""
    material = "\x1f".join(
        (
            origin.removeprefix("cv:"),
            (row.get("year") or "").strip(),
            row.get("activity_type") or "",
            " ".join(norm_text(row.get("title") or "").casefold().split()),
            " ".join(norm_text(row.get("venue") or "").casefold().split()),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _clean(text: str) -> str:
    return " ".join((text or "").replace(";", ",").split())


def sync_review_items(activities: list[dict[str, str]], queue: list[dict[str, str]]) -> tuple[int, int]:
    """Open one review item per ungrounded CV row; close items whose row is grounded or gone.

    Returns (opened, closed). The row itself is not dropped: it stays in the
    ledger with ``publishable=no`` and its ``ungrounded=`` note, and the item
    tells a person to look at it. An item a person already closed is left as
    it is and not opened again for the same reading.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    failing: dict[str, dict[str, str]] = {}
    for row in activities:
        if not (row.get("origin") or "").startswith("cv:"):
            continue
        reason = _NOTE_REASON.search(row.get("reviewer_note") or "")
        if reason:
            failing.setdefault(row_key(row), {**row, "_missing": reason.group(1).strip()})
    known: dict[str, dict[str, str]] = {}
    for item in queue:
        if item.get("reason") == REVIEW_REASON:
            matched = _ROW_KEY.search(item.get("detail") or "")
            if matched:
                known[matched.group(1)] = item
    opened = closed = 0
    for key, row in sorted(failing.items()):
        if key in known:
            continue
        queue.append(
            {
                "queue_id": str(uuid.uuid4()),
                "ledger_id": row.get("ledger_id") or "",
                "reason": REVIEW_REASON,
                "detail": (
                    f"row={key}; activity={row.get('activity_id') or ''}; missing={row['_missing']}; "
                    f"source={(row.get('origin') or '').removeprefix('cv:')}; "
                    f"{_clean(row.get('year') or '')} {_clean(row.get('title') or '')} — {_clean(row.get('venue') or '')}"
                ),
                "status": "open",
                "created_at": now,
            }
        )
        opened += 1
    for key, item in sorted(known.items()):
        if key not in failing and item.get("status") == "open":
            item["status"] = "done"
            item["detail"] = f"{item.get('detail') or ''}; grounded_or_gone={now[:10]}"
            closed += 1
    return opened, closed
