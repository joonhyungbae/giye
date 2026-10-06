# SPDX-License-Identifier: AGPL-3.0-only
"""Grounding check: a CV row's year and venue must occur in the CV text it cites.

Why: the schema check (``giye.extract.schema``) validates the shape of a model
reading, not its content. A schema-valid row with an invented venue would be
published with the CV as its source, which is the claim "every fact keeps its
source" made false. This rule is deterministic and checks two fields a reader
can verify against the document:

- the year: the four digits occur in the text, not inside a longer number;
- a non-empty venue: the whitespace-collapsed, case-folded venue occurs in the
  text treated the same way, or, when that fails, after punctuation in both is
  replaced by a space and whitespace collapsed again ("Museum, Seoul" against
  "Museum (Seoul)").

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
from dataclasses import dataclass, field

_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]|_")
NOTE_KEY = "ungrounded"


def collapse(text: str) -> str:
    """Whitespace collapsed to one space, case-folded."""
    return _SPACE.sub(" ", text).strip().casefold()


def strip_punct(text: str) -> str:
    """Punctuation replaced by a space, then :func:`collapse`."""
    return collapse(_PUNCT.sub(" ", text))


@dataclass
class CvText:
    """One CV text in the two forms the check compares against."""

    collapsed: str
    bare: str

    @classmethod
    def of(cls, text: str) -> CvText:
        return cls(collapsed=collapse(text), bare=strip_punct(text))


def year_in(year: str, text: CvText) -> bool:
    """The year occurs as a number of its own (not inside ``120190``)."""
    year = str(year).strip()
    return bool(year) and re.search(rf"(?<!\d){re.escape(year)}(?!\d)", text.collapsed) is not None


def venue_in(venue: str, text: CvText) -> bool:
    """An empty venue holds. Otherwise the collapsed venue, or its punctuation-free form, occurs in the text."""
    if not venue.strip():
        return True
    if collapse(venue) in text.collapsed:
        return True
    bare = strip_punct(venue)
    return bool(bare) and bare in text.bare


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
