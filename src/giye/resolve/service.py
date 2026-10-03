# SPDX-License-Identifier: MIT
"""Run same-person resolution against a ledger.

Order, matching production ``resolve_same_person.py``:

1. Expand team members onto their own rows (the team stays; T1).
2. E1: rows that share a website and an overlapping name, unless one side is a
   team and the other is a person.
3. E2–E4 on same-script exact-name pairs that are not pinned apart and not a
   team/person mismatch. Both rows must be on a roster.
4. X1 candidates. E1–E4 still have to hold. The evidence is prefixed ``X1+``.
   No evidence queues the pair. A shared frame, a team row, or a pin drops it.
5. Reopen review items a merge closed without joining that pair.
6. Queue same-script pairs no rule decided.

Production's live loop merged E2–E4 only when an open review item already named
the pair, because collectors had queued it. This collector does not, so a pair
the evidence rules accept is merged here. The evidence, the guards, and the
year window are unchanged. Nothing is merged on a shared name alone.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from giye.config import Config
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import split_pipe
from giye.resolve.candidates import (
    identity_keys,
    new_queue_item,
    pinned_apart,
    reopen_wrongly_closed,
    review_id_set,
    same_script_pairs,
    undecided_same_script,
    x1_candidates,
    x1_detail,
)
from giye.resolve.cv import load_cv_activities, move_extract_file
from giye.resolve.evidence import (
    evidence_e1,
    evidence_e2_e4,
    pattern_table,
    rule_of,
    url_key,
    website_keys,
)
from giye.resolve.teams import expand_teams, team_like, team_person_mismatch


@dataclass
class MergeRecord:
    kept: str
    dropped: str
    evidence: str
    rule: str


@dataclass
class ResolveResult:
    merges: list[MergeRecord] = field(default_factory=list)
    queued: list[dict] = field(default_factory=list)
    blocked_team: list[tuple[str, str]] = field(default_factory=list)
    expanded: list[str] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)


def resolve(config: Config, *, dry_run: bool = False) -> ResolveResult:
    """Resolve the ledger in ``config``. ``dry_run`` decides and writes nothing."""
    ledger = Ledger.open(config)
    return resolve_ledger(ledger, dry_run=dry_run)


def resolve_ledger(ledger: Ledger, *, dry_run: bool = False) -> ResolveResult:
    result = ResolveResult()
    result.expanded = expand_teams(ledger, dry_run=dry_run)
    state = _State(ledger)
    _merge_by_website(state, result, dry_run=dry_run)
    _merge_same_script(state, result, dry_run=dry_run)
    _merge_or_queue_x1(state, result, dry_run=dry_run)
    if not dry_run:
        state.reload()
    review = ledger.read("review_queue") if ledger.path("review_queue").exists() else []
    result.reopened = reopen_wrongly_closed(review, state.artists)
    queued = undecided_same_script(
        state.artists,
        review,
        state.frames,
        state.identities,
        lambda left, right: _evidence(state, left, right),
    )
    # T1 blocks that the queue skips are still part of the result.
    _record_same_script_blocks(state, result)
    result.queued.extend(queued)
    if not dry_run and (result.reopened or queued):
        review.extend(queued)
        ledger.write("review_queue", review, task="resolve")
    result.blocked_team = sorted(set(result.blocked_team))
    return result


class _State:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.patterns = pattern_table(ledger.config.event_patterns)
        self.cvs = load_cv_activities(ledger, ledger.config)
        self.artists: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.rows_of: dict[str, list[dict]] = {}
        self.acts: Counter[str] = Counter()
        self.frames: dict[str, set[str]] = {}
        self.identities: dict[str, list[str]] = {}
        self.sites: dict[str, set[str]] = {}
        self.links: list[dict] = []
        self.reload()

    def reload(self) -> None:
        self.artists = self.ledger.read("artists")
        self.by_id = {row["ledger_id"]: row for row in self.artists}
        rows_of: dict[str, list[dict]] = defaultdict(list)
        for row in self.ledger.read("activities"):
            rows_of[row["ledger_id"]].append(row)
        self.rows_of = rows_of
        self.acts = Counter({key: len(value) for key, value in rows_of.items()})
        frames: dict[str, set[str]] = defaultdict(set)
        for row in self.ledger.read("frame_membership"):
            frames[row["ledger_id"]].add(row["frame_code"])
        self.frames = frames
        identities: dict[str, list[str]] = {}
        for row in self.artists:
            keys = identity_keys(row.get("reviewer_note") or "")
            if keys:
                identities[row["ledger_id"]] = keys
        self.identities = identities
        self.links = self.ledger.read("links") if self.ledger.path("links").exists() else []
        self.sites = website_keys(self.links)

    def transfer_cv(self, keep: str, drop: str) -> None:
        move_extract_file(self.ledger.config, keep, drop)
        if drop in self.cvs and keep not in self.cvs:
            self.cvs[keep] = self.cvs.pop(drop)
        else:
            self.cvs.pop(drop, None)


def _evidence(state: _State, left: str, right: str) -> str | None:
    return evidence_e1(left, right, state.sites) or evidence_e2_e4(
        left, right, state.frames, state.rows_of, state.cvs, state.patterns
    )


def _keep_drop(state: _State, left: str, right: str) -> tuple[str, str]:
    """More activities wins. A tie keeps ``left`` (callers pass the preferred id first)."""
    if state.acts[left] >= state.acts[right]:
        return left, right
    return right, left


def _apply(
    state: _State,
    result: ResolveResult,
    keep: str,
    drops: list[str],
    evidence: str,
    *,
    dry_run: bool,
) -> None:
    drops = [drop for drop in drops if drop in state.by_id and drop != keep]
    if keep not in state.by_id or not drops:
        return
    rule = rule_of(evidence)
    for drop in drops:
        result.merges.append(MergeRecord(kept=keep, dropped=drop, evidence=evidence, rule=rule))
    if dry_run:
        for drop in drops:
            state.by_id.pop(drop, None)
        gone = set(drops)
        state.artists = [row for row in state.artists if row["ledger_id"] not in gone]
        return
    for drop in drops:
        state.transfer_cv(keep, drop)
    state.ledger.merge(keep, drops, evidence=evidence, rule=rule)
    state.reload()


def _names(artist: dict) -> set[str]:
    values = [artist.get("name_ko") or "", artist.get("name_en") or "", *split_pipe(artist.get("aliases"))]
    found = set()
    for value in values:
        if not value:
            continue
        hangul = "".join(ch for ch in value if "가" <= ch <= "힣")
        found.add(hangul or value.lower())
    return found


def _merge_by_website(state: _State, result: ResolveResult, *, dry_run: bool) -> None:
    """E1. One website group, then reload, matching production's restart after each write."""
    for _ in range(len(state.artists) + 1):
        owners: dict[str, set[str]] = defaultdict(set)
        live = set(state.by_id)
        for link in state.links:
            if link.get("link_type") == "social" or link.get("ledger_id") not in live:
                continue
            key = url_key(link.get("url") or "")
            if key:
                owners[key].add(link["ledger_id"])
        chosen: tuple[str, str, list[str]] | None = None
        for key, ids in owners.items():
            pending = sorted(ids)
            if len(pending) < 2:
                continue
            groups: list[list[str]] = []
            for lid in pending:
                if lid not in state.by_id:
                    continue
                placed = False
                for group in groups:
                    person = state.by_id[lid]
                    head = state.by_id[group[0]]
                    if bool(team_like(person)) != bool(team_like(head)):
                        if _names(person) & _names(head):
                            result.blocked_team.append(tuple(sorted((lid, group[0]))))
                        continue
                    if _names(person) & set().union(*(_names(state.by_id[other]) for other in group)):
                        group.append(lid)
                        placed = True
                        break
                if not placed:
                    groups.append([lid])
            for group in groups:
                if len(group) < 2:
                    continue
                keep = max(group, key=lambda item: (state.acts[item],))
                # max() keeps the first row when the counts tie. Ids were sorted, and the
                # earliest id seeded the group, so a tie keeps that earlier id.
                drops = [item for item in group if item != keep]
                chosen = (key, keep, drops)
                break
            if chosen:
                break
        if chosen is None:
            return
        key, keep, drops = chosen
        _apply(state, result, keep, drops, f"E1 same website {key}", dry_run=dry_run)


