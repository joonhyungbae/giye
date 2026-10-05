# SPDX-License-Identifier: AGPL-3.0-only
"""The file-backed ledger: open, read, write, merge, and roster upsert.

A ``gy_id`` is permanent (docs/ARCHITECTURE.md, Permanence). ``merge`` is the
only way a person row leaves the artists table: the dropped ``gy_id`` is
retired, and ids already retired into that row are chained to the survivor.
A merge without an evidence string is refused. A retired id has to carry a
reason, so an empty note is not evidence (docs/RULES.md, ledger invariants).

A CV-derived activity (``origin`` starts with ``cv:``) belongs to the owner of
that CV source, not to the ledger id written on the extraction file. Merges
update ``cv_sources.ledger_id``. Rows left on a retired id follow the source.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from giye.config import Config
from giye.ledger.ids import (
    activity_id_key,
    allocate_gy_id,
    gy_number,
    norm_activity_title,
    take_activity_id,
)
from giye.ledger.io import backup_before_write, hold_ledger_lock, read_csv, write_csv
from giye.ledger.schemas import (
    ACTIVITIES_FIELDS,
    ARTISTS_FIELDS,
    MEMBERSHIP_FIELDS,
    TABLES,
    empty_row,
    join_pipe,
    split_pipe,
)

_ARTIST_FILL = ("name_ko", "name_en", "affiliation", "active_since", "country", "region", "field", "category")


def cv_row_owner(row: Mapping[str, Any], sources: Iterable[Mapping[str, Any]]) -> str | None:
    """Ledger id a CV-derived activity belongs to, or None if the row is not one.

    The extraction file keeps the ledger id it was made for. After a merge that
    id is retired, while ``cv_sources.ledger_id`` is the person who still holds
    the CV. That owner decides whose activity the row is (docs/RULES.md, Owner).
    """
    origin = str(row.get("origin") or "")
    if not origin.startswith("cv:"):
        return None
    source_id = origin[3:]
    for source in sources:
        if source.get("source_id") == source_id:
            owner = str(source.get("ledger_id") or "")
            return owner or None
    return None


def repoint_cv_activities(activities: list[dict[str, Any]], sources: Iterable[Mapping[str, Any]]) -> int:
    """Move CV rows onto the owner of their source. Returns how many moved."""
    source_rows = list(sources)
    moved = 0
    for row in activities:
        owner = cv_row_owner(row, source_rows)
        if owner and owner != (row.get("ledger_id") or ""):
            row["ledger_id"] = owner
            moved += 1
    return moved


def collapse_cv_sources(
    rows: list[dict[str, Any]], activities: list[dict[str, Any]], keep: str
) -> dict[str, str]:
    """Drop a second registration of the same CV URL on ``keep``.

    Each side of a merge may have registered the URL. The row that already has
    a snapshot wins; otherwise the earlier row wins. Activity origins
    ``cv:<loser>`` are rewritten to the winner. Returns loser → winner.
    """
    best: dict[tuple[str, str], dict[str, Any]] = {}
    remap: dict[str, str] = {}
    for row in rows:
        if row.get("ledger_id") != keep:
            continue
        key = (row.get("ledger_id") or "", row.get("url") or "")
        first = best.get(key)
        if first is None:
            best[key] = row
            continue
        if first.get("snapshot_path") or not row.get("snapshot_path"):
            winner, loser = first, row
        else:
            winner, loser = row, first
        best[key] = winner
        remap[str(loser.get("source_id") or "")] = str(winner.get("source_id") or "")
    if not remap:
        return {}
    rows[:] = [row for row in rows if row.get("source_id") not in remap]
    for activity in activities:
        origin = activity.get("origin") or ""
        if origin.startswith("cv:") and origin[3:] in remap:
            activity["origin"] = f"cv:{remap[origin[3:]]}"
    return remap


class Ledger:
    """One archive's CSV ledger. Construct with ``Ledger.open(config)``."""

    def __init__(self, config: Config) -> None:
        self.config = config

    @classmethod
    def open(cls, config: Config) -> Ledger:
        """Create the ledger directory, take its lock, and return a handle."""
        config.ledger.mkdir(parents=True, exist_ok=True)
        hold_ledger_lock(config.ledger)
        return cls(config)

    @property
    def directory(self) -> Path:
        """Directory that holds this archive's CSV tables."""
        return self.config.ledger

    def path(self, table: str) -> Path:
        """CSV path for ``table``. Raises ``KeyError`` when the name is unknown."""
        filename, _fields = self._table(table)
        return self.directory / filename

    def fields(self, table: str) -> list[str]:
        """Column names for ``table``, in file order."""
        _filename, columns = self._table(table)
        return list(columns)

    def read(self, table: str) -> list[dict[str, str]]:
        """Return the table. A file that does not exist yet is an empty list."""
        hold_ledger_lock(self.directory)
        return read_csv(self.path(table))

    def write(self, table: str, rows: Iterable[Mapping[str, Any]], *, task: str) -> Path | None:
        """Replace ``table``. Copies the current file into the backup directory first.

        ``task`` is the label in ``<file>-<YYYYMMDD>-before-<task>.csv.gz``. The
        first write of a missing file has nothing to copy and returns None. A
        later write of the same table and task in the same run returns the copy
        already taken (``giye.ledger.io.backup_before_write``).
        """
        if not str(task or "").strip():
            raise ValueError("a ledger write needs a task name so the backup can be identified")
        hold_ledger_lock(self.directory)
        path = self.path(table)
        backup = backup_before_write(
            path, self.config.work / "backups", task, keep_days=self.config.keep_backups_days
        )
        write_csv(path=path, fields=self.fields(table), rows=rows)
        return backup

    def next_gy_id(self) -> str:
        """The id the next new person would receive. Does not reserve it."""
        existing = [row.get("gy_id", "") for row in self.read("artists")]
        existing += [row.get("gy_id", "") for row in self.read("gy_retired")]
        return allocate_gy_id(existing, prefix=self._prefix)

    def merge(self, kept: str, dropped: str | Iterable[str], *, evidence: str, rule: str) -> None:
        """Absorb ``dropped`` into ``kept`` after checking the evidence against the ledger.

        This is the public merge. ``evidence`` must be a merge evidence string
        (``E1``-``E4``, ``X1+E1``-``X1+E4`` with a citation, or ``H`` with a
        reason and a date), ``rule`` must be the code that string names, and
        the cited evidence must hold on this ledger for every dropped id
        (``giye.resolve.decide.verify_merge_evidence``). Free text, an unknown
        rule id, or a citation the data do not bear out is refused with
        ``GiyeError``. Why: a stored ``E1`` tells a reader that a shared
        website was checked, so a label nobody checked must not be written.
        """
        # Imported here: giye.resolve imports this module.
        from giye.config import GiyeError
        from giye.resolve.decide import verify_merge_evidence

        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("merge refused without an evidence string")
        if not isinstance(rule, str) or not rule.strip():
            raise ValueError("merge refused without a rule id")
        drop_ids = _drop_ids(dropped)
        if not drop_ids:
            raise ValueError("merge needs at least one dropped ledger id")
        for item in drop_ids:
            code = verify_merge_evidence(self, kept, item, evidence)
            if code != rule.strip():
                raise GiyeError(f"merge refused: rule {rule.strip()!r} is not the rule the evidence names ({code})")
        self._merge_rows(kept, drop_ids, evidence=evidence, rule=rule)

    def _merge_rows(self, kept: str, dropped: str | Iterable[str], *, evidence: str, rule: str) -> None:
        """Absorb ``dropped`` into ``kept`` without checking what the evidence says.

        Internal path for callers that have already decided the merge under a
        rule: the automatic resolver (its E1-E4 and X1 checks produce the
        evidence string) and ``giye.resolve.decide.merge_people`` (which ran
        ``verify_merge_evidence``). Moves activities, frame memberships and CV
        sources onto ``kept``, retires every dropped ``gy_id`` that ``kept``
        does not adopt, and points older retirements that landed on a dropped
        row at ``kept``. ``evidence`` is required: an empty string is not a
        reason. ``rule`` is the rule id stored on the kept row's note.
        """
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("merge refused without an evidence string")
        if not isinstance(rule, str) or not rule.strip():
            raise ValueError("merge refused without a rule id")
        drop_ids = _drop_ids(dropped)
        if not drop_ids:
            raise ValueError("merge needs at least one dropped ledger id")
        if kept in drop_ids:
            raise ValueError("the kept ledger id is also in the dropped list")

        artists = self.read("artists")
        by_id = {row["ledger_id"]: row for row in artists}
        if kept not in by_id:
            raise KeyError(kept)
        missing = [item for item in drop_ids if item not in by_id]
        if missing:
            raise KeyError(missing[0])
        survivor = by_id[kept]
        dropped_rows = [by_id[item] for item in drop_ids]
        _merge_artist_fields(survivor, dropped_rows, evidence=evidence.strip(), rule=rule.strip())
        self._retire(survivor, dropped_rows, task="merge")
        dropset = set(drop_ids)
        self.write("artists", [row for row in artists if row["ledger_id"] not in dropset], task="merge")
        self._move_child_tables(kept, dropset)
        self._collapse_and_repoint(kept)

    def redirects(self) -> dict[str, str]:
        """Retired ``gy_id`` → the survivor's current ``gy_id``.

        ``merge`` rewrites ``merged_into_ledger_id`` to the final survivor, so
        one lookup follows a chain of merges. A survivor who has no ``gy_id``
        yet is omitted.
        """
        gy_of = {row["ledger_id"]: row.get("gy_id", "") for row in self.read("artists")}
        out: dict[str, str] = {}
        for row in self.read("gy_retired"):
            target = gy_of.get(row.get("merged_into_ledger_id", ""), "")
            if row.get("gy_id") and target:
                out[row["gy_id"]] = target
        return out

    def apply_roster(self, frame: str, rows: Iterable[Mapping[str, Any]], *, task: str = "collect") -> None:
        """Upsert roster appearances for one frame.

        A row joins an existing person only under A1–A6 (``giye.resolve.attach``).
        The rule id is stored on the new membership (``attach_rule``). A row no
        rule attaches becomes a new person, ``attach_rule`` ``first``, and a
        same-name near-miss opens ``possible_same_person``. An exact
        ``(name_ko, name_en)`` pair is not by itself a join.

        A new person receives a new ``ledger_id`` and a new ``gy_id``. Membership
        is one row per person and frame. Each appearance becomes an activity
        whose id is the collector uuid5, so a second run of the same roster
        rewrites the same ids. A row may also carry ``activity`` (fields of that
        appearance row), ``extra_activities`` (more rows), ``note`` (the
        appearance row's note), ``websites`` and ``reviewer_note`` (segments
        added to the person's note); see ``giye.collect.Person``.

        Re-collection is idempotent: it adds rows that did not exist and fills
        empty fields, and nothing else (docs/FIELD.md, Re-collection). An
        existing activity row keeps its id, its ``collected_at`` and every
        non-empty value (``_upsert_frame_activities``); an existing membership
        is not touched; an existing person gains a missing name, alias or
        ``members=`` name, and a collector note only on request
        (``_stamp_roster_person``). ``updated_at`` moves only when a field
        changed. ``activity`` set to ``False`` writes no appearance row.
        """
        # Imported here: giye.resolve.teams imports Ledger, and attach imports teams.
        from giye.field import frame_family
        from giye.normalize.language import language_for
        from giye.resolve.attach import attach_row

        _require_roster_frame(frame)
        roster = list(rows)
        field = self.config.field_config
        language = language_for(self.config)
        state = _load_roster_tables(self, field, frame_family)
        assigned, rules, links_added, review_added = _attach_roster_rows(
            self, frame, roster, state, attach_row, frame_family, language
        )
        _upsert_frame_activities(frame, roster, assigned, state)
        _add_roster_memberships(frame, roster, assigned, rules, state)
        _commit_roster(self, state, task, links_added=links_added, review_added=review_added)

    def _write_roster_links(self, frame: str, roster: list[Mapping[str, Any]], assigned: list[str], *, task: str) -> None:
        """Store a personal website from a roster row. A second run does not add the URL again.

        Rule E1 reads ``links.csv``. Social links are not written here; a collector that
        knows a link is social can still record ``link_type`` social and E1 will skip it.
        """
        links = self.read("links") if self.path("links").exists() else []
        seen = {(row.get("ledger_id", ""), row.get("url", "")) for row in links}
        added = False
        for row, lid in zip(roster, assigned, strict=True):
            url = str(row.get("website") or "").strip()
            if not url or (lid, url) in seen:
                continue
            links.append(
                empty_row(
                    self.fields("links"),
                    link_id=str(uuid.uuid4()),
                    ledger_id=lid,
                    label="website",
                    url=url,
                    link_type="website",
                    origin=frame,
                )
            )
            seen.add((lid, url))
            added = True
        if added:
            self.write("links", links, task=task)

    def restore_cv_owners(self, *, task: str) -> int:
        """Write activities so each CV row's ``ledger_id`` is its source's owner."""
        activities = self.read("activities")
        moved = repoint_cv_activities(activities, self.read("cv_sources"))
        if moved:
            self.write("activities", activities, task=task)
        return moved

    @property
    def _prefix(self) -> str:
        """Person-id prefix from the archive config, or ``GY``."""
        return self.config.id_prefix or "GY"

    def _table(self, table: str) -> tuple[str, list[str]]:
        """Filename and columns for a known table name."""
        try:
            return TABLES[table]
        except KeyError:
            known = ", ".join(sorted(TABLES))
            raise KeyError(f"unknown ledger table {table!r} (known: {known})") from None

    def _retire(self, survivor: dict[str, str], dropped: list[dict[str, str]], *, task: str) -> None:
        """Keep the survivor's id, or the lowest dropped id if the survivor has none.

        Every other dropped id is retired into the survivor. Retirements that
        already pointed at a dropped row are rewritten to the survivor, so a
        later redirect is one step.
        """
        held = sorted(
            (row["gy_id"] for row in dropped if row.get("gy_id")),
            key=lambda gy: gy_number(gy, prefix=self._prefix),
        )
        if not survivor.get("gy_id") and held:
            survivor["gy_id"] = held.pop(0)
        path = self.path("gy_retired")
        rows = read_csv(path) if path.exists() else []
        dropped_ids = {row["ledger_id"] for row in dropped}
        for row in rows:
            if row.get("merged_into_ledger_id") in dropped_ids:
                row["merged_into_ledger_id"] = survivor["ledger_id"]
        today = datetime.now(timezone.utc).date().isoformat()
        rows.extend(
            {"gy_id": gy, "merged_into_ledger_id": survivor["ledger_id"], "retired_at": today} for gy in held
        )
        if rows or path.exists():
            self.write("gy_retired", rows, task=task)

    def _move_child_tables(self, kept: str, dropset: set[str]) -> None:
        """Point child rows that named a dropped person at the survivor."""
        self._move(
            "frame_membership",
            dropset,
            kept,
            dedupe=lambda row: (row.get("ledger_id", ""), row.get("frame_code", "")),
        )
        self._move("activities", dropset, kept, dedupe=None)
        self._move("cv_sources", dropset, kept, dedupe=None)
        self._move(
            "links",
            dropset,
            kept,
            dedupe=lambda row: (row.get("ledger_id", ""), (row.get("url") or "").rstrip("/")),
        )
        self._move("collaborations", dropset, kept, dedupe=None)
        self._move_review(kept, dropset)
        self._move("scope", dropset, kept, dedupe=lambda row: (row.get("ledger_id", ""),))

    def _move(self, table: str, dropset: set[str], kept: str, *, dedupe) -> None:
        """Rewrite ``ledger_id`` on one table. ``dedupe`` drops a repeated key."""
        path = self.path(table)
        if not path.exists():
            return
        rows = self.read(table)
        for row in rows:
            if row.get("ledger_id") in dropset:
                row["ledger_id"] = kept
        if dedupe is not None:
            seen: set[Any] = set()
            kept_rows = []
            for row in rows:
                key = dedupe(row)
                if key in seen:
                    continue
                seen.add(key)
                kept_rows.append(row)
            rows = kept_rows
        self.write(table, rows, task="merge")

    def _move_review(self, kept: str, dropset: set[str]) -> None:
        """Move review rows and close a same-person item the merge already decided."""
        path = self.path("review_queue")
        if not path.exists():
            return
        rows = self.read("review_queue")
        for row in rows:
            if row.get("ledger_id") in dropset:
                row["ledger_id"] = kept
            detail = row.get("detail") or ""
            if (
                row.get("reason") == "possible_same_person"
                and row.get("ledger_id") == kept
                and any(item in detail or kept in detail for item in dropset)
            ):
                row["status"] = "done"
        self.write("review_queue", rows, task="merge")

    def _collapse_and_repoint(self, kept: str) -> None:
        """Keep one CV source per URL on ``kept`` and point activities at its owner."""
        activities = self.read("activities")
        sources = self.read("cv_sources") if self.path("cv_sources").exists() else []
        remap = collapse_cv_sources(sources, activities, kept)
        if remap:
            self._rewrite_extract(kept, remap)
            self.write("cv_sources", sources, task="merge")
        moved = repoint_cv_activities(activities, sources)
        if remap or moved:
            self.write("activities", activities, task="merge")

    def _rewrite_extract(self, kept: str, remap: dict[str, str]) -> None:
        """Point a kept person's extraction file at the surviving source ids."""
        path = self.config.work / "cv_extract" / f"{kept}.json"
        if not path.is_file():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        seen: set[str] = set()
        sources = []
        for source in data.get("sources", []):
            source_id = remap.get(source.get("source_id"), source.get("source_id"))
            if source_id in seen:
                continue
            seen.add(source_id)
            sources.append({**source, "source_id": source_id})
        data["sources"] = sources
        for activity in data.get("activities", []):
            activity["source_id"] = remap.get(activity.get("source_id"), activity.get("source_id"))
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


