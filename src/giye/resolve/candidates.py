# SPDX-License-Identifier: AGPL-3.0-only
"""Candidate pairs and the review queue.

X1. A Hangul personal name and a Latin-only row are a candidate when
``hangul_name_keys`` and ``latin_name_keys`` intersect. A candidate is merged
only when E1–E4 also hold; the evidence is prefixed ``X1+``. With no evidence
the pair is queued (``possible_same_person``), never merged on the spelling.

A Korean row that already has a Latin name is paired only when that Latin
spelling does not meet the other row's (different spelling). Either row being
team-like drops the pair. A collector identity key that pins them apart drops
the pair. Sharing a frame code drops the pair.

Same-script exact names (spaces removed, case folded) that no rule accepts, that
are not pinned apart, that are not a team paired with a person, and that do not
share a frame, are queued the same way. Similarity is not a merge.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from giye.resolve.names import hangul_name_keys, latin_name_keys
from giye.resolve.teams import person_like, team_person_mismatch

_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
_LEDGER_ID = re.compile(r"(?:CAND|LED)-[0-9A-Za-z]+")
_MERGED_ID = re.compile(r"^merged\s+((?:LED|CAND)-[0-9A-Za-z]+)$")
_DECISION = re.compile(r"decided=(same|different)\b")
_REOPEN = "reopened=wrong_close"
_REOPEN_WHY = {
    "closed_by_another_merge": (
        "the pair was not merged; the item was closed because the kept id appeared "
        "in the detail when a different person was merged into this row"
    ),
    "closed_with_a_survivor_still_listed": (
        "the pair was not merged; the detail still names a living row that was not merged into this one"
    ),
    "done_pair_not_merged": (
        "the pair was not merged; both rows are still in the ledger and no decision was recorded"
    ),
}


def identity_keys(note: str) -> list[str]:
    """``identity=<collector>:<key>`` markers a collector used to pin two rows apart."""
    return sorted(part.strip() for part in (note or "").split(";") if part.strip().startswith("identity="))


def pinned_apart(left: list[str], right: list[str]) -> bool:
    """Same collector, different identity key. The collector already split the rows."""
    left_ns = _identity_namespace(left)
    right_ns = _identity_namespace(right)
    return any(left_ns[key] != right_ns[key] for key in set(left_ns) & set(right_ns))


def _identity_namespace(keys: list[str]) -> dict[str, str]:
    return {item.split(":", 1)[0]: item for item in keys}


def display_name(row: dict) -> str:
    return (row.get("name_ko") or row.get("name_en") or "").strip()


def normalised_full_name(value: str) -> str:
    """Full name for exact equality: NFC, whitespace removed, case folded.

    Hyphens stay. This is not a romanisation key.
    """
    text = unicodedata.normalize("NFC", value or "")
    text = re.sub(r"\s+", "", text)
    return text.casefold()


def primary_name(row: dict) -> str:
    """The name the same-name check uses: ``name_ko``, else ``name_en``."""
    return (row.get("name_ko") or row.get("name_en") or "").strip()


def script_of(value: str) -> str:
    """``hangul``, ``latin``, or empty when the string has neither."""
    if _HANGUL.search(value or ""):
        return "hangul"
    if _LATIN.search(value or ""):
        return "latin"
    return ""


def same_script_pairs(artists: list[dict]) -> list[tuple[str, str, str]]:
    """Unordered pairs whose primary names are the same script and the same full name."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in artists:
        raw = primary_name(row)
        script = script_of(raw)
        norm = normalised_full_name(raw)
        if not script or not norm:
            continue
        groups[(script, norm)].append(row["ledger_id"])
    pairs: list[tuple[str, str, str]] = []
    for (script, _norm), ids in groups.items():
        unique = sorted(set(ids))
        if len(unique) < 2:
            continue
        for index, left in enumerate(unique):
            for right in unique[index + 1 :]:
                pairs.append((left, right, script))
    pairs.sort()
    return pairs


def x1_candidates(artists: list[dict]) -> list[tuple[str, str]]:
    """``(korean_ledger_id, english_only_ledger_id)`` pairs whose romanization keys meet (X1).

    English-only: ``name_ko`` has no Hangul and ``latin_name_keys(name_en)`` is non-empty.
    The ledger copies an English-only name into ``name_ko``, so that row is still
    English-only when ``name_ko`` has no Hangul. A Korean row that also has a Latin
    name is paired only when the two English keys do not meet.
    """
    index: dict[str, list[str]] = defaultdict(list)
    en_keys: dict[str, set[str]] = {}
    ko_rows = []
    for row in artists:
        ko = row.get("name_ko") or ""
        en = row.get("name_en") or ""
        lid = row["ledger_id"]
        if person_like(ko):
            ko_rows.append(row)
        elif not _HANGUL.search(ko):
            keys = latin_name_keys(en)
            if not keys:
                continue
            en_keys[lid] = keys
            for key in keys:
                index[key].append(lid)
    pairs: dict[tuple[str, str], tuple[str, str]] = {}
    for row in ko_rows:
        ko = row["name_ko"]
        en = row.get("name_en") or ""
        latin = bool(_LATIN.search(en))
        hkeys = hangul_name_keys(ko)
        if not hkeys:
            continue
        own = latin_name_keys(en) if latin else set()
        hits: set[str] = set()
        for key in hkeys:
            hits.update(index.get(key, ()))
        for eid in hits:
            if eid == row["ledger_id"]:
                continue
            if latin and (own & en_keys[eid]):
                continue
            ordered = tuple(sorted((row["ledger_id"], eid)))
            pairs[ordered] = (row["ledger_id"], eid)
    return [pairs[key] for key in sorted(pairs)]


