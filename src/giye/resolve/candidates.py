# SPDX-License-Identifier: AGPL-3.0-only
"""Candidate pairs and the review queue.

X1. A Hangul personal name and a Latin-only row are a candidate when their
name keys intersect (``LanguageModule.name_keys``; the Korean-English module
wraps ``hangul_name_keys`` and ``latin_name_keys``). A candidate is merged
only when E1–E4 also hold; the evidence is prefixed ``X1+``. With no evidence
the pair is queued (``possible_same_person``), never merged on the spelling.

A Korean row that already has a Latin name is paired whether or not that
Latin spelling meets the other row's. When it meets, the queue detail says
``rule=x1_own_en``. Why: the pair was
skipped on the assumption that A2 had joined it, but A2 needs the same Latin
tokens (``Do-yun Lee`` is not ``Doyun Lee``) and does not join a Latin-only
personal name across programmes, so the most likely duplicates were neither
attached nor queued. Either row being team-like drops the pair. A collector identity key that pins them apart drops
the pair. Sharing a frame code drops the pair.

Same-script exact names (spaces removed, case folded) that no rule accepts, that
are not pinned apart, that are not a team paired with a person, and that do not
share a frame, are queued the same way. Similarity is not a merge.

A person's decision on a ``possible_same_person`` pair stays decided. Distinct
(``decided=different``) and dismiss (``decided=dismissed`` or status
``dismissed``) cover the unordered pair after merges: a row that absorbed
either id is the same pair. A later run does not open a second item. An E-rule
that did not fire at decision time reopens that same item and records the rule.
It does not merge over the decision.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from giye.resolve.teams import person_like, team_person_mismatch

if TYPE_CHECKING:  # pragma: no cover
    from giye.normalize.language import LanguageModule

_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
_LEDGER_ID = re.compile(r"(?:CAND|LED)-[0-9A-Za-z]+")
_MERGED_ID = re.compile(r"^merged\s+((?:LED|CAND)-[0-9A-Za-z]+)$")
_DECISION = re.compile(r"decided=(same|different)\b")
_SPLIT = re.compile(r"decided=(different|dismissed)\b")
_EVIDENCE_AT = re.compile(r"evidence_at_decision=([A-Za-z0-9+|]+)")
_RULE_ID = re.compile(r"E[1-4]")
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
    """Collector prefix of each identity key, so two keys from one collector can be compared."""
    return {item.split(":", 1)[0]: item for item in keys}


def display_name(row: dict) -> str:
    """``name_ko`` when set, otherwise ``name_en``."""
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


def x1_candidates(artists: list[dict], language: LanguageModule | None = None) -> list[tuple[str, str]]:
    """``(korean_ledger_id, english_only_ledger_id)`` pairs whose romanization keys meet (X1).

    English-only: ``name_ko`` has no Hangul, ``name_en`` has Latin letters, and
    ``language.name_keys(name_en)`` is non-empty. Keys come from ``language``
    (the configured default when None).
    The ledger copies an English-only name into ``name_ko``, so that row is still
    English-only when ``name_ko`` has no Hangul. A Korean row that also has a Latin
    name is paired too (:func:`x1_own_en` tells the two cases apart).
    """
    if language is None:
        # Imported here: the language module imports giye.resolve.names, whose package imports this file.
        from giye.normalize.language import default_language

        language = default_language()
    index: dict[str, list[str]] = defaultdict(list)
    en_keys: dict[str, set[str]] = {}
    ko_rows = []
    for row in artists:
        ko = row.get("name_ko") or ""
        en = row.get("name_en") or ""
        lid = row["ledger_id"]
        # personal_name is also true for a Latin personal name. That row is the
        # English side of X1, not the Hangul side. Hangul is what makes a row Korean.
        if _HANGUL.search(ko) and person_like(ko, language):
            ko_rows.append(row)
        elif not _HANGUL.search(ko):
            # The English side is a name written in Latin letters. A name with
            # none has no Latin keys; checking first keeps a Hangul-only
            # name_en from taking Hangul keys through the language module.
            if not _LATIN.search(en):
                continue
            keys = language.name_keys(en)
            if not keys:
                continue
            en_keys[lid] = keys
            for key in keys:
                index[key].append(lid)
    pairs: dict[tuple[str, str], tuple[str, str]] = {}
    for row in ko_rows:
        ko = row["name_ko"]
        # Keys come from the language module (``name_keys``). For the Korean-English
        # module a bare Hangul personal name gives its Hangul keys, and a name
        # with Latin letters gives its Latin keys, as X1 compares them.
        hkeys = language.name_keys(ko)
        if not hkeys:
            continue
        hits: set[str] = set()
        for key in hkeys:
            hits.update(index.get(key, ()))
        for eid in hits:
            if eid == row["ledger_id"]:
                continue
            ordered = tuple(sorted((row["ledger_id"], eid)))
            pairs[ordered] = (row["ledger_id"], eid)
    return [pairs[key] for key in sorted(pairs)]


def review_id_set(item: dict) -> set[str]:
    """Ledger ids a queue item names: its own id and every id written in the detail."""
    return {item.get("ledger_id") or "", *_LEDGER_ID.findall(item.get("detail") or "")} - {""}


def absorption_map(artists: list[dict]) -> dict[str, str]:
    """Dropped ledger id → the living row that absorbed it, chains followed.

    The kept row's note records ``merged <ledger id>`` for each row it absorbed
    directly. ``_move_review`` already rewrites a queue item's own ``ledger_id``
    along a chain; this map is what follows an id that appears only in the detail.
    """
    parent: dict[str, str] = {}
    for survivor, dropped in merged_drop_ids(artists).items():
        for item in dropped:
            parent[item] = survivor

    def canon(item: str) -> str:
        """Follow ``merged`` links to the living id. A cycle stops at the id already seen."""
        seen: set[str] = set()
        while item in parent and item not in seen:
            seen.add(item)
            item = parent[item]
        return item

    return {key: canon(key) for key in parent}


def _canon(item: str, absorbed: dict[str, str]) -> str:
    """Living ledger id at the end of a merge chain, or ``item`` when nothing absorbed it."""
    return absorbed.get(item, item) if item else ""


def point_review_at_survivors(review: list[dict], artists: list[dict]) -> bool:
    """Replace a dropped ledger id in a queue detail with the row that absorbed it.

    The item stays one item. After the rewrite it names the living pair, so a
    later pass does not open a second item for the survivor.
    """
    absorbed = absorption_map(artists)
    if not absorbed:
        return False
    changed = False
    for item in review:
        detail = item.get("detail") or ""
        updated = detail
        for dropped, survivor in absorbed.items():
            if not dropped or dropped == survivor or survivor in updated:
                continue
            token = re.compile(rf"(?<![0-9A-Za-z]){re.escape(dropped)}(?![0-9A-Za-z])")
            if not token.search(updated):
                continue
            updated = token.sub(survivor, updated)
        if updated != detail:
            item["detail"] = updated
            changed = True
    return changed


def survivor_pair(left: str, right: str, artists: list[dict]) -> tuple[str, str]:
    """Unordered pair after following merges. An id with no survivor stays itself."""
    absorbed = absorption_map(artists)
    return tuple(sorted((_canon(left, absorbed), _canon(right, absorbed))))


def queue_pair_keys(items: list[dict], artists: list[dict] | None = None) -> set[tuple[str, str]]:
    """Unordered pairs already named by a ``possible_same_person`` item, any status.

    When ``artists`` is given, a dropped id is read as the row that absorbed it,
    so a decision on A–B also covers the survivor of A or of B.
    """
    absorbed = absorption_map(artists or [])
    keys: set[tuple[str, str]] = set()
    for item in items:
        if item.get("reason") != "possible_same_person":
            continue
        lid = _canon(item.get("ledger_id") or "", absorbed)
        others = {_canon(other, absorbed) for other in _LEDGER_ID.findall(item.get("detail") or "")} - {lid, ""}
        for other in others:
            if other and other != lid:
                keys.add(tuple(sorted((lid, other))))
    return keys


def evidence_rule_id(evidence: str | None) -> str:
    """``E1`` … ``E4`` from an evidence string, ignoring an ``X1+`` prefix."""
    token = (evidence or "").split(" ", 1)[0].removeprefix("X1+")
    return token if _RULE_ID.fullmatch(token) else ""


def rules_at_decision(detail: str) -> set[str]:
    """E-rules recorded when a person decided. Missing marker and ``none`` are empty."""
    match = _EVIDENCE_AT.search(detail or "")
    if not match or match.group(1) == "none":
        return set()
    return {part for part in match.group(1).split("+") if _RULE_ID.fullmatch(part)}


def set_evidence_snapshot(detail: str, rules: set[str]) -> str:
    """Record which E-rules held when the person decided. ``none`` when nothing did."""
    token = "+".join(sorted(rules)) if rules else "none"
    marker = f"evidence_at_decision={token}"
    if _EVIDENCE_AT.search(detail or ""):
        return _EVIDENCE_AT.sub(marker, detail or "")
    return f"{detail}; {marker}".strip("; ")


def _reopen_new_evidence(item: dict, rule: str) -> bool:
    """Reopen one decided item because ``rule`` did not fire at decision time.

    The decision markers stay, so a later run still will not merge the pair or
    open a second item. Returns whether the row changed.
    """
    detail = item.get("detail") or ""
    marker = f"reopened=new_evidence ({rule})"
    changed = False
    if marker not in detail:
        sentence = f"{marker}: evidence rule {rule} did not fire at decision time"
        item["detail"] = f"{detail}; {sentence}".strip("; ")
        changed = True
    if item.get("status") != "open":
        item["status"] = "open"
        changed = True
    return changed


def is_human_split(item: dict) -> bool:
    """Distinct or dismiss. ``decided=same`` is a merge, not a split."""
    if item.get("reason") != "possible_same_person":
        return False
    detail = item.get("detail") or ""
    if _SPLIT.search(detail):
        return True
    return item.get("status") == "dismissed"


def decision_blocks(
    review: list[dict],
    artists: list[dict],
    left: str,
    right: str,
    evidence: str | None,
) -> tuple[bool, list[str]]:
    """Whether a human split covers this pair, and queue ids reopened for new evidence.

    Mutates ``review`` when an E-rule fires that was not recorded at decision
    time. The pair is still blocked: new evidence reopens the item, it does not
    merge and it does not open a second item.
    """
    pair = survivor_pair(left, right, artists)
    if not pair[0] or not pair[1] or pair[0] == pair[1]:
        return False, []
    known = queue_pair_keys(review, artists)
    if pair not in known:
        return False, []
    rule = evidence_rule_id(evidence)
    blocked = False
    reopened: list[str] = []
    absorbed = absorption_map(artists)
    for item in review:
        if not is_human_split(item):
            continue
        lid = _canon(item.get("ledger_id") or "", absorbed)
        others = {_canon(other, absorbed) for other in _LEDGER_ID.findall(item.get("detail") or "")} - {lid, ""}
        if pair not in {tuple(sorted((lid, other))) for other in others if other and other != lid}:
            continue
        blocked = True
        if not rule or rule in rules_at_decision(item.get("detail") or ""):
            continue
        if _reopen_new_evidence(item, rule):
            reopened.append(item.get("queue_id") or "")
    return blocked, [item for item in reopened if item]


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
    """Queue detail for an exact same-script name that no evidence rule accepted."""
    return f"{left} shares a name with {right} (rule=same_script_exact)"


def x1_own_en(ko_row: dict, en_row: dict, language: LanguageModule | None = None) -> bool:
    """The Korean row's own Latin name has keys that meet the Latin-only row's keys."""
    en = ko_row.get("name_en") or ""
    if not _LATIN.search(en):
        return False
    if language is None:
        from giye.normalize.language import default_language

        language = default_language()
    other = en_row.get("name_en") or en_row.get("name_ko") or ""
    return bool(language.name_keys(en) & language.name_keys(other))


def x1_detail(ko_row: dict, en_row: dict, language: LanguageModule | None = None) -> str:
    """Queue detail for an X1 romanisation pair that E1–E4 did not merge.

    ``(rule=x1_own_en)`` marks a pair whose Korean row already carries a Latin
    name that meets the other row's, so those pairs can be counted.
    """
    detail = f"romanization match: {display_name(ko_row)} ~ {display_name(en_row)} ({en_row['ledger_id']})"
    if x1_own_en(ko_row, en_row, language):
        detail = f"{detail} (rule=x1_own_en)"
    return detail


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
    evidence_of: Callable[[str, str], str | None],
    *,
    words: re.Pattern[str] | None = None,
    language: LanguageModule | None = None,
) -> list[dict]:
    """Open a review item for an exact-name pair no rule has decided.

    Skipped: team paired with a person (T1), pinned apart, the same frame,
    a pair E1–E4 already accepts, and a pair an existing item already names.
    """
    by_id = {row["ledger_id"]: row for row in artists}
    known = queue_pair_keys(review, artists)
    items: list[dict] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for left, right, _script in same_script_pairs(artists):
        pair = tuple(sorted((left, right)))
        if pair in known:
            continue
        row_a, row_b = by_id[pair[0]], by_id[pair[1]]
        if team_person_mismatch(row_a, row_b, words=words, language=language):
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