@dataclass
class _RosterState:
    """Tables and indexes one roster apply reads and writes."""

    artists: list[dict[str, str]]
    activities: list[dict[str, str]]
    membership: list[dict[str, str]]
    links: list[dict[str, str]]
    review: list[dict[str, str]]
    issued: list[str]
    taken: set[str]
    by_id: dict[str, dict[str, str]]
    families: dict[str, set[str]]
    open_review: set[tuple[str, str, str]]
    seen_links: set[tuple[str, str]]
    stamp: str
    field: Any
    # People this apply created. A collector's person note goes on them only.
    created: set[str] = dc_field(default_factory=set)


def _require_roster_frame(frame: str) -> None:
    """Refuse a frame code that could be read as a path."""
    if not frame or "/" in frame or "\\" in frame:
        raise ValueError("frame code must not contain a path separator")


def _load_roster_tables(ledger: Ledger, field: Any, frame_family: Any) -> _RosterState:
    """Read the tables a roster upsert edits, and the indexes attachment consults."""
    artists = ledger.read("artists")
    activities = ledger.read("activities")
    membership = ledger.read("frame_membership")
    links = ledger.read("links") if ledger.path("links").exists() else []
    review = ledger.read("review_queue") if ledger.path("review_queue").exists() else []
    issued = [row.get("gy_id", "") for row in artists]
    issued += [row.get("gy_id", "") for row in ledger.read("gy_retired")]
    families: dict[str, set[str]] = {}
    for row in membership:
        families.setdefault(row["ledger_id"], set()).add(frame_family(row.get("frame_code", ""), field))
    open_review = {
        (row.get("ledger_id", ""), row.get("reason", ""), row.get("detail") or "")
        for row in review
        if row.get("status") == "open"
    }
    return _RosterState(
        artists=artists,
        activities=activities,
        membership=membership,
        links=links,
        review=review,
        issued=issued,
        taken={row["ledger_id"] for row in artists},
        by_id={row["ledger_id"]: row for row in artists},
        families=families,
        open_review=open_review,
        seen_links={(row.get("ledger_id", ""), row.get("url", "")) for row in links},
        stamp=_now(),
        field=field,
    )


