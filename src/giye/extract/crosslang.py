# SPDX-License-Identifier: AGPL-3.0-only
"""Rule X2: fold the Korean and the English copy of one CV event.

Why: a person with a Korean CV and an English CV (or two records joined by a
merge) lists the same exhibition twice, once in each language ("신호 —
서울시립미술관 외" and "Signal — Seoul Museum of Art"). The in-file repeat rule
in ``giye.extract.apply`` compares titles, and a translated title shares no
characters with its original, so both rows were published. X2 compares the
venue instead, through the institution rules that already join a Hangul and a
Latin spelling of one place (V7 spelling and word bag, V9 reading).

The rule is conservative. Two publishable ``cv:`` rows of one person fold when

- one is Korean (Hangul in the title or the venue) and the other is Latin
  script (no Hangul in either, a Latin letter in the title),
- they come from two different CV documents (``origin`` differs). Inside one
  CV a Korean-titled line and an English line at the same venue are two
  entries; a measurement on the production ledger (2026-10-06) found that
  6 of 9 same-document pairs were different shows,
- they have the same year and the same activity type,
- each venue names exactly one institution (V2 split, V3 classification), and
  the two read as the same institution: the same V4/V7a–d key, the same V7e
  word bag, or a V9 reading of the Hangul name equal to the Latin bag (not a
  reading that another Hangul institution in the ledger shares with different
  proper words, such as 예시미술관 and 예시시립미술관; N-3). A V8b
  office is never joined to its place. An empty venue, or a venue that is only
  a place, names no institution and never folds. A name of generic venue words
  only (``Art Space``, ``갤러리``: V4's generic test) never folds, and two venues
  that name different places (``Art Space, 대구`` and ``Art Space, Berlin``) do
  not fold: the place is part of the event (pre-release audit N-2),
- the match is one to one: in that (person, year, type) neither row matches
  any other row on the other side, from any document. Two shows at one museum
  in one year stay separate,
- when the two titles are in the same script, their word bags overlap (the
  Latin row has no Hangul by definition, so in practice this is a Korean
  row that carries a Latin title): Jaccard at least ``TITLE_JACCARD`` over tokens of
  two or more characters, numbers removed (:func:`titles_agree`). A Korean CV
  may write an English title, so a "Korean" row can carry a Latin title, and
  then the two titles can be compared; two unrelated Latin titles at one museum
  in one year are two shows. A Hangul and a Latin title cannot be compared
  without a translation, so a cross-script pair keeps the venue rule alone.

Folding never deletes. The row in the archive's first configured language
(``[archive] languages``; Korean first keeps the Korean row) stays. The other
gets ``publishable=no`` and ``superseded_by=<kept activity_id>; rule=X2`` in its
note, the way a self-reported row is superseded by a CV. Every apply removes
the marks first and decides again, so the fold is idempotent and removing the
mark by hand is undone only if the rule still holds.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from giye.normalize import venue_names
from giye.normalize.language import LanguageModule
from giye.normalize.rules import norm_text
from giye.normalize.venues import classify_fragments, fold_width, institution_key, split_venue

RULE = "X2"
HANGUL_RE = re.compile(r"[가-힣]")
LATIN_RE = re.compile(r"[A-Za-z]")
MARK_RE = re.compile(rf"(?:^|;\s*)superseded_by=[^;]*;\s*rule={RULE}(?=;|$)")
# Same-script titles must share at least half their word bag (Jaccard). Why
# 0.5: the one production fold with two Latin titles differs only by an
# edition mark ("Example '05' Digital Art Exhibition" against "Example Digital
# Art Exhibition", Jaccard 1.0 once the number is dropped), and half the bag
# means one shared generic word such as "exhibition" is not enough on its own.
TITLE_JACCARD = 0.5
TITLE_TOKEN_RE = re.compile(r"[^\W\d_]+")


@dataclass(frozen=True)
class Fold:
    """One X2 fold: the row that stays and the row marked superseded."""

    ledger_id: str
    year: str
    activity_type: str
    kept: dict[str, str]
    folded: dict[str, str]


def clear_marks(activities: list[dict[str, str]]) -> int:
    """Remove every X2 mark and make the row publishable again. Returns how many were cleared."""
    cleared = 0
    for row in activities:
        note = row.get("reviewer_note") or ""
        if not MARK_RE.search(note):
            continue
        row["reviewer_note"] = MARK_RE.sub("", note).strip("; ")
        row["publishable"] = "yes"
        cleared += 1
    return cleared


def _side(row: dict[str, str]) -> str:
    """``ko`` (Hangul in title or venue), ``latin`` (no Hangul, a Latin title), or ""."""
    title = row.get("title") or ""
    venue = row.get("venue") or ""
    if HANGUL_RE.search(title) or HANGUL_RE.search(venue):
        return "ko"
    if LATIN_RE.search(title):
        return "latin"
    return ""


def _title_script(title: str) -> str:
    """``hangul`` if the title has Hangul, ``latin`` if it has a Latin letter and no Hangul, else ""."""
    if HANGUL_RE.search(title):
        return "hangul"
    if LATIN_RE.search(title):
        return "latin"
    return ""


def title_bag(title: str) -> frozenset[str]:
    """Case-folded letter runs of two or more characters; digits (years, edition numbers) are dropped."""
    return frozenset(token for token in TITLE_TOKEN_RE.findall(norm_text(title).casefold()) if len(token) >= 2)


def titles_agree(left: str, right: str) -> bool:
    """The X2 title guard. Same script: word-bag Jaccard >= ``TITLE_JACCARD``. Otherwise True (not comparable)."""
    script = _title_script(left)
    if not script or script != _title_script(right):
        return True
    left_bag, right_bag = title_bag(left), title_bag(right)
    if not left_bag or not right_bag:
        return False
    return len(left_bag & right_bag) / len(left_bag | right_bag) >= TITLE_JACCARD


def institutions(venue: str, lang: LanguageModule) -> list[str]:
    """V4/V7a–d keys of the institution fragments of one venue string (V2 split, V3 kinds)."""
    return _venue_parts(venue, lang)[0]


Places = tuple[frozenset[str], frozenset[str]]


def places(venue: str, lang: LanguageModule) -> Places:
    """Cities and countries named by the place fragments of one venue string."""
    return _venue_parts(venue, lang)[1]


def _venue_parts(venue: str, lang: LanguageModule) -> tuple[list[str], Places]:
    pieces, _aliases = split_venue(fold_width(norm_text(venue)))
    if not pieces:
        return [], (frozenset(), frozenset())
    fragments = classify_fragments(pieces, lang)
    keys = [institution_key(fragment.text, lang) for fragment in fragments if fragment.kind == "institution"]
    found = [fragment.place for fragment in fragments if fragment.kind == "place" and fragment.place]
    cities = frozenset(place.city.casefold() for place in found if place.city)
    return keys, (cities, frozenset(place.country for place in found if place.country))


def same_institution(
    left: str,
    right: str,
    lang: LanguageModule,
    ambiguous: frozenset[tuple[str, ...]] | set[tuple[str, ...]] = frozenset(),
) -> bool:
    """V7 key or word bag, or a V9 Hangul reading equal to the Latin bag. V8b blocks an office.

    A generic name (V4: venue words only) is never the same institution: it
    names no particular place (N-2). A V9 bag in ``ambiguous`` (two Hangul
    institutions of the archive read it alike, N-3) is not a match.
    """
    if not left or not right:
        return False
    if venue_names.generic_name(left, lang) or venue_names.generic_name(right, lang):
        return False
    if venue_names.forbids_place_office_merge(left, right, lang):
        return False
    if left == right:
        return True
    left_bag, right_bag = venue_names.latin_bag(left, lang), venue_names.latin_bag(right, lang)
    if left_bag and left_bag == right_bag:
        return True  # V7e
    for hangul, latin in ((left, right), (right, left)):
        bag = venue_names.latin_bag(latin, lang, cross_script=True)
        if bag and bag not in ambiguous and bag in venue_names.hangul_bags(hangul, lang):
            return True  # V9
    return False


def _other_places(left: Places, right: Places) -> bool:
    """True when both venues name a city (or both a country) and none is shared: two events in two places."""
    return any(mine and theirs and not mine & theirs for mine, theirs in zip(left, right))


def _ambiguous_readings(activities: list[dict[str, str]], lang: LanguageModule) -> set[tuple[str, ...]]:
    """N-3 over every venue in the ledger: V9 bags two different Hangul institutions read alike."""
    hangul: set[str] = set()
    for venue in sorted({(row.get("venue") or "").strip() for row in activities} - {""}):
        if HANGUL_RE.search(venue):
            hangul.update(key for key in institutions(venue, lang) if venue_names.mostly_hangul(key))
    return venue_names.ambiguous_readings({key: key for key in hangul}, lang)


def fold_cross_language(
    activities: list[dict[str, str]],
    *,
    lang: LanguageModule,
    keep_korean: bool,
) -> list[Fold]:
    """Mark the second-language copy of each one-to-one pair. Rows are edited in place.

    Call :func:`clear_marks` first. Groups and rows are visited in sorted order
    so the same ledger gives the same folds on every run.
    """
    groups: dict[tuple[str, str, str], dict[str, list[dict[str, str]]]] = defaultdict(lambda: {"ko": [], "latin": []})
    for row in activities:
        if not (row.get("origin") or "").startswith("cv:") or row.get("publishable") != "yes":
            continue
        if not (row.get("year") or "").strip() or not (row.get("venue") or "").strip():
            continue
        side = _side(row)
        if side:
            groups[(row["ledger_id"], row["year"], row.get("activity_type") or "")][side].append(row)
    keys_of: dict[str, list[str]] = {}
    places_of: dict[str, Places] = {}
    folds: list[Fold] = []
    ambiguous = _ambiguous_readings(activities, lang)
    for (ledger_id, year, activity_type), sides in sorted(groups.items()):
        if not sides["ko"] or not sides["latin"]:
            continue
        for row in sides["ko"] + sides["latin"]:
            if row["activity_id"] not in keys_of:
                keys_of[row["activity_id"]], places_of[row["activity_id"]] = _venue_parts(row.get("venue") or "", lang)
        edges = [
            (ko, latin)
            for ko in sorted(sides["ko"], key=lambda item: item["activity_id"])
            for latin in sorted(sides["latin"], key=lambda item: item["activity_id"])
            if not _other_places(places_of[ko["activity_id"]], places_of[latin["activity_id"]])
            and any(
                same_institution(left, right, lang, ambiguous)
                for left in keys_of[ko["activity_id"]]
                for right in keys_of[latin["activity_id"]]
            )
        ]
        degree: dict[str, int] = defaultdict(int)
        for ko, latin in edges:
            degree[ko["activity_id"]] += 1
            degree[latin["activity_id"]] += 1
        for ko, latin in edges:
            if degree[ko["activity_id"]] != 1 or degree[latin["activity_id"]] != 1:
                continue
            if ko.get("origin") == latin.get("origin"):
                continue  # one CV document: a Korean and an English line there are two entries
            if len(keys_of[ko["activity_id"]]) != 1 or len(keys_of[latin["activity_id"]]) != 1:
                continue
            if not titles_agree(ko.get("title") or "", latin.get("title") or ""):
                continue  # same-script titles that share too few words are two shows
            kept, folded = (ko, latin) if keep_korean else (latin, ko)
            folded["publishable"] = "no"
            note = folded.get("reviewer_note") or ""
            folded["reviewer_note"] = f"{note}; superseded_by={kept['activity_id']}; rule={RULE}".strip("; ")
            folds.append(Fold(ledger_id, year, activity_type, kept, folded))
    return folds