def _merge_same_script(state: _State, result: ResolveResult, *, dry_run: bool) -> None:
    """E2–E4 for an exact same-script name. T1 and a collector pin block the merge."""
    guard = len(state.artists) + 1
    while guard:
        guard -= 1
        if not dry_run:
            state.reload()
        progressed = False
        for left, right, _script in same_script_pairs(state.artists):
            if left not in state.by_id or right not in state.by_id:
                continue
            if left not in state.frames or right not in state.frames:
                continue
            row_a, row_b = state.by_id[left], state.by_id[right]
            if team_person_mismatch(row_a, row_b):
                result.blocked_team.append(tuple(sorted((left, right))))
                continue
            if pinned_apart(state.identities.get(left, []), state.identities.get(right, [])):
                continue
            evidence = _evidence(state, left, right)
            if not evidence:
                continue
            keep, drop = _keep_drop(state, left, right)
            _apply(state, result, keep, [drop], evidence, dry_run=dry_run)
            progressed = True
            if not dry_run:
                break
        if not progressed or dry_run:
            return


def _merge_or_queue_x1(state: _State, result: ResolveResult, *, dry_run: bool) -> None:
    """X1 waives only the same-name check. E1–E4 still decide. Otherwise queue."""
    if not dry_run:
        state.reload()
    review = state.ledger.read("review_queue") if state.ledger.path("review_queue").exists() else []
    open_sets = [review_id_set(item) for item in review if item.get("status") == "open"]
    fresh: list[dict] = []
    for ko_id, en_id in x1_candidates(state.artists):
        if ko_id not in state.by_id or en_id not in state.by_id:
            continue
        pair = (state.by_id[ko_id], state.by_id[en_id])
        if team_like(pair[0]) or team_like(pair[1]):
            result.blocked_team.append(tuple(sorted((ko_id, en_id))))
            continue
        if pinned_apart(state.identities.get(ko_id, []), state.identities.get(en_id, [])):
            continue
        if state.frames.get(ko_id, set()) & state.frames.get(en_id, set()):
            continue
        evidence = _evidence(state, ko_id, en_id)
        if evidence:
            evidence = f"X1+{evidence}"
            keep, drop = _keep_drop(state, ko_id, en_id)
            _apply(state, result, keep, [drop], evidence, dry_run=dry_run)
            continue
        if any({ko_id, en_id} <= ids for ids in open_sets):
            continue
        if any({ko_id, en_id} <= review_id_set(item) for item in fresh):
            continue
        item = new_queue_item(ko_id, x1_detail(state.by_id[ko_id], state.by_id[en_id]))
        fresh.append(item)
        open_sets.append({ko_id, en_id})
    result.queued.extend(fresh)
    if fresh and not dry_run:
        review = state.ledger.read("review_queue") if state.ledger.path("review_queue").exists() else []
        # A merge above may have closed items. Append only the new X1 rows.
        review.extend(fresh)
        state.ledger.write("review_queue", review, task="resolve")


def _record_same_script_blocks(state: _State, result: ResolveResult) -> None:
    for left, right, _script in same_script_pairs(state.artists):
        if left not in state.by_id or right not in state.by_id:
            continue
        if team_person_mismatch(state.by_id[left], state.by_id[right]):
            result.blocked_team.append(tuple(sorted((left, right))))