def _attach_roster_rows(
    ledger: Ledger,
    frame: str,
    roster: list[Mapping[str, Any]],
    state: _RosterState,
    attach_row: Any,
    frame_family: Any,
    language: Any,
) -> tuple[list[str], list[str], bool, bool]:
    """Join each roster row under A1–A6, or open a new person. Returns ids, rules, and write flags."""
    assigned: list[str] = []
    rules: list[str] = []
    links_added = False
    review_added = False
    for row in roster:
        lid, rule, added_link, added_review = _attach_one_roster_row(
            ledger, frame, row, state, attach_row, frame_family, language
        )
        assigned.append(lid)
        rules.append(rule)
        if added_link:
            links_added = True
        if added_review:
            review_added = True
    return assigned, rules, links_added, review_added


def _attach_one_roster_row(
    ledger: Ledger,
    frame: str,
    row: Mapping[str, Any],
    state: _RosterState,
    attach_row: Any,
    frame_family: Any,
    language: Any,
) -> tuple[str, str, bool, bool]:
    """One roster row: attach or insert, then website and same-name queue."""
    raw_ko = str(row.get("name_ko") or "").strip()
    raw_en = str(row.get("name_en") or "").strip()
    aliases = str(row.get("aliases") or "")
    identity = str(row.get("identity") or "").strip()
    websites = _roster_websites(row)
    source_url = str(row.get("source_url") or "").strip()
    collected = str(row.get("collected_at") or "")[:10]
    decision = attach_row(
        artists=state.artists,
        families_by_lid=state.families,
        links=state.links,
        frame_code=frame,
        name_ko=raw_ko,
        name_en=raw_en,
        aliases=aliases,
        identity=identity,
        websites=websites,
        field=state.field,
        language=language,
        team_lid=str(row.get("team_lid") or ""),
    )
    stored_ko, stored_en = _stored_name(raw_ko, raw_en)
    attached = bool(decision.ledger_id and decision.ledger_id in state.by_id)
    before: dict[str, str] = {}
    if attached:
        before = dict(state.by_id[decision.ledger_id or ""])
        artist, rule = _fill_attached_artist(ledger, state, decision, raw_ko, stored_en, source_url)
    else:
        artist, rule = _insert_roster_artist(ledger, state, stored_ko, stored_en, aliases, source_url, collected)
        state.created.add(artist["ledger_id"])
    _note_roster_identity(artist, identity)
    _stamp_roster_person(artist, row, created=artist["ledger_id"] in state.created)
    # An unchanged person keeps its timestamp, so a re-run does not look like an edit.
    if attached and artist != before:
        artist["updated_at"] = state.stamp
    lid = artist["ledger_id"]
    state.families.setdefault(lid, set()).add(frame_family(frame, state.field))
    added_link = False
    for url in websites:
        if _add_roster_website(ledger, frame, lid, url, [url], state):
            added_link = True
    added_review = _queue_possible_same_person(
        ledger, frame, lid, raw_ko, raw_en, stored_ko, decision, attached, state
    )
    return lid, rule, added_link, added_review


