# SPDX-License-Identifier: AGPL-3.0-only
"""The subject's own corrections of CV-derived activities (rule S1).

A CV row is rewritten from its cached extraction on every apply, including the
fold after a merge, so an edit to the ledger row alone does not last. A person
who says that one of their CV entries is wrong (an exhibition marked upcoming
has opened, a year or a venue is off) is recorded once in
``<work>/activity_corrections.csv``, and ``apply_extractions`` applies every
correction after the extraction files, every time.

One row per corrected field:

``source_id``, ``title``, ``year``, ``activity_type``, ``venue``
    The CV line as the extraction reads it. Matched without the ledger id, so a
    merge that moves the row to another record keeps the correction. Title and
    venue are compared normalised (``norm_activity_title``).
``activity_id``
    The row's id when the correction was recorded. Also matched, so a row whose
    title an earlier correction changed is still found. Corrections never
    change an activity id: a corrected entry is not a new activity.
``field``, ``value``
    ``upcoming`` (``no`` or ``yes``), ``year``, ``venue``, ``title`` or ``role``.
``stated_at``
    Date of the person's statement, ``YYYY-MM-DD``.
``confirmed_by``
    Optional: another source that says the same (``CV re-pull 2026-10-06``).

The row's note gains ``self_report=<field> <date> (the subject's own
statement)``, followed by ``, confirmed by <confirmed_by>`` when given; a later
apply replaces the marker instead of adding a second one. ``upcoming=no``
removes the ``upcoming`` note key and the ``(예정)`` role suffix and lets a
future year be published. ``publishable`` is recomputed from the row's own
markers: a private CV section or title, ``ungrounded=``, ``suppressed=``,
``superseded_by_cv`` and ``superseded_by=`` keep the row hidden, because a
correction of one field does not vouch for the rest of the row (grounding is
judged on the extraction's reading, before the correction).

A correction that matches no CV row is counted, not dropped: the CV line may
come back after a re-extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from giye.ledger.ids import norm_activity_title
from giye.ledger.io import read_csv

FILE_NAME = "activity_corrections.csv"
FIELDS = [
    "source_id",
    "title",
    "year",
    "activity_type",
    "venue",
    "activity_id",
    "field",
    "value",
    "stated_at",
    "confirmed_by",
]
CORRECTABLE = ("upcoming", "year", "venue", "title", "role")
UPCOMING_SUFFIX = "(예정)"
_HIDDEN_MARKS = re.compile(r"ungrounded=|suppressed=|superseded_by_cv|superseded_by=")


@dataclass(frozen=True)
class Correction:
    source_id: str
    title: str
    year: str
    activity_type: str
    venue: str
    activity_id: str
    field: str
    value: str
    stated_at: str
    confirmed_by: str = ""

    def matches(self, row: dict[str, str]) -> bool:
        """Same CV line as the extraction reads it, or the recorded activity id."""
        if self.activity_id and row.get("activity_id") == self.activity_id:
            return True
        return (
            row.get("origin") == f"cv:{self.source_id}"
            and str(row.get("year") or "") == self.year
            and (row.get("activity_type") or "") == self.activity_type
            and norm_activity_title(row.get("title") or "") == norm_activity_title(self.title)
            and norm_activity_title(row.get("venue") or "") == norm_activity_title(self.venue)
        )

    def marker(self) -> str:
        text = f"self_report={self.field} {self.stated_at} (the subject's own statement)"
        return f"{text}, confirmed by {self.confirmed_by}" if self.confirmed_by else text


def check(correction: Correction) -> None:
    """Refuse a field outside ``CORRECTABLE`` or a value that field cannot hold."""
    if correction.field not in CORRECTABLE:
        raise ValueError(f"field {correction.field!r} is not correctable; use one of {', '.join(CORRECTABLE)}")
    date.fromisoformat(correction.stated_at)
    if not correction.source_id:
        raise ValueError("source_id is empty")
    if correction.field == "upcoming" and correction.value not in ("no", "yes"):
        raise ValueError("upcoming takes no or yes")
    if correction.field == "year" and not re.fullmatch(r"\d{4}", correction.value):
        raise ValueError("year takes four digits")
    if correction.field == "title" and not correction.value.strip():
        raise ValueError("title cannot be empty")


def load(work: Path) -> list[Correction]:
    """Every correction in ``<work>/activity_corrections.csv``, checked. None when the file is absent."""
    path = work / FILE_NAME
    if not path.exists():
        return []
    corrections = []
    for row in read_csv(path):
        item = Correction(**{name: (row.get(name) or "").strip() for name in FIELDS})
        check(item)
        corrections.append(item)
    return corrections


def apply(rows: list[dict[str, str]], corrections: list[Correction], *, today: date) -> tuple[int, list[Correction]]:
    """Apply corrections to CV rows in place. Returns rows changed and corrections that matched nothing."""
    changed: set[int] = set()
    unmatched = []
    cv_rows = [row for row in rows if (row.get("origin") or "").startswith("cv:")]
    for item in corrections:
        hits = [row for row in cv_rows if item.matches(row)]
        if not hits:
            unmatched.append(item)
        for row in hits:
            before = dict(row)
            _correct(row, item, today=today)
            if row != before:
                changed.add(id(row))
    return len(changed), unmatched


def _correct(row: dict[str, str], item: Correction, *, today: date) -> None:
    parts = [part.strip() for part in (row.get("reviewer_note") or "").split(";") if part.strip()]
    upcoming = "upcoming" in parts
    role = (row.get("role") or "").replace(UPCOMING_SUFFIX, "").strip()
    if item.field == "upcoming":
        upcoming = item.value == "yes"
    elif item.field == "role":
        role = item.value.replace(UPCOMING_SUFFIX, "").strip()
    else:
        row[item.field] = item.value.strip()

    parts = [part for part in parts if part != "upcoming" and not part.startswith(f"self_report={item.field} ")]
    if upcoming:
        # The same place apply puts it: right after the section key.
        parts.insert(1 if parts and parts[0].startswith("cv_section=") else 0, "upcoming")
    parts.append(item.marker())
    row["reviewer_note"] = "; ".join(parts)

    year = int(row.get("year") or 0)
    row["role"] = f"{role} {UPCOMING_SUFFIX}".strip() if upcoming and year == today.year else role
    row["publishable"] = "no" if _hidden(row, parts) or (upcoming and year > today.year) else "yes"


def _hidden(row: dict[str, str], parts: list[str]) -> bool:
    """The reasons apply or a curator hide a CV row, other than an upcoming year."""
    # Imported here: apply imports this module.
    from giye.extract.apply import PRIVATE_SECTIONS, PRIVATE_TITLE

    section = next((part.split("=", 1)[1] for part in parts if part.startswith("cv_section=")), "other")
    if section in PRIVATE_SECTIONS:
        return True
    if PRIVATE_TITLE.search(f"{row.get('title') or ''} {row.get('role') or ''}"):
        return True
    return any(_HIDDEN_MARKS.search(part) for part in parts)
