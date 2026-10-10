# SPDX-License-Identifier: AGPL-3.0-only
"""Rule ids for the career bundle.

What: the constants the builder applies, each with the rule and the reason it
exists. Grouping is a data rule, not a hand list (AGENTS.md).

Why: phase 1 of docs/CAREER.md turns those definitions into columns. Where the
phase 1 spec and CAREER.md differ, the spec wins; the differences are named in
the phase report, not by silent edits here.

How to run: imported by ``python -m giye.career build``. This module is not a
script.
"""

from __future__ import annotations

# D1 cell size. CAREER.md and the phase 1 spec.
K = 10
# A share or a mean of a share needs this many people. Author decision 2026-10-10.
# Counts still use K. The spec's name is PCT_MIN.
PCT_MIN = 20

# Quantiles the bundle may publish. Minimum and maximum are never among them (D2).
QUANTILES: tuple[tuple[str, float], ...] = (
    ("q10", 0.10),
    ("q25", 0.25),
    ("q50", 0.50),
    ("q75", 0.75),
    ("q90", 0.90),
)

# C3f. Fixed family order. A tie in C9 keeps the family earlier in this tuple.
FAMILY_ORDER: tuple[str, ...] = (
    "exhibition",
    "screening",
    "performance",
    "practice_other",
    "discourse",
    "support",
    "private",
)

FAMILIES: dict[str, tuple[str, ...]] = {
    "exhibition": ("solo_exhibition", "group_exhibition"),
    "screening": ("screening",),
    "performance": ("performance",),
    "practice_other": ("festival", "residency", "commission", "collection", "online_release", "other"),
    "discourse": ("talk_workshop", "publication_press", "teaching"),
    "support": ("funding", "award"),
    "private": ("education", "employment", "service"),
}

# kind → family. Every K1 kind is in exactly one family, so family shares partition kind rows.
KIND_FAMILY: dict[str, str] = {kind: family for family, kinds in FAMILIES.items() for kind in kinds}

# C11. A next-window person is counted once, at the first age of the band.
AGE_BANDS: tuple[tuple[int, int, str], ...] = (
    (0, 2, "0-2"),
    (3, 5, "3-5"),
    (6, 10, "6-10"),
    (11, 15, "11-15"),
    (16, 20, "16-20"),
    (21, 30, "21-30"),
)

COUNTRY_GROUPS: tuple[str, ...] = ("home", "abroad", "unresolved")
# C9 buckets that are not a family. Families come first, in FAMILY_ORDER.
MIX_EXTRA: tuple[str, ...] = ("mixed", "none")

# CAREER.md §1, what map_question will call not_covered. Copied, not shortened.
NOT_COVERED: tuple[str, ...] = (
    "contract clauses",
    "fees",
    "salaries",
    "admissions to degree programmes",
    "chances of being selected",
    "what will advance a career",
)

# --- rule ids. The string after each constant is the rule and the reason. ---

C0 = "C0"
"""C0 CV layer. A published person with at least one processed row whose origin
starts with ``cv:``. The roster layer is every published person. Reason: the CV
layer is the people who are still publishing a CV, a minority of the roster."""

C1 = "C1"
"""C1 first year. The A1 ``active_since`` attribute. A CV-layer person without
A1 is excluded from every CV-layer table and counted in
``counts.cv_no_first_year``. Reason: career age is years since the first public
practice year, and A1 is that year."""

C2 = "C2"
"""C2 generation. ``generation_of(first_year)`` as ``GEN-<start>``, the same
five-year bins as the rim (R2). Reason: the site and the bundle must name the
same cohort."""

C3 = "C3"
"""C3 kind shares. Over the person's ``cv:`` rows with a four-digit year (any
channel) and ``year <= first_year + age``, the share of each of the 18 kinds.
The denominator is those rows. Reason: K1 ``career_cv_kind_only`` — a roster
row labelled teaching is only the site channel."""

C3F = "C3f"
"""C3f kind family. The fixed map in this module (exhibition, screening,
performance, practice_other, discourse, support, private). Reason: the
next-window mix and the field trend need a coarser grain than 18 kinds, and
the map is a rule rather than a list edited per archive."""

C4 = "C4"
"""C4 country groups. ``home`` when ``venue_country`` equals the archive
territory, ``abroad`` when the country is non-empty and different,
``unresolved`` when it is empty. Shares use ``cv:`` rows with a venue
(``venue_kind`` not ``empty`` or ``title_only``). An empty territory never
matches ``home``. Reason: an unresolved country is its own bucket and is not
dropped."""