def _fill_attached_artist(
    ledger: Ledger,
    state: _RosterState,
    decision: Any,
    raw_ko: str,
    stored_en: str,
    source_url: str,
) -> tuple[dict[str, str], str]:
    """Fill blanks on a person A1–A6 already joined. Returns the person and the rule id.

    ``name_ko`` is filled only from the row's own ``name_ko`` and only when it
    is not Latin text. The insert path copies a Latin name into ``name_ko`` so
    the name key of a new row matches on a re-run, but an existing person
    already has its key, and a Latin string there is not a native-script name.
    ``updated_at`` is the caller's: it moves only when a field changed.
    """
    artist = state.by_id[decision.ledger_id or ""]
    rule = decision.rule or ""
    if not artist.get("name_ko") and raw_ko and not _latin_only(raw_ko):
        artist["name_ko"] = raw_ko
    if not artist.get("name_en") and stored_en:
        artist["name_en"] = stored_en
    if not artist.get("gy_id"):
        gy = allocate_gy_id(state.issued, prefix=ledger._prefix)
        artist["gy_id"] = gy
        state.issued.append(gy)
    if not artist.get("source_url") and source_url:
        artist["source_url"] = source_url
        artist["source_type"] = artist.get("source_type") or "PUBLIC_RECORD"
    return artist, rule


