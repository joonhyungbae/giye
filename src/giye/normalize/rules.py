# SPDX-License-Identifier: AGPL-3.0-only
"""Preprocessing rules: checks (P1), text normalisation (P2), derived attributes (P5, P6).

Every rule is a function of the ledger (and the CV text the ledger points at).
The same input gives the same output, and every derived value names the rule
and the evidence it came from. Nothing here deletes or edits a ledger row:
checks become flags, derivations become rows in ``artist_attributes.csv``.

P5: a value the ledger already holds wins. ``country`` / ``region`` are derived
only when both are empty. ``active_since`` is derived only when that cell is
empty. Medium tags are derived only when both ``field`` and ``category`` are
empty. Birth year has no ledger column; it is written only as a derived row.

P6: record depth, one level per published person, the highest they satisfy.
There is no ledger cell. The site snapshot does not copy the row.

The place of a base phrase (L1) and of a venue (V1) comes from the language
module's gazetteer, not from a path in the code.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from datetime import date
from pathlib import Path

from giye.normalize.gazetteer import Gazetteer

# The bound is the machine's local calendar year. UTC would move it across New Year's Eve.
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

# Y0 year_missing, Y1 year_range, Y2 year_from_title (docs/RULES.md). The stored
# flag is the long name; the Y-id names the same check.
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


def event_links(
    rows: list[dict],
    memberships: list[str],
    event_pattern: Callable[[str], str | None],
) -> dict[str, str]:
    """P4: activity_id → frame edition it is an account of.

    A roster row (origin = a frame code) is its edition. A CV row is linked to
    an edition of a frame the artist is on when its title or venue names that
    frame's event in that edition's year. Rows stay as they are. The stored
    value is the frame code. This is not identity rule E1 (docs/RULES.md): E1
    joins two people who share a website, and reusing that id here would make
    the two rules indistinguishable.
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


# ── P6 record depth ──────────────────────────────────────────────────────────────────────────────
# One level per published person, the highest they satisfy. No ledger cell, so
# this is not a case of "the ledger value wins". The site loader drops the row.

# The mark a later CV extraction writes on the row it replaced.
SUPERSEDED_MARK = "superseded_by_cv"
# T1 snippet classes that are practice evidence. has_title_only and nothing are not.
PRACTICE_CLASSES = ("has_description", "has_medium_word")
_SNIPPET_CLASS_FIELDS = ("classification", "label", "class", "kind")
_SNIPPET_LIST_FIELDS = ("labels", "classes", "tags")


def _id_list(rows: list[dict], key: str, limit: int) -> str:
    """Sorted unique ids, capped so one artist's evidence cannot grow with every row."""
    vals = sorted({(row.get(key) or "") for row in rows if row.get(key)})
    shown = "|".join(vals[:limit])
    extra = len(vals) - limit
    if extra > 0:
        return f"{shown}|+{extra}"
    return shown


def extracted_cv_rows(activities: list[dict]) -> list[dict]:
    """Level 4: a CV extraction that still stands.

    ``origin`` ``cv:`` is a row the extraction apply step wrote. A note containing
    ``superseded_by_cv`` is a row a later extraction replaced, so it is not standing.
    ``publishable`` is not consulted: a private or future CV row is still an extraction.
    """
    kept = []
    for row in activities:
        origin = row.get("origin") or ""
        if not origin.startswith("cv:"):
            continue
        if SUPERSEDED_MARK in (row.get("reviewer_note") or ""):
            continue
        kept.append(row)
    return kept


def own_site_rows(links: list[dict]) -> list[dict]:
    """Level 3, own website: a non-social link.

    ``link_type`` ``social`` is the exclusion used when a personal site is the
    identity key. Video and repository links are not social, so they count.
    A dead link still counts: the location is registered on the row.
    """
    return [
        row
        for row in links
        if (row.get("link_type") or "") != "social" and (row.get("url") or "").strip()
    ]


def active_cv_rows(sources: list[dict]) -> list[dict]:
    """Level 3, registered CV location: a ``cv_sources`` row that is still active.

    ``active`` defaults to true. ``false`` is a retired location. Extraction reads
    these rows, not links.
    """
    return [row for row in sources if (row.get("active") or "true") != "false"]


def load_snippet_classes(path: Path, gy_to_ledger: dict[str, str] | None = None) -> dict[str, list[str]]:
    """ledger_id → practice classes in ``data/work/tendency/snippets.jsonl``.

    When the path is missing, nobody has snippet evidence and level 2 is the M1
    rows only. A later preprocess reads the file if it has appeared.

    One object per person: ``gy_id`` and ``class`` (``has_description``,
    ``has_medium_word``, ``has_title_only``, or ``nothing``). The first two are
    practice evidence. ``gy_to_ledger`` maps that id onto the ledger row. A line
    may instead carry ``ledger_id``.

    Also accepted, so a differently shaped file still counts: boolean fields
    ``has_description`` / ``has_medium_word``; a string ``classification`` /
    ``label`` / ``class`` / ``kind``; a list ``labels`` / ``classes`` / ``tags``.
    ``kept`` false drops the line. A practice line with no resolvable id is an
    error: dropping it would publish level 2 too low.
    """
    if not path.is_file():
        return {}
    gy_to_ledger = gy_to_ledger or {}
    found: dict[str, set[str]] = {}
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{lineno}: expected a JSON object")  # noqa: TRY004 — a bad file, not a bad call
            if obj.get("kept") is False or obj.get("kept") == "false":
                continue
            classes = _snippet_classes(obj)
            if not classes:
                continue
            ledger_id = _snippet_ledger_id(obj, gy_to_ledger, path, lineno)
            found.setdefault(ledger_id, set()).update(classes)
    return {ledger_id: sorted(classes) for ledger_id, classes in found.items()}


