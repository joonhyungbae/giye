# SPDX-License-Identifier: AGPL-3.0-only
"""Rule X2: find the Korean and the English copy of one CV event; a person decides the fold.

Why: a person with a Korean CV and an English CV (or two records joined by a
merge) lists the same exhibition twice, once in each language ("신호 —
서울시립미술관 외" and "Signal — Seoul Museum of Art"). The in-file repeat rule
in ``giye.extract.apply`` compares titles, and a translated title shares no
characters with its original, so both rows were published. X2 compares the
venue instead, through the institution rules that already join a Hangul and a
Latin spelling of one place (V7 spelling and word bag, V9 reading).

Since 2026-10-06 X2 does not fold on its own (author decision after software
review 5, MAJOR-2): a venue match cannot tell one show from two shows at one
museum in one year, and Korean and English CVs often list different
selections. A pair that meets the conditions below becomes a review-queue item
(``reason=cross_language_duplicate``) for a person to decide. Two publishable
``cv:`` rows of one person are such a pair when

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
  a place, names no institution and is never a pair. A name of generic venue
  words only (``Art Space``, ``갤러리``: V4's generic test) is never a pair, and
  two venues that name different places (``Art Space, 대구`` and ``Art Space,
  Berlin``) are not: the place is part of the event (pre-release audit N-2),
- the match is one to one: in that (person, year, type) neither row matches
  any other row on the other side, from any document. Two shows at one museum
  in one year are not queued as one,
- when the two titles are in the same script, they are similar enough
  (:func:`titles_agree`, the title-similarity rule in docs/RULES.md) before
  the pair is even queued; two unrelated Latin titles at one museum in one
  year are two shows. A Hangul and a Latin title cannot be compared without a
  translation, so a cross-script pair is queued on the venue rule alone.

A person's decision is recorded on that queue item (``decided=same`` or
``decided=different`` with ``decided_at``) and honoured on every apply. The
item names the pair by :func:`pair_key`, a hash of the two rows' source,
year, type, title and venue, not by activity id or ledger id, so the decision
survives a merge (which changes both). ``same`` folds the pair even when the
queue conditions no longer hold (a later row broke one-to-one, say): the
person judged these two rows, and nothing about the rows changed. Two rows
that changed are a new pair and are queued again.

Folding never deletes. The row in the archive's first configured language
(``[archive] languages``; Korean first keeps the Korean row) stays. The other
gets ``publishable=no`` and ``superseded_by=<kept activity_id>; rule=X2+H;
x2_decided=<date>`` in its note. Every apply removes the marks first and
decides again (also the ``rule=X2`` marks of the automatic folds before
2026-10-06), so the fold is idempotent and follows the recorded decisions.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from giye.normalize import venue_names
from giye.normalize.language import LanguageModule
from giye.normalize.rules import norm_text
from giye.normalize.venues import classify_fragments, fold_width, institution_key, split_venue

RULE = "X2"
# The rule a recorded fold carries: X2 found the pair, a person (H) decided it.
DECIDED_RULE = "X2+H"
REASON = "cross_language_duplicate"
HANGUL_RE = re.compile(r"[가-힣]")
LATIN_RE = re.compile(r"[A-Za-z]")
# Also clears the automatic ``rule=X2`` marks written before 2026-10-06.
MARK_RE = re.compile(rf"(?:^|;\s*)superseded_by=[^;]*;\s*rule={RULE}(?:\+H)?(?:;\s*x2_decided=[^;]*)?(?=;|$)")
_PAIR_RE = re.compile(r"(?:^|;)\s*x2_pair=([0-9a-f]+)")
_DECISION_RE = re.compile(r"(?:^|;)\s*decided=(same|different)(?=;|$)")
_DECIDED_AT_RE = re.compile(r"(?:^|;)\s*decided_at=(\d{4}-\d{2}-\d{2})")
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
    rule: str = DECIDED_RULE
    decided_at: str = ""


@dataclass
class CrossLanguage:
    """What one X2 pass did: folds a person decided, pairs waiting, pairs kept apart."""

    folds: list[Fold] = field(default_factory=list)
    # Queue items opened in this pass (a pair seen for the first time).
    queued: int = 0
    # Pairs that meet X2 and have no decision yet (open items, new or older).
    pending: int = 0
    # Pairs a person decided are two events.
    kept_apart: int = 0


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


def _signature(row: dict[str, str]) -> str:
    """What identifies one CV line apart from its owner: source, year, type, title, venue (normalised)."""
    origin = row.get("origin") or ""
    parts = (
        origin.removeprefix("cv:"),
        (row.get("year") or "").strip(),
        row.get("activity_type") or "",
        " ".join(norm_text(row.get("title") or "").casefold().split()),
        " ".join(norm_text(row.get("venue") or "").casefold().split()),
    )
    return "\x1f".join(parts)


def pair_key(left: dict[str, str], right: dict[str, str]) -> str:
    """Stable id of an unordered pair of CV rows (16 hex digits of SHA-256).

    Built from the rows' content, not their activity or ledger ids: a merge
    moves the rows to the survivor and gives them new activity ids, and a
    person's decision about the two lines must still find them.
    """
    material = "\x1e".join(sorted((_signature(left), _signature(right))))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def recorded_decisions(queue: list[dict[str, str]]) -> dict[str, tuple[str, str]]:
    """``pair_key`` → (``same`` or ``different``, ``decided_at``) from decided X2 queue items."""
    found: dict[str, tuple[str, str]] = {}
    for item in queue:
        if item.get("reason") != REASON:
            continue
        detail = item.get("detail") or ""
        key, decision = _PAIR_RE.search(detail), _DECISION_RE.search(detail)
        if key and decision:
            when = _DECIDED_AT_RE.search(detail)
            found[key.group(1)] = (decision.group(1), when.group(1) if when else "")
    return found


def item_pair_key(item: dict[str, str]) -> str:
    """The ``x2_pair=`` key a queue item names, or ""."""
    matched = _PAIR_RE.search(item.get("detail") or "")
    return matched.group(1) if matched else ""


def _clean(text: str) -> str:
    """A title or venue as it can sit in a ``;``-separated detail cell."""
    return " ".join((text or "").replace(";", ",").replace("|", "/").split())


def _queue_item(ledger_id: str, key: str, ko: dict[str, str], latin: dict[str, str], now: str) -> dict[str, str]:
    detail = (
        f"x2_pair={key}; year={ko.get('year') or ''}; type={ko.get('activity_type') or ''}; "
        f"korean={ko['activity_id']}; latin={latin['activity_id']}; "
        f"{_clean(ko.get('title') or '')} — {_clean(ko.get('venue') or '')} | "
        f"{_clean(latin.get('title') or '')} — {_clean(latin.get('venue') or '')}"
    )
    return {
        "queue_id": str(uuid.uuid4()),
        "ledger_id": ledger_id,
        "reason": REASON,
        "detail": detail,
        "status": "open",
        "created_at": now,
    }


def _fold(kept: dict[str, str], folded: dict[str, str], decided_at: str) -> None:
    folded["publishable"] = "no"
    note = folded.get("reviewer_note") or ""
    mark = f"superseded_by={kept['activity_id']}; rule={DECIDED_RULE}"
    if decided_at:
        mark = f"{mark}; x2_decided={decided_at}"
    folded["reviewer_note"] = f"{note}; {mark}".strip("; ")


def fold_cross_language(
    activities: list[dict[str, str]],
    *,
    lang: LanguageModule,
    keep_korean: bool,
    queue: list[dict[str, str]] | None = None,
) -> CrossLanguage:
    """Fold the pairs a person decided ``same`` and queue the undecided ones. Edits rows and ``queue`` in place.

    Call :func:`clear_marks` first. Groups and rows are visited in sorted order
    so the same ledger gives the same folds and the same new items on every
    run. ``queue`` None reads no decisions and opens no items (nothing folds).
    """
    result = CrossLanguage()
    queue_rows: list[dict[str, str]] = queue if queue is not None else []
    decisions = recorded_decisions(queue_rows)
    known = {item_pair_key(item) for item in queue_rows if item.get("reason") == REASON} - {""}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    ambiguous = _ambiguous_readings(activities, lang)
    for (ledger_id, year, activity_type), sides in sorted(groups.items()):
        if not sides["ko"] or not sides["latin"]:
            continue
        ko_rows = sorted(sides["ko"], key=lambda item: item["activity_id"])
        latin_rows = sorted(sides["latin"], key=lambda item: item["activity_id"])
        # 1. Recorded decisions, whatever the queue conditions say now.
        folded_ids: set[str] = set()
        for ko in ko_rows:
            for latin in latin_rows:
                if ko["activity_id"] in folded_ids or latin["activity_id"] in folded_ids:
                    continue
                decision, decided_at = decisions.get(pair_key(ko, latin), ("", ""))
                if decision != "same":
                    continue
                kept, folded = (ko, latin) if keep_korean else (latin, ko)
                _fold(kept, folded, decided_at)
                folded_ids.update((ko["activity_id"], latin["activity_id"]))
                result.folds.append(Fold(ledger_id, year, activity_type, kept, folded, DECIDED_RULE, decided_at))
        # 2. Pairs that meet X2 and are not decided yet go to the queue.
        for row in ko_rows + latin_rows:
            if row["activity_id"] not in keys_of:
                keys_of[row["activity_id"]], places_of[row["activity_id"]] = _venue_parts(row.get("venue") or "", lang)
        edges = [
            (ko, latin)
            for ko in ko_rows
            for latin in latin_rows
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
                continue  # same-script titles that share too few words are two shows: not even queued
            key = pair_key(ko, latin)
            decision, _decided_at = decisions.get(key, ("", ""))
            if decision == "same":
                continue  # folded in step 1
            if decision == "different":
                result.kept_apart += 1
                continue
            result.pending += 1
            if key not in known and queue is not None:
                queue_rows.append(_queue_item(ledger_id, key, ko, latin, now))
                known.add(key)
                result.queued += 1
    return result