C4R = "C4r"
"""C4r region shares. Among ``home`` rows, the distribution of ``venue_region``.
A region is its own published value only when at least ``k`` distinct people
have a home row there; every other non-empty region is ``other_region``. An
empty region is ``unresolved``. Reason: a region named by fewer than ``k``
people would be a small cell (D1)."""

C5 = "C5"
"""C5 art-tech institution. The venue ids that appear on a processed row with
a non-empty ``event_link`` (P4: the venue hosted a programme edition), or whose
name or alias equals, after NFC and strip, an operator name of an admitted
frame (G13). The share is ``cv:`` rows with a venue id in that set over
``cv:`` rows with a venue id. Reason: titles are what adoption analyses read;
using them here would be circular."""

C6 = "C6"
"""C6 funding share. Rows with ``venue_kind`` ``funder`` or ``activity_kind``
``funding``, over all ``cv:`` rows in the same age window. Reason: funding is
the kind and the funder venue, not a title word."""

C7 = "C7"
"""C7 distinct institutions per year. Distinct ``venue_id`` among ``cv:`` rows
in ``[first_year, first_year + age]``, divided by ``age + 1``. Reason: a raw
count grows with career length; the rate does not."""

C8 = "C8"
"""C8 programme entry. The person's first dated roster edition of that
programme (smallest year). Career age at entry is ``year - first_year``. A
negative value is clamped to 0 and counted in ``counts.entry_before_first_year``.
Reason: a roster year before the first public practice year is not a negative
career."""

C9 = "C9"
"""C9 mix bucket. At a career age, the kind family with the largest share of
the person's rows up to that age. The bucket is that family when its share is
at least 0.5, otherwise ``mixed``. No rows is ``none``. Ties keep the family
earlier in ``FAMILY_ORDER``. Reason: the next window is conditioned on the
shape of the career so far, and the tie break is a rule."""

C10 = "C10"
"""C10 right-censoring. A person contributes to career age ``a`` only when
``first_year + a <= current_year``, and to a next-window cell only when
``first_year + a + 3 <= current_year``. People left out are counted per table
in ``counts.censored``, not dropped without a number. Reason: a career that
has not had the years yet is not a career that did nothing."""

C11 = "C11"
"""C11 age bands. ``0-2``, ``3-5``, ``6-10``, ``11-15``, ``16-20``, ``21-30``.
For the next window a person contributes to a band once, at the first age of
the band. Reason: one-year ages are too thin for a three-year outcome once
``k`` is applied."""

C12 = "C12"
"""C12 cell membership. A person enters a reference-position cell, a next-window
cell, or a programme-entry cell only when they have at least one dated ``cv:``
row in that cell's window (``year <= first_year + age``, or ``year < entry
year``). People without such a row are left out of the cell and counted per
table in ``counts.no_rows_in_window``. Reason: a zero vector from an empty
window is not a measurement."""

RULE_IDS: tuple[str, ...] = (
    C0,
    C1,
    C2,
    C3,
    C3F,
    C4,
    C4R,
    C5,
    C6,
    C7,
    C8,
    C9,
    C10,
    C11,
    C12,
    "D1",
    "D2",
    "D3",
    "D4",
    "D5",
    "K1",
    "R1",
    "R2",
    "A1",
    "P4",
    "P6",
    "G13",
)


def mix_buckets() -> tuple[str, ...]:
    """C9 labels in the order a tie is broken, then ``mixed`` and ``none``."""
    return FAMILY_ORDER + MIX_EXTRA


def band_of(age: int) -> str | None:
    """The C11 band that contains ``age``, or None when ``age`` is outside 0–30."""
    for start, end, label in AGE_BANDS:
        if start <= age <= end:
            return label
    return None


def band_start(label: str) -> int:
    """First age of a C11 band. The next window uses this age once per person."""
    for start, _end, name in AGE_BANDS:
        if name == label:
            return start
    raise KeyError(label)


def reached_age(first_year: int, age: int, current_year: int) -> bool:
    """C10. True when the person has had ``age`` years by ``current_year``.

    ``first_year + age == current_year`` is in. ``first_year + age == current_year + 1`` is out.
    """
    return first_year + age <= current_year


def window_open(first_year: int, age: int, current_year: int) -> bool:
    """C10 for a next window. The three years after ``age`` have to have happened."""
    return first_year + age + 3 <= current_year