def _latin_only(text: str) -> bool:
    """True when every letter in ``text`` is a Latin letter (accents included)."""
    letters = [ch for ch in text if ch.isalpha()]
    return bool(letters) and all(unicodedata.name(ch, "").startswith("LATIN") for ch in letters)


def _roster_websites(row: Mapping[str, Any]) -> list[str]:
    """``website`` and then ``websites`` (a list or a pipe-separated cell), http(s) only, once each."""
    extra = row.get("websites") or []
    if isinstance(extra, str):
        extra = split_pipe(extra)
    urls = [str(row.get("website") or "").strip(), *(str(item).strip() for item in extra)]
    return list(dict.fromkeys(url for url in urls if url.startswith("http")))


def _insert_roster_artist(
    ledger: Ledger,
    state: _RosterState,
    stored_ko: str,
    stored_en: str,
    aliases: str,
    source_url: str,
    collected: str,
) -> tuple[dict[str, str], str]:
    """A row no A-rule joins becomes a new person. The membership rule is ``first``."""
    lid = _new_ledger_id(state.taken)
    gy = allocate_gy_id(state.issued, prefix=ledger._prefix)
    state.issued.append(gy)
    artist = empty_row(
        ARTISTS_FIELDS,
        ledger_id=lid,
        gy_id=gy,
        name_ko=stored_ko,
        name_en=stored_en,
        aliases=aliases,
        cv_link_ok="no",
        frame_status="IN_FRAME",
        verification="UNVERIFIED",
        status="STAGED",
        source_url=source_url,
        source_type="PUBLIC_RECORD",
        collected_at=collected,
        updated_at=state.stamp,
    )
    state.artists.append(artist)
    state.by_id[lid] = artist
    return artist, "first"


def _note_roster_identity(artist: dict[str, str], identity: str) -> None:
    """Record ``identity=`` once, so a re-run does not append the same pin (A5)."""
    if identity:
        marker = f"identity={identity}"
        note = artist.get("reviewer_note") or ""
        if marker not in note:
            artist["reviewer_note"] = f"{note}; {marker}".strip("; ")


def _add_roster_website(
    ledger: Ledger,
    frame: str,
    lid: str,
    website: str,
    websites: list[str],
    state: _RosterState,
) -> bool:
    """Store a site from a roster row once. Returns whether a link row was added (E1 reads it).

    The type is ``roster_link_type``: a social profile is stored as ``social``,
    which E1 and the own-site depth level skip.
    """
    if websites and (lid, website) not in state.seen_links:
        kind = roster_link_type(website)
        state.links.append(
            empty_row(
                ledger.fields("links"),
                link_id=str(uuid.uuid4()),
                ledger_id=lid,
                label=kind,
                url=website,
                link_type=kind,
                origin=frame,
            )
        )
        state.seen_links.add((lid, website))
        return True
    return False


# Hosts filed as video or repository links. Not social: they still count as the
# person's own published location (``normalize.rules.own_site_rows``).
_VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com")
_REPOSITORY_HOSTS = ("github.com",)


