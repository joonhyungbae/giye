# SPDX-License-Identifier: AGPL-3.0-only
"""Run same-person resolution against a ledger.

Order (see docs/RULES.md):

1. Expand team members onto their own rows (the team stays; T1).
2. E1: rows that share a website and an overlapping name, unless one side is a
   team and the other is a person.
3. E2–E4 on same-script exact-name pairs that are not pinned apart and not a
   team/person mismatch. Both rows must be on a roster.
4. X1 candidates. E1–E4 still have to hold. The evidence is prefixed ``X1+``.
   No evidence queues the pair. A shared frame, a team row, or a pin drops it.
   A pair a person already decided (distinct or dismiss) is not queued again.
   The ids follow merges. An E-rule that did not fire at decision time reopens
   that item and is recorded; it does not merge over the decision.
5. Reopen review items a merge closed without joining that pair.
6. Queue same-script pairs no rule decided.

E2–E4 merge a pair the evidence rules accept, including a pair no open review
item has named yet. Collectors here do not pre-queue those pairs. The
evidence, the guards, and the year window are the rules in ``giye.resolve.evidence``.
Nothing is merged on a shared name alone.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from giye.config import Config
from giye.ledger.ledger import Ledger, hidden_ids
from giye.ledger.schemas import split_pipe
from giye.resolve.candidates import (
    decision_blocks,
    identity_keys,
    new_queue_item,
    pinned_apart,
    point_review_at_survivors,
    queue_pair_keys,
    reopen_wrongly_closed,
    same_script_pairs,
    survivor_pair,
    undecided_same_script,
    x1_candidates,
    x1_detail,
)
from giye.resolve.cv import fold_merged_cvs, load_cv_activities
from giye.resolve.evidence import (
    evidence_e1,
    evidence_e2_e4,
    generic_titles,
    pattern_table,
    rule_of,
    url_key,
    website_keys,
)
from giye.resolve.teams import expand_teams, team_like, team_person_mismatch


@dataclass
class MergeRecord:
    """One merge: the surviving ledger id, the retired id, the evidence sentence, and the rule token."""

    kept: str
    dropped: str
    evidence: str
    rule: str


@dataclass
class ResolveResult:
    """What one resolve run did. ``dry_run`` still fills these lists and writes nothing."""

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
    """Resolve ``ledger`` in the order in the module docstring. Returns the merges, the queue, and the blocks."""
    result = ResolveResult()
    result.expanded = expand_teams(ledger, dry_run=dry_run)
    state = _State(ledger)
    _merge_by_website(state, result, dry_run=dry_run)
    _merge_same_script(state, result, dry_run=dry_run)
    _merge_or_queue_x1(state, result, dry_run=dry_run)
    if not dry_run:
        state.reload()
    review = ledger.read("review_queue") if ledger.path("review_queue").exists() else []
    result.reopened.extend(reopen_wrongly_closed(review, state.artists))
    queued = undecided_same_script(
        state.artists,
        review,
        state.frames,
        state.identities,
        lambda left, right: _evidence(state, left, right),
        words=state.team_words,
        language=state.language,
    )
    # T1 blocks that the queue skips are still part of the result.
    _record_same_script_blocks(state, result)
    result.queued.extend(queued)
    if not dry_run and (result.reopened or queued):
        review.extend(queued)
        ledger.write("review_queue", review, task="resolve")
    result.blocked_team = sorted(set(result.blocked_team))
    # Extract applied each CV while the records were still two people. Apply
    # again now that the sources share an owner, so a repeated line and a CV
    # line that only restates the other roster row are folded before publish.
    if not dry_run and result.merges:
        fold_merged_cvs(ledger)
    return result


class _State:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.patterns = pattern_table(ledger.config.field_config.event_patterns, ledger.config.event_patterns)
        self.team_prefix = ledger.config.field_config.team_prefix or "팀:"
        self.team_words = ledger.config.field_config.compiled_team_words()
        # Imported here: giye.normalize.language imports giye.resolve.names, and this package's
        # __init__ imports this module.
        from giye.normalize.language import language_for

        self.language = language_for(ledger.config)
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
        """Re-read artists, activities, membership, identities, and links after a write."""
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
        # E3: titles too many records use to identify one work, over the whole ledger.
        # A language module may also list titles that name no work whatever their count.
        listed = getattr(self.language, "generic_titles", frozenset())
        self.generic = generic_titles(self.rows_of, self.cvs, self.ledger.config.generic_title_records) | listed

    def transfer_cv(self, keep: str, drop: str) -> None:
        """Keep both activity lists on the survivor for the rest of this run.

        The extraction file is not renamed: the merge moves ``cv_sources`` to
        the survivor, and a later run reads ownership from there
        (``giye.resolve.cv``).
        """
        # Both readings stay on the survivor. Dropping the second list would
        # hide a CV line from a later pair in this same run.
        dropped = self.cvs.pop(drop, None)
        if not dropped:
            return
        if keep not in self.cvs:
            self.cvs[keep] = dropped
        else:
            self.cvs[keep] = [*self.cvs[keep], *dropped]


def evidence_for_pair(ledger: Ledger, left: str, right: str) -> str | None:
    """E1, or the first of E2–E4, for two living rows. Empty when none holds.

    Used when a person decides a queue item, so the decision can record which
    evidence rules already fired. A later rule is what may reopen the item.
    """
    return _evidence(_State(ledger), left, right)


def _review(state: _State) -> list[dict]:
    """The review queue, or an empty list when the ledger has no queue file yet."""
    path = state.ledger.path("review_queue")
    return state.ledger.read("review_queue") if path.exists() else []


def _blocks_merge(
    state: _State,
    result: ResolveResult,
    left: str,
    right: str,
    evidence: str | None,
    *,
    dry_run: bool,
) -> bool:
    """A human split covers this pair. New evidence reopens the same item."""
    review = _review(state)
    blocked, reopened = decision_blocks(review, state.artists, left, right, evidence)
    if reopened:
        result.reopened.extend(reopened)
        if not dry_run:
            state.ledger.write("review_queue", review, task="resolve")
    return blocked


def _evidence(state: _State, left: str, right: str) -> str | None:
    """E1, or the first of E2–E4. None when no evidence rule holds."""
    return evidence_e1(left, right, state.sites) or evidence_e2_e4(
        left,
        right,
        state.frames,
        state.rows_of,
        state.cvs,
        state.patterns,
        team_prefix=state.team_prefix,
        generic=state.generic,
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
    """Record the merge and, unless this is a dry run, write it and reload."""
    # A record hidden by request is never merged automatically: the merge
    # would retire or republish it (Ledger._merge_rows refuses it as well).
    hidden = hidden_ids(state.by_id.values())
    drops = [drop for drop in drops if drop in state.by_id and drop != keep and drop not in hidden]
    if keep not in state.by_id or keep in hidden or not drops:
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
    state.ledger._merge_rows(keep, drops, evidence=evidence, rule=rule)
    state.reload()


def _names(artist: dict) -> set[str]:
    """Hangul runs, or the lower-cased spelling when a name has no Hangul, for an E1 overlap."""
    values = [artist.get("name_ko") or "", artist.get("name_en") or "", *split_pipe(artist.get("aliases"))]
    found = set()
    for value in values:
        if not value:
            continue
        hangul = "".join(ch for ch in value if "가" <= ch <= "힣")
        found.add(hangul or value.lower())
    return found


def _merge_by_website(state: _State, result: ResolveResult, *, dry_run: bool) -> None:
    """E1. Merge one website group, then reload, so the next group is chosen from the rows just written."""
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
                    if team_person_mismatch(person, head, words=state.team_words, language=state.language):
                        if _names(person) & _names(head):
                            result.blocked_team.append(tuple(sorted((lid, group[0]))))
                        continue
                    if _names(person) & set().union(*(_names(state.by_id[other]) for other in group)):
                        if _blocks_merge(
                            state,
                            result,
                            lid,
                            group[0],
                            f"E1 same website {key}",
                            dry_run=dry_run,
                        ):
                            continue
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
            if team_person_mismatch(row_a, row_b, words=state.team_words, language=state.language):
                result.blocked_team.append(tuple(sorted((left, right))))
                continue
            if pinned_apart(state.identities.get(left, []), state.identities.get(right, [])):
                continue
            evidence = _evidence(state, left, right)
            if not evidence:
                continue
            if _blocks_merge(state, result, left, right, evidence, dry_run=dry_run):
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
    deferred: list[tuple[str, str]] = []
    for ko_id, en_id in x1_candidates(state.artists, state.language):
        if ko_id not in state.by_id or en_id not in state.by_id:
            continue
        pair = (state.by_id[ko_id], state.by_id[en_id])
        # T1: either side a team, or one side recorded as a member of the other.
        if any(team_like(row, words=state.team_words, language=state.language) for row in pair) or (
            team_person_mismatch(*pair, words=state.team_words, language=state.language)
        ):
            result.blocked_team.append(tuple(sorted((ko_id, en_id))))
            continue
        if pinned_apart(state.identities.get(ko_id, []), state.identities.get(en_id, [])):
            continue
        if state.frames.get(ko_id, set()) & state.frames.get(en_id, set()):
            continue
        evidence = _evidence(state, ko_id, en_id)
        if evidence:
            if _blocks_merge(state, result, ko_id, en_id, evidence, dry_run=dry_run):
                continue
            evidence = f"X1+{evidence}"
            keep, drop = _keep_drop(state, ko_id, en_id)
            _apply(state, result, keep, [drop], evidence, dry_run=dry_run)
            continue
        deferred.append((ko_id, en_id))
    # Merges above can absorb one side of a pair that attachment already queued.
    # Point that item at the survivor before deciding whether a second item is new.
    if not dry_run:
        state.reload()
    review = _review(state)
    rewritten = point_review_at_survivors(review, state.artists)
    known = queue_pair_keys(review, state.artists)
    fresh: list[dict] = []
    for ko_id, en_id in deferred:
        covered = survivor_pair(ko_id, en_id, state.artists)
        if covered[0] == covered[1] or covered in known:
            continue
        if covered[0] not in state.by_id or covered[1] not in state.by_id:
            continue
        left, right = (ko_id, en_id) if ko_id in state.by_id and en_id in state.by_id else covered
        rows = (state.by_id[left], state.by_id[right])
        if any(team_like(row, words=state.team_words, language=state.language) for row in rows) or (
            team_person_mismatch(*rows, words=state.team_words, language=state.language)
        ):
            continue
        if pinned_apart(state.identities.get(left, []), state.identities.get(right, [])):
            continue
        if state.frames.get(left, set()) & state.frames.get(right, set()):
            continue
        item = new_queue_item(left, x1_detail(rows[0], rows[1], state.language))
        fresh.append(item)
        known.add(covered)
    result.queued.extend(fresh)
    if (fresh or rewritten) and not dry_run:
        review.extend(fresh)
        state.ledger.write("review_queue", review, task="resolve")


def _record_same_script_blocks(state: _State, result: ResolveResult) -> None:
    for left, right, _script in same_script_pairs(state.artists):
        if left not in state.by_id or right not in state.by_id:
            continue
        row_a, row_b = state.by_id[left], state.by_id[right]
        if team_person_mismatch(row_a, row_b, words=state.team_words, language=state.language):
            result.blocked_team.append(tuple(sorted((left, right))))
