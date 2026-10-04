# SPDX-License-Identifier: AGPL-3.0-only
"""Preprocessing rules: checks (P1), text normalisation (P2), derived attributes (P5).

Every rule is a function of the ledger (and the CV text the ledger points at).
The same input gives the same output, and every derived value names the rule
and the evidence it came from. Nothing here deletes or edits a ledger row:
checks become flags, derivations become rows in ``artist_attributes.csv``.

P5: a value the ledger already holds wins. ``country`` / ``region`` are derived
only when both are empty. ``active_since`` is derived only when that cell is
empty. Medium tags are derived only when both ``field`` and ``category`` are
empty. Birth year has no ledger column; it is written only as a derived row.

The place of a base phrase (L1) and of a venue (V1) comes from the language
module's gazetteer, not from a path in the code.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from giye.normalize.gazetteer import Gazetteer

# Production uses the machine's local calendar year. UTC would move the bound on New Year's Eve.
THIS_YEAR = date.today().year  # noqa: DTZ011
PRACTICE_TYPES = {
    "solo_exhibition",
    "group_exhibition",
    "screening",
    "performance",
    "festival",
    "award",
    "residency",
    "online_release",
}
HANGUL = re.compile(r"[가-힣]")
LATIN = re.compile(r"[A-Za-z]")


# ── P2 normalisation ──────────────────────────────────────────────────────────────────────────────


def norm_text(text: str | None) -> str:
    """P2: NFC, no control characters, single spaces, typographic brackets kept."""
    cleaned = unicodedata.normalize("NFC", text or "")
    # Control characters, zero-width space through joiner, and the byte-order mark.
    cleaned = re.sub(r"[\u0000-\u001f\u007f\u200b-\u200d\ufeff]", " ", cleaned).replace("\xa0", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def match_key(text: str | None) -> str:
    """P2: comparison key, case-folded, letters and digits only."""
    return re.sub(r"[\W_]+", "", norm_text(text).casefold())


def lang_of(text: str | None) -> str:
    """P2: ko / en / mixed / "" by script share (Hangul vs Latin letters)."""
    hangul, latin = len(HANGUL.findall(text or "")), len(LATIN.findall(text or ""))
    if not hangul and not latin:
        return ""
    if hangul and latin and min(hangul, latin) / (hangul + latin) >= 0.2:
        return "mixed"
    return "ko" if hangul > latin else "en"


# ── P1 checks (flags, never deletions) ───────────────────────────────────────────────────────────

# Y0 year_missing, Y1 year_range, Y2 year_from_title. The flag strings are the
# long names; the Y-ids are the production names of the same three checks.
PERIOD_IN_TITLE = r"(?:after|since|nach|seit|depuis|dopo|desde)\s+{y}\b|{y}\s*년\s*이후"


def year_flags(rows: list[dict]) -> dict[str, list[str]]:
    """P1: activity_id → flags for one artist's rows. Nothing is deleted.

    Y0 year_missing     no year
    Y1 year_range       year outside 1900 … this year + 2
    Y2 year_from_title  the row's year is a period the title itself names
                        ("Art After 1945"). A range like 2008–2009 is not
                        flagged: the row's year is its start.
    """
    out: dict[str, list[str]] = {}
    for row in rows:
        raw = str(row.get("year", ""))
        year = int(raw) if raw.isdigit() else None
        flags: list[str] = []
        if year is None:
            flags.append("year_missing")  # Y0
        elif year < 1900 or year > THIS_YEAR + 2:
            flags.append("year_range")  # Y1
        elif re.search(PERIOD_IN_TITLE.format(y=year), row.get("title") or "", re.IGNORECASE):
            flags.append("year_from_title")  # Y2
        if flags:
            out[row["activity_id"]] = flags
    return out


# ── P5 derived attributes ────────────────────────────────────────────────────────────────────────

BIRTH = [
    re.compile(r"\(?\bb\.\s?((?:19|20)\d\d)\b", re.IGNORECASE),  # (b. 1985 · b.1985
    re.compile(r"\bborn(?:\s+in)?\s+((?:19|20)\d\d)\b", re.IGNORECASE),  # born 1978 · born in 1993
    re.compile(r"((?:19|20)\d\d)\s*년\s*생"),  # 1997년생
    re.compile(r"((?:19|20)\d\d)\s*년\s*(?:[가-힣]{2,8}\s*)?출생"),  # 1993년 출생 · 1984년 서울 출생
]
BASED = [
    re.compile(
        r"(?:based in|based between|lives and works in|lives and works between|works and lives in|"
        r"living and working in|lives and works in both)\s+([^.;\n()]{2,80})",
        re.IGNORECASE,
    ),
    re.compile(r"([가-힣][가-힣\s,·]{1,20}?)(?:을|를)\s*기반으로\s*(?:활동|작업)"),
    re.compile(r"([가-힣][가-힣\s]{1,15}?)에서\s*(?:거주|활동|작업)하"),
    re.compile(r"([가-힣][가-힣\s]{1,15}?)\s*거주"),
]
# Statements of birth and base sit in the opening lines. Later text mentions
# other people's births and other cities.
HEAD_CHARS = 4000


def birth_year(texts: list[str]) -> int | None:
    """B1 (P5): exactly one distinct plausible year across the birth phrases in the CV heads.

    Plausible is 1900 … this year minus 15. Two different years, or none, leave the value empty.
    """
    found = {int(match.group(1)) for text in texts for pattern in BIRTH for match in pattern.finditer(text[:HEAD_CHARS])}
    found = {year for year in found if 1900 <= year <= THIS_YEAR - 15}
    return found.pop() if len(found) == 1 else None


def based_in(texts: list[str], gazetteer: Gazetteer) -> tuple[list[tuple[str, str]], str]:
    """L1 (P5): places a base phrase names, via the gazetteer. The first CV that has one wins.

    Returns ``([(country, Korean region)], phrase)``.
    """
    for text in texts:
        head = text[:HEAD_CHARS]
        for pattern in BASED:
            for match in pattern.finditer(head):
                places = gazetteer.resolve(match.group(1))
                if places:
                    return places, norm_text(match.group(0))[:120]
    return [], ""


def active_since(rows: list[dict], flags: dict[str, list[str]]) -> tuple[int, str] | None:
    """A1 (P5): earliest year among public practice rows that carry no year flag.

    Practice is an exhibition, screening, performance, festival, award, residency,
    or release. A row whose note says ``upcoming`` is skipped. Returns
    ``(year, activity_id)``.
    """
    best: tuple[int, str] | None = None
    for row in rows:
        if row.get("publishable") != "yes" or row.get("activity_type") not in PRACTICE_TYPES:
            continue
        if flags.get(row["activity_id"]) or not str(row.get("year", "")).isdigit():
            continue
        if "upcoming" in (row.get("reviewer_note") or ""):
            continue
        year = int(row["year"])
        if best is None or year < best[0]:
            best = (year, row["activity_id"])
    return best


# Floor used when a caller does not pass the field file's minimum. The vocabulary
# itself is the field file (``[[tags.medium]]``).
MEDIUM_MIN_ROWS = 2


def medium_tags(
    rows: list[dict],
    *,
    words: tuple[tuple[str, str], ...] | None = None,
    screening_tag: str | None = None,
    min_rows: int | None = None,
) -> dict[str, list[str]]:
    """M1 (P5): a tag when at least ``min_rows`` public rows name it in the title or role.

    ``words`` is ``(tag, regex)`` from the field file. When it is omitted, the
    shipped Korean media-art list is used. A screening row counts as
    ``screening_tag`` (that file's tag for film). One mention can be incidental;
    two is a pattern. A strand note (``strand=…``) counts as well.
    Returns ``{tag: [activity_id, …]}``.
    """
    if words is None or screening_tag is None or min_rows is None:
        from giye.field import shipped_field

        shipped = shipped_field().resolved()
        if words is None:
            words = shipped.medium_words
        if screening_tag is None:
            screening_tag = shipped.screening_tag
        if min_rows is None:
            min_rows = shipped.medium_min_rows
    compiled = [(tag, re.compile(pattern, re.IGNORECASE)) for tag, pattern in words]
    hits: dict[str, list[str]] = {}
    for row in rows:
        if row.get("publishable") != "yes":
            continue
        note = re.search(r"strand=[^;]*", row.get("reviewer_note") or "")
        text = f"{row.get('title', '')} {row.get('role', '')} {note.group(0) if note else ''}"
        for tag, pattern in compiled:
            if pattern.search(text) or (screening_tag and tag == screening_tag and row.get("activity_type") == "screening"):
                hits.setdefault(tag, []).append(row["activity_id"])
    return {tag: ids for tag, ids in hits.items() if len(ids) >= min_rows}


def venue_place(venue: str, gazetteer: Gazetteer) -> tuple[str, str]:
    """V1: the first place a venue string names as a fragment of its own.

    The fragment must itself be a place (``words=False`` inside fragment
    resolution). Returns ``(country, Korean region)`` or ``("", "")``.
    """
    parts = [
        re.sub(r"^\s*the\s+", "", part, flags=re.IGNORECASE).strip()
        for part in re.split(r"[,/&|·;]|\band\b|\bbetween\b|그리고|및", venue or "")
    ]
    got = next((place for place in gazetteer.resolve_fragments(parts) if place), None)
    return (got[1], got[2]) if got else ("", "")


def event_links(rows: list[dict], memberships: list[str], event_pattern) -> dict[str, str]:
    """P4: activity_id → frame edition it is an account of.

    A roster row (origin = a frame code) is its edition. A CV row is linked to
    an edition of a frame the artist is on when its title or venue names that
    frame's event in that edition's year. Rows stay as they are.

    The production docstring calls this check E1. That id is also the
    same-person website rule. The string is kept; see the questions in the
    port report. The link itself stores the frame code, not the id.
    """
    out: dict[str, str] = {}
    editions = []
    for code in memberships:
        match = re.search(r"-(\d{4})$", code)
        pattern = event_pattern(code)
        if match and pattern:
            editions.append((code, int(match.group(1)), re.compile(pattern, re.IGNORECASE)))
    for row in rows:
        origin = row.get("origin") or ""
        if origin in memberships:
            out[row["activity_id"]] = origin
            continue
        if not origin.startswith("cv:") or not str(row.get("year", "")).isdigit():
            continue
        text = f"{row.get('title', '')} {row.get('venue', '')}"
        for code, year, compiled in editions:
            if int(row["year"]) == year and compiled.search(text):
                out[row["activity_id"]] = code
                break
    return out