def roster_link_type(url: str) -> str:
    """``social``, ``video``, ``repository`` or ``website`` for a URL a collector states.

    The same classes the production collectors stored. ``social`` is a host in
    ``giye.collect.fetch.SOCIAL_HOSTS`` (platforms whose terms forbid
    collection). Hosts match exactly or as a subdomain, so ``minaparkx.com`` is
    not ``x.com``.
    """
    # Imported here: giye.collect imports this module.
    from giye.collect.fetch import is_social

    if is_social(url):
        return "social"
    host = (urlparse(url.strip()).hostname or "").lower().rstrip(".")

    def on(domains: tuple[str, ...]) -> bool:
        return any(host == domain or host.endswith("." + domain) for domain in domains)

    if on(_VIDEO_HOSTS):
        return "video"
    if on(_REPOSITORY_HOSTS):
        return "repository"
    return "website"


def _queue_possible_same_person(
    ledger: Ledger,
    frame: str,
    lid: str,
    raw_ko: str,
    raw_en: str,
    stored_ko: str,
    decision: Any,
    attached: bool,
    state: _RosterState,
) -> bool:
    """Queue a same-name near-miss that was not attached. Returns whether a row was added."""
    if decision.ambiguous and not attached:
        detail = f"{raw_ko or raw_en or stored_ko} ({frame}) shares a name with {', '.join(decision.ambiguous)}"
        # ``miss`` is the attachment rule's reason (Latin personal names).
        # Empty keeps the Korean homonym sentence unchanged.
        if decision.miss:
            detail = f"{detail} ({decision.miss})"
        key = (lid, "possible_same_person", detail)
        if key not in state.open_review:
            state.review.append(
                empty_row(
                    ledger.fields("review_queue"),
                    queue_id=str(uuid.uuid4()),
                    ledger_id=lid,
                    reason="possible_same_person",
                    detail=detail,
                    status="open",
                    created_at=state.stamp,
                )
            )
            state.open_review.add(key)
            return True
    return False


def _upsert_frame_activities(
    frame: str, roster: list[Mapping[str, Any]], assigned: list[str], state: _RosterState
) -> None:
    """Add this edition's missing activity rows and fill empty fields. Nothing else changes.

    Re-collecting the pages a register was built from must be idempotent
    (docs/FIELD.md, Re-collection). A new row is matched against every row
    this person already has under the same origin, rows appended earlier in
    this apply included (``_match_existing``). A matched row keeps its id and
    every non-empty value; only its empty columns are filled (``_enrich``).
    An unmatched row is appended. No row is removed.

    Because a match may be any earlier row, a person who appears twice in one
    edition with the same credit ends up with one row, not a second row under
    the next counter id.
    """
    counters: dict[str, dict[str, int]] = {}
    index: dict[tuple[str, str], list[dict[str, str]]] = {}
    for existing in state.activities:
        index.setdefault((existing.get("ledger_id", ""), existing.get("origin", "")), []).append(existing)
    for row, lid in zip(roster, assigned, strict=True):
        for new, primary in _roster_activity_rows(frame, row, lid, counters):
            pool = index.setdefault((lid, new["origin"]), [])
            match, how = _match_existing(new, pool, primary=primary)
            if match is None:
                state.activities.append(new)
                pool.append(new)
                continue
            _enrich(match, new, how)


def _match_existing(
    new: Mapping[str, str], pool: list[dict[str, str]], *, primary: bool
) -> tuple[dict[str, str] | None, str]:
    """The existing row ``new`` stands for, and how it was matched.

    Identity is person and origin (frame and edition), and then, in order:

    1. the same ``activity_id`` (the same fact from the same source);
    2. the same normalised title (the same credit, whatever the source URL,
       year column or counter suffix the replay gives it);
    3. for the appearance row only, the same year when one side is the
       placeholder titled with the edition code: the appearance is already
       recorded (``placeholder`` when the new row is the placeholder).
    """
    for row in pool:
        if row.get("activity_id") == new["activity_id"]:
            return row, "same"
    title = norm_activity_title(new["title"])
    for row in pool:
        if norm_activity_title(row.get("title") or "") == title:
            return row, "same"
    if not primary:
        return None, ""
    origin = new["origin"]
    year = str(new.get("year") or "")
    same_year = [row for row in pool if str(row.get("year") or "") == year]
    if new["title"] == origin:
        return (same_year[0], "placeholder") if same_year else (None, "")
    for row in same_year:
        if (row.get("title") or "") == origin:
            return row, "same"
    return None, ""


# Columns a re-collection may fill on a matched row.
# activity_id, ledger_id and origin are identity, not content.
_ACTIVITY_CONTENT = (
    "title",
    "venue",
    "year",
    "activity_type",
    "role",
    "source_url",
    "source_type",
    "collected_at",
    "publishable",
    "reviewer_note",
)


def _enrich(existing: dict[str, str], new: Mapping[str, str], how: str) -> None:
    """Fill the matched row's empty columns from ``new``. A non-empty value always wins.

    The id is never replaced, and neither is a value someone or an earlier
    run stored: ``collected_at`` stays the date the fact was first read,
    ``publishable`` stays a curator's ``no``, and a note keeps its text.
    ``placeholder``: the collector stated nothing beyond the edition code, so
    nothing changes.
    """
    if how == "placeholder":
        return
    for key in _ACTIVITY_CONTENT:
        if not existing.get(key) and new.get(key):
            existing[key] = new[key]