def _snippet_ledger_id(obj: dict, gy_to_ledger: dict[str, str], path: Path, lineno: int) -> str:
    """Ledger id of one snippet. A gy_id that points at a different row is an error."""
    ledger_id = (obj.get("ledger_id") or "").strip()
    gy_id = (obj.get("gy_id") or "").strip()
    if ledger_id and gy_id:
        mapped = gy_to_ledger.get(gy_id)
        if mapped and mapped != ledger_id:
            raise ValueError(f"{path}:{lineno}: gy_id does not match ledger_id")
        return ledger_id
    if ledger_id:
        return ledger_id
    if not gy_id:
        raise ValueError(f"{path}:{lineno}: practice snippet has no ledger_id or gy_id")
    mapped = gy_to_ledger.get(gy_id)
    if not mapped:
        raise ValueError(f"{path}:{lineno}: gy_id is not on an artist row")
    return mapped


def _snippet_classes(obj: dict) -> set[str]:
    """Practice classes on one snippet object. Title-only and nothing are not practice."""
    found: set[str] = set()
    for name in PRACTICE_CLASSES:
        if obj.get(name) in (True, "true", "yes", 1):
            found.add(name)
    for key in _SNIPPET_CLASS_FIELDS:
        value = obj.get(key)
        if isinstance(value, str) and value in PRACTICE_CLASSES:
            found.add(value)
    for key in _SNIPPET_LIST_FIELDS:
        value = obj.get(key)
        if isinstance(value, list):
            found.update(item for item in value if item in PRACTICE_CLASSES)
    return found


def record_depth(
    *,
    activities: list[dict],
    links: list[dict],
    cv_sources: list[dict],
    medium_values: list[str],
    snippet_classes: list[str],
    memberships: list[str],
) -> tuple[str, str]:
    """P6: ``("1"|"2"|"3"|"4", evidence)``.

    Evidence names the file and the rows that justify the level actually assigned.
    Lower levels the person also meets are not listed. The evidence URL stays
    empty: a personal site address is not copied into the derived row.
    """
    extracted = extracted_cv_rows(activities)
    if extracted:
        origins = sorted({(row.get("origin") or "")[3:] for row in extracted if (row.get("origin") or "")[3:]})
        evidence = (
            f"activities.csv origin=cv:{'|'.join(origins)} rows={len(extracted)} "
            f"activity_id={_id_list(extracted, 'activity_id', 5)}"
        )
        return "4", evidence

    sites = own_site_rows(links)
    cvs = active_cv_rows(cv_sources)
    if sites or cvs:
        parts = []
        if sites:
            parts.append(f"links.csv link_id={_id_list(sites, 'link_id', 20)}")
        if cvs:
            parts.append(f"cv_sources.csv source_id={_id_list(cvs, 'source_id', 20)}")
        return "3", "; ".join(parts)

    parts = []
    if medium_values:
        parts.append("artist_attributes.csv field=medium value=" + "|".join(sorted(set(medium_values))))
    classes = [name for name in PRACTICE_CLASSES if name in set(snippet_classes)]
    if classes:
        parts.append("snippets.jsonl " + "|".join(classes))
    if parts:
        return "2", "; ".join(parts)

    codes = [code for code in memberships if code]
    unique = sorted(set(codes))
    shown = "|".join(unique[:20])
    extra = f"|+{len(unique) - 20}" if len(unique) > 20 else ""
    return "1", f"frame_membership.csv editions={len(codes)} frame_code={shown}{extra}"


def published_ids(
    artists: list[dict],
    membership: list[dict],
    scope_rows: list[dict],
    frames: list[dict],
    edition_of: Callable[[str], tuple[str, str | None] | None],
) -> set[str]:
    """Ledger ids the site publishes. P6 is defined on that population.

    Same test as the site build: not ``scope=out``, on a roster or
    ``cv_link_ok=yes``, a source URL (the row's, or else the roster page's), and
    status empty, PUBLISHED, or STAGED. ``edition_of`` maps a membership code to
    ``(registry frame, year)`` so a frame URL can fill a row that has none.
    The artist row is not modified.
    """
    frame_url = {frame["code"]: frame.get("source_url") or "" for frame in frames if frame.get("code")}
    roster_url: dict[str, str] = {}
    members: set[str] = set()
    for row in membership:
        ledger_id = row.get("ledger_id") or ""
        if ledger_id:
            members.add(ledger_id)
        edition = edition_of(row.get("frame_code") or "")
        for url in (row.get("source_url") or "", frame_url.get(edition[0], "") if edition else ""):
            if str(url).startswith("http"):
                roster_url.setdefault(ledger_id, url)
                break
    out_of_scope = {row["ledger_id"] for row in scope_rows if row.get("scope") == "out" and row.get("ledger_id")}
    published: set[str] = set()
    for artist in artists:
        ledger_id = artist.get("ledger_id") or ""
        if not ledger_id or ledger_id in out_of_scope:
            continue
        if artist.get("cv_link_ok") != "yes" and ledger_id not in members:
            continue
        source = artist.get("source_url") or ""
        if not source.startswith("http"):
            source = roster_url.get(ledger_id, "")
        if not source.startswith("http"):
            continue
        if artist.get("status") not in ("", "PUBLISHED", "STAGED"):
            continue
        published.add(ledger_id)
    return published