def review_id_set(item: dict) -> set[str]:
    return {item.get("ledger_id") or "", *_LEDGER_ID.findall(item.get("detail") or "")} - {""}


def queue_pair_keys(items: list[dict]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for item in items:
        if item.get("reason") != "possible_same_person":
            continue
        lid = item.get("ledger_id") or ""
        others = set(_LEDGER_ID.findall(item.get("detail") or "")) - {lid}
        for other in others:
            keys.add(tuple(sorted((lid, other))))
    return keys


def new_queue_item(ledger_id: str, detail: str, *, now: str | None = None) -> dict[str, str]:
    """An open ``possible_same_person`` item. The detail names the pair and why it is open."""
    return {
        "queue_id": str(uuid.uuid4()),
        "ledger_id": ledger_id,
        "reason": "possible_same_person",
        "detail": detail,
        "status": "open",
        "created_at": now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def same_script_detail(left: str, right: str) -> str:
    return f"{left} shares a name with {right} (rule=same_script_exact)"


def x1_detail(ko_row: dict, en_row: dict) -> str:
    return f"romanization match: {display_name(ko_row)} ~ {display_name(en_row)} ({en_row['ledger_id']})"


def explicit_decision(detail: str) -> str:
    """``same`` or ``different`` when a person closed the item, else empty."""
    match = _DECISION.search(detail or "")
    return match.group(1) if match else ""


def merged_drop_ids(artists: list[dict]) -> dict[str, set[str]]:
    """Ledger ids named by a ``merged <id>`` marker on each surviving row."""
    drops: dict[str, set[str]] = defaultdict(set)
    for row in artists:
        for part in (piece.strip() for piece in (row.get("reviewer_note") or "").split(";")):
            match = _MERGED_ID.match(part)
            if match:
                drops[row["ledger_id"]].add(match.group(1))
    return drops


def wrong_close_reason(item: dict, artist_ids: set[str], drops_of: dict[str, set[str]]) -> str:
    """A done item whose pair was not merged and that nobody explicitly decided."""
    if item.get("reason") != "possible_same_person" or item.get("status") != "done":
        return ""
    detail = item.get("detail") or ""
    if explicit_decision(detail):
        return ""
    lid = item.get("ledger_id") or ""
    others = set(_LEDGER_ID.findall(detail)) - {lid}
    if not others:
        return ""
    drops = drops_of.get(lid, set())
    living_unmerged = [other for other in others if other in artist_ids and other not in drops]
    in_note = [other for other in others if other in drops]
    absent = [other for other in others if other not in artist_ids and other not in drops]
    if living_unmerged and not in_note and not absent:
        return "closed_by_another_merge" if lid in detail else "done_pair_not_merged"
    if living_unmerged:
        return "closed_with_a_survivor_still_listed"
    return ""


def reopen_wrongly_closed(review: list[dict], artists: list[dict]) -> list[str]:
    """Set status back to open when a merge closed an item without joining its pair.

    Queue ids are kept. The detail gains one ``reopened=wrong_close`` sentence.
    Returns the queue ids that were reopened.
    """
    artist_ids = {row["ledger_id"] for row in artists}
    drops_of = merged_drop_ids(artists)
    opened: list[str] = []
    for item in review:
        reason = wrong_close_reason(item, artist_ids, drops_of)
        if not reason:
            continue
        detail = item.get("detail") or ""
        if _REOPEN not in detail:
            item["detail"] = f"{detail}; {_REOPEN} ({reason}): {_REOPEN_WHY[reason]}".strip("; ")
        item["status"] = "open"
        opened.append(item["queue_id"])
    return opened


def undecided_same_script(
    artists: list[dict],
    review: list[dict],
    frames: dict[str, set[str]],
    identities: dict[str, list[str]],
    evidence_of,
    *,
    words: re.Pattern[str] | None = None,
) -> list[dict]:
    """Open a review item for an exact-name pair no rule has decided.

    Skipped: team paired with a person (T1), pinned apart, the same frame,
    a pair E1–E4 already accepts, and a pair an existing item already names.
    """
    by_id = {row["ledger_id"]: row for row in artists}
    known = queue_pair_keys(review)
    items: list[dict] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for left, right, _script in same_script_pairs(artists):
        pair = tuple(sorted((left, right)))
        if pair in known:
            continue
        row_a, row_b = by_id[pair[0]], by_id[pair[1]]
        if team_person_mismatch(row_a, row_b, words=words):
            continue
        if pinned_apart(identities.get(pair[0], []), identities.get(pair[1], [])):
            continue
        if frames.get(pair[0], set()) & frames.get(pair[1], set()):
            continue
        if evidence_of(pair[0], pair[1]):
            continue
        items.append(new_queue_item(pair[0], same_script_detail(pair[0], pair[1]), now=now))
        known.add(pair)
    return items