def _add_roster_memberships(
    frame: str,
    roster: list[Mapping[str, Any]],
    assigned: list[str],
    rules: list[str],
    state: _RosterState,
) -> None:
    """One membership per person and frame. A second appearance does not add a row."""
    mem_keys = {(row.get("ledger_id", ""), row.get("frame_code", "")) for row in state.membership}
    for row, lid, rule in zip(roster, assigned, rules, strict=True):
        if (lid, frame) in mem_keys:
            continue
        state.membership.append(
            empty_row(
                MEMBERSHIP_FIELDS,
                ledger_id=lid,
                frame_code=frame,
                source_url=str(row.get("source_url") or "").strip(),
                collected_at=str(row.get("collected_at") or "")[:10],
                attach_rule=rule,
            )
        )
        mem_keys.add((lid, frame))


def _commit_roster(
    ledger: Ledger, state: _RosterState, task: str, *, links_added: bool, review_added: bool
) -> None:
    """Write the roster tables. Links and the review queue are written only when a row was added."""
    ledger.write("artists", state.artists, task=task)
    ledger.write("activities", state.activities, task=task)
    ledger.write("frame_membership", state.membership, task=task)
    if links_added:
        ledger.write("links", state.links, task=task)
    if review_added:
        ledger.write("review_queue", state.review, task=task)


def _drop_ids(dropped: str | Iterable[str]) -> list[str]:
    """The dropped ledger ids, whether the caller passed one string or many."""
    if isinstance(dropped, str):
        return [dropped]
    return [str(item) for item in dropped]


def _merge_artist_fields(survivor: dict[str, str], dropped: list[dict[str, str]], *, evidence: str, rule: str) -> None:
    """Fill empty survivor fields from the dropped rows and record the evidence."""
    for other in dropped:
        for field in _ARTIST_FILL:
            if not survivor.get(field) and other.get(field):
                survivor[field] = other[field]
        names = [other.get("name_ko", ""), other.get("name_en", "")] + split_pipe(other.get("aliases"))
        survivor["aliases"] = join_pipe(
            item
            for item in split_pipe(survivor.get("aliases")) + names
            if item and item not in (survivor.get("name_ko"), survivor.get("name_en"))
        )
        if other.get("cv_link_ok") == "yes":
            survivor["cv_link_ok"] = "yes"
            survivor["status"] = "PUBLISHED"
        note = f"{survivor.get('reviewer_note') or ''}; merged {other['ledger_id']}".strip("; ")
        # Collectors pin some rows by an identity key kept in the note; the kept row inherits it.
        for part in (other.get("reviewer_note") or "").split(";"):
            piece = part.strip()
            if piece.startswith("identity=") and piece not in note:
                note = f"{note}; {piece}"
        survivor["reviewer_note"] = note
    note = f"{survivor.get('reviewer_note') or ''}; merge_evidence={evidence}; rule={rule}"
    survivor["reviewer_note"] = note.strip("; ")


def _stamp_roster_person(artist: dict[str, str], row: Mapping[str, Any], *, created: bool) -> None:
    """Copy aliases, note segments and a ``members=`` note onto the person.

    Re-collection may only add and fill (docs/FIELD.md, Re-collection), so
    every part leaves the stored text byte-identical when it has nothing new:

    - an alias is added when it is not listed yet;
    - the collector's note (``reviewer_note``, ``Person.person_note``) goes on
      a person this apply created. An existing person gets it only when the
      row sets ``reviewer_note_existing`` (``Person.person_note_existing``),
      and then only the segments not already there. Segments compare after
      whitespace normalisation. A note is curated text, and a replayed page
      must not reclassify a person (a ``team`` segment reads as T1);
    - there is one ``members=`` segment. It is written on a person this apply
      created, or extended on a person that already has one: an existing
      record without a list was judged without one (T1 would turn it into a
      team). Names from this row that the list lacks are added
      (pipe-separated), none is removed, and a list that gains no name keeps
      its spelling.
    """
    aliases = split_pipe(str(row.get("aliases") or ""))
    have = split_pipe(artist.get("aliases"))
    if any(alias not in have for alias in aliases):
        artist["aliases"] = join_pipe([*have, *aliases])
    segments = _note_segments(artist.get("reviewer_note") or "")
    changed = False
    if created or row.get("reviewer_note_existing"):
        for segment in _note_segments(str(row.get("reviewer_note") or "")):
            if segment.startswith("members="):
                changed = _merge_members(segments, segment[len("members="):]) or changed
            elif _norm_segment(segment) not in {_norm_segment(item) for item in segments}:
                segments.append(segment)
                changed = True
    members = row.get("members") or ""
    if isinstance(members, (list, tuple)):
        members = "|".join(str(item) for item in members)
    has_list = any(segment.startswith("members=") for segment in segments)
    if str(members).strip() and (created or has_list):
        changed = _merge_members(segments, str(members)) or changed
    if changed:
        artist["reviewer_note"] = "; ".join(segments)


def _norm_segment(segment: str) -> str:
    """A note segment with runs of whitespace collapsed, for the already-present test."""
    return " ".join(segment.split())


def _note_segments(note: str) -> list[str]:
    """Non-empty ``;``-separated parts of a note, stripped."""
    return [part.strip() for part in (note or "").split(";") if part.strip()]


def _member_names(value: str) -> list[str]:
    """Member names from ``A, B`` or ``A|B``."""
    return [part.strip() for part in re.split(r"[,|]", value or "") if part.strip()]


def _merge_members(segments: list[str], value: str) -> bool:
    """Add names to the single ``members=`` segment, pipe-separated. Returns whether it changed.

    A list that already holds every name is left as it is, whatever its
    separator. Otherwise the list is rewritten pipe-separated with the new
    names at the end, and a second ``members=`` segment left by an earlier
    double write is folded into the first.
    """
    names = _member_names(value)
    found = [index for index, segment in enumerate(segments) if segment.startswith("members=")]
    if not names:
        return False
    listed = [name for index in found for name in _member_names(segments[index][len("members="):])]
    if found and all(name in listed for name in names):
        return False
    merged = "members=" + join_pipe([*listed, *names])
    if not found:
        segments.append(merged)
        return True
    segments[found[0]] = merged
    for index in reversed(found[1:]):
        del segments[index]
    return True


def _stored_name(name_ko: str, name_en: str) -> tuple[str, str]:
    """Names as stored on insert.

    A missing Hangul name is copied from the Latin name, and both empty becomes
    ``unknown``, so the match key and the stored key agree on a re-run. The
    placeholder is the English word because ledger labels are English.
    """
    ko = (name_ko or "").strip()
    en = (name_en or "").strip()
    if not ko:
        ko = en or "unknown"
    return ko, en


def _new_ledger_id(taken: set[str]) -> str:
    """A new ``LED-`` id that is not already in ``taken``."""
    for _ in range(8):
        lid = f"LED-{uuid.uuid4().hex[:10]}"
        if lid not in taken:
            taken.add(lid)
            return lid
    raise RuntimeError("could not allocate a ledger id")


def _now() -> str:
    """UTC timestamp stored on new and updated rows."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Keys a collector may state for an activity row. ``origin`` only on an extra row.
_ACTIVITY_KEYS = frozenset(_ACTIVITY_CONTENT)


def _activity_spec(spec: object, *, extra: bool) -> dict[str, str]:
    """A collector's activity dict, checked. An unknown key is refused so a typo is not dropped silently."""
    if spec is None:
        return {}
    if not isinstance(spec, Mapping):
        raise TypeError("an activity must be a dict of activity columns")
    allowed = _ACTIVITY_KEYS | {"origin"} if extra else _ACTIVITY_KEYS
    unknown = sorted(set(spec) - allowed)
    if unknown:
        where = "extra activity" if extra else "appearance activity (its origin is the edition code)"
        raise ValueError(f"unknown key(s) on {where}: {', '.join(unknown)}")
    out = {key: "" if value is None else str(value).strip() for key, value in spec.items()}
    origin = out.get("origin", "")
    if origin and (origin.startswith("cv:") or "/" in origin or "\\" in origin):
        raise ValueError(f"an extra activity origin must be a frame code: {origin!r}")
    if extra and not out.get("title"):
        raise ValueError("an extra activity needs a title")
    return out


def _roster_activity_rows(
    frame: str, row: Mapping[str, Any], ledger_id: str, counters: dict[str, dict[str, int]]
) -> list[tuple[dict[str, str], bool]]:
    """The appearance row, then each extra row. The flag marks the appearance row.

    ``activity`` set to ``False`` means the appearance has no activity row
    (``Person(activity=False)``): the person gets the membership and only the
    extra rows, if any.
    """
    rows: list[tuple[dict[str, str], bool]] = []
    if row.get("activity") is not False:
        rows.append((_roster_activity(frame, row, ledger_id, counters), True))
    for spec in row.get("extra_activities") or []:
        checked = _activity_spec(spec, extra=True)
        rows.append((_roster_activity(frame, row, ledger_id, counters, spec=checked), False))
    return rows


def _roster_activity(
    frame: str,
    row: Mapping[str, Any],
    ledger_id: str,
    counters: dict[str, dict[str, int]],
    *,
    spec: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """One activity row of a roster appearance. Without a stated title, the title is the frame code.

    The roster row carries a year and a source URL. The frame code is the
    stable title and the type is ``other``, the collector default when a type
    is absent. The year is its own column and the frame code is the origin, so
    two editions do not share an id (docs/RULES.md, activity ids).

    ``row["activity"]`` (or ``spec`` for an extra row) overrides any of those
    columns. Defaults are the row's year, source, date and role and the
    row's ``note``, so a collector that states nothing writes the same row,
    with the same id, as before.
    """
    if spec is None:
        spec = _activity_spec(row.get("activity"), extra=False)
    # A stated ``source_url``, even an empty one, is the row's source. Falling
    # back to the roster page would cite a page that does not state this fact
    # and would change the row's identity.
    if "source_url" in spec:
        source = spec["source_url"]
    else:
        source = str(row.get("source_url") or "").strip()
    # A stated year, even an empty one, is the row's year: an undated career line
    # must not take the edition's year, which the source does not state.
    if "year" in spec:
        year = str(spec["year"] or "")
    else:
        year = str(row.get("year") or "")
    origin = spec.get("origin") or frame
    title = spec.get("title") or frame
    activity_type = spec.get("activity_type") or "other"
    venue = spec.get("venue") or ""
    # Role is not part of the activity id. A work title or ``팀:`` credit can sit
    # here (rules E3 and E4) without changing the id of the roster appearance.
    role = spec.get("role") or str(row.get("role") or "")
    key = activity_id_key(
        ledger_id=ledger_id,
        source=source,
        title=title,
        year=year,
        activity_type=activity_type,
        venue=venue,
        origin=origin,
    )
    return empty_row(
        ACTIVITIES_FIELDS,
        activity_id=take_activity_id(counters.setdefault(ledger_id, {}), key),
        ledger_id=ledger_id,
        title=title,
        venue=venue,
        year=year,
        activity_type=activity_type,
        role=role,
        source_url=source,
        source_type=spec.get("source_type") or "PUBLIC_RECORD",
        collected_at=(spec.get("collected_at") or str(row.get("collected_at") or ""))[:10],
        publishable=spec.get("publishable") or ("yes" if source.startswith("http") else "no"),
        reviewer_note=spec.get("reviewer_note") or str(row.get("note") or "").strip(),
        origin=origin,
    )
