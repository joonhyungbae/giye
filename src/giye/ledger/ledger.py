# SPDX-License-Identifier: MIT
"""The file-backed ledger: open, read, write, merge, and roster upsert.

A ``gy_id`` is permanent (docs/ARCHITECTURE.md, Permanence). ``merge`` is the
only way a person row leaves the artists table: the dropped ``gy_id`` is
retired, and ids already retired into that row are chained to the survivor.
A merge without an evidence string is refused. Production recorded evidence
when the caller passed it and also allowed an empty note; this API requires
the string so a retired id always has a reason.

A CV-derived activity (``origin`` starts with ``cv:``) belongs to the owner of
that CV source, not to the ledger id written on the extraction file. Merges
update ``cv_sources.ledger_id``. Rows left on a retired id follow the source.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from giye.config import Config
from giye.ledger.ids import (
    activity_id_key,
    allocate_gy_id,
    gy_number,
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
    the CV. That owner decides whose activity the row is
    (``apply_cv_extractions.py``).
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
        return self.config.ledger

    def path(self, table: str) -> Path:
        filename, _fields = self._table(table)
        return self.directory / filename

    def fields(self, table: str) -> list[str]:
        _filename, columns = self._table(table)
        return list(columns)

    def read(self, table: str) -> list[dict[str, str]]:
        """Return the table. A file that does not exist yet is an empty list."""
        hold_ledger_lock(self.directory)
        return read_csv(self.path(table))

    def write(self, table: str, rows: Iterable[Mapping[str, Any]], *, task: str) -> Path | None:
        """Replace ``table``. Copies the current file into the backup directory first.

        ``task`` is the label in ``<file>-<YYYYMMDD>-before-<task>.csv``. The
        first write of a missing file has nothing to copy and returns None.
        """
        if not str(task or "").strip():
            raise ValueError("a ledger write needs a task name so the backup can be identified")
        hold_ledger_lock(self.directory)
        path = self.path(table)
        backup = backup_before_write(path, self.config.work / "backups", task)
        write_csv(path=path, fields=self.fields(table), rows=rows)
        return backup

    def next_gy_id(self) -> str:
        """The id the next new person would receive. Does not reserve it."""
        existing = [row.get("gy_id", "") for row in self.read("artists")]
        existing += [row.get("gy_id", "") for row in self.read("gy_retired")]
        return allocate_gy_id(existing, prefix=self._prefix)

    def merge(self, kept: str, dropped: str | Iterable[str], *, evidence: str, rule: str) -> None:
        """Absorb ``dropped`` into ``kept``.

        Moves activities, frame memberships and CV sources onto ``kept``, retires
        every dropped ``gy_id`` that ``kept`` does not adopt, and points older
        retirements that landed on a dropped row at ``kept``. ``evidence`` is
        required: an empty string is not a reason. ``rule`` is the rule id
        (E1–E4 and the rest) stored on the kept row's note.
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

        A new person receives a new ``ledger_id`` and a new ``gy_id``. A person
        already stored under the same ``name_ko`` and ``name_en`` (after the
        same empty-name fallback the insert uses) keeps both ids. Membership is
        one row per person and frame. Each appearance becomes an activity whose
        id is the collector uuid5, so a second run of the same roster rewrites
        the same ids. Activities this frame wrote earlier for those people are
        replaced; other origins are left in place.
        """
        if not frame or "/" in frame or "\\" in frame:
            raise ValueError("frame code must not contain a path separator")
        roster = list(rows)
        artists = self.read("artists")
        activities = self.read("activities")
        membership = self.read("frame_membership")
        issued = [row.get("gy_id", "") for row in artists]
        issued += [row.get("gy_id", "") for row in self.read("gy_retired")]
        taken = {row["ledger_id"] for row in artists}
        by_name = {(_stored_name(row.get("name_ko", ""), row.get("name_en", ""))): row for row in artists}
        stamp = _now()
        assigned: list[str] = []
        for row in roster:
            key = _stored_name(str(row.get("name_ko") or ""), str(row.get("name_en") or ""))
            artist = by_name.get(key)
            source_url = str(row.get("source_url") or "").strip()
            collected = str(row.get("collected_at") or "")[:10]
            if artist is None:
                lid = _new_ledger_id(taken)
                gy = allocate_gy_id(issued, prefix=self._prefix)
                issued.append(gy)
                artist = empty_row(
                    ARTISTS_FIELDS,
                    ledger_id=lid,
                    gy_id=gy,
                    name_ko=key[0],
                    name_en=key[1],
                    cv_link_ok="no",
                    frame_status="IN_FRAME",
                    verification="UNVERIFIED",
                    status="STAGED",
                    source_url=source_url,
                    source_type="PUBLIC_RECORD",
                    collected_at=collected,
                    updated_at=stamp,
                )
                artists.append(artist)
                by_name[key] = artist
            else:
                if not artist.get("gy_id"):
                    gy = allocate_gy_id(issued, prefix=self._prefix)
                    artist["gy_id"] = gy
                    issued.append(gy)
                if not artist.get("source_url") and source_url:
                    artist["source_url"] = source_url
                    artist["source_type"] = artist.get("source_type") or "PUBLIC_RECORD"
                artist["updated_at"] = stamp
            assigned.append(artist["ledger_id"])

        touched = set(assigned)
        activities = [
            row for row in activities if not (row.get("origin") == frame and row.get("ledger_id") in touched)
        ]
        counters: dict[str, dict[str, int]] = {}
        for row, lid in zip(roster, assigned, strict=True):
            activities.append(_roster_activity(frame, row, lid, counters))
        mem_keys = {(row.get("ledger_id", ""), row.get("frame_code", "")) for row in membership}
        for row, lid in zip(roster, assigned, strict=True):
            if (lid, frame) in mem_keys:
                continue
            membership.append(
                empty_row(
                    MEMBERSHIP_FIELDS,
                    ledger_id=lid,
                    frame_code=frame,
                    source_url=str(row.get("source_url") or "").strip(),
                    collected_at=str(row.get("collected_at") or "")[:10],
                )
            )
            mem_keys.add((lid, frame))
        self.write("artists", artists, task=task)
        self.write("activities", activities, task=task)
        self.write("frame_membership", membership, task=task)

    def restore_cv_owners(self, *, task: str) -> int:
        """Write activities so each CV row's ``ledger_id`` is its source's owner."""
        activities = self.read("activities")
        moved = repoint_cv_activities(activities, self.read("cv_sources"))
        if moved:
            self.write("activities", activities, task=task)
        return moved

    @property
    def _prefix(self) -> str:
        return self.config.id_prefix or "GY"

    def _table(self, table: str) -> tuple[str, list[str]]:
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


def _drop_ids(dropped: str | Iterable[str]) -> list[str]:
    if isinstance(dropped, str):
        return [dropped]
    return [str(item) for item in dropped]


def _merge_artist_fields(survivor: dict[str, str], dropped: list[dict[str, str]], *, evidence: str, rule: str) -> None:
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


def _stored_name(name_ko: str, name_en: str) -> tuple[str, str]:
    """Names as stored on insert.

    Production copies a missing Hangul name from the Latin name, and uses a
    placeholder when both are empty, so the match key and the stored key agree
    on a re-run. The placeholder here is ``unknown`` (production stored a Korean
    phrase; this repository's code and ledger labels are English).
    """
    ko = (name_ko or "").strip()
    en = (name_en or "").strip()
    if not ko:
        ko = en or "unknown"
    return ko, en


def _new_ledger_id(taken: set[str]) -> str:
    for _ in range(8):
        lid = f"LED-{uuid.uuid4().hex[:10]}"
        if lid not in taken:
            taken.add(lid)
            return lid
    raise RuntimeError("could not allocate a ledger id")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _roster_activity(
    frame: str, row: Mapping[str, Any], ledger_id: str, counters: dict[str, dict[str, int]]
) -> dict[str, str]:
    """One roster appearance. The title is the frame code.

    Production collectors set a programme-specific title and type. ``Edition``
    carries a year and a source URL only, so the frame code is the stable title
    and the type is ``other`` (the collector default when a type is absent).
    The year is its own column, and the frame code is the origin, so two
    editions do not share an id.
    """
    source = str(row.get("source_url") or "").strip()
    year = str(row.get("year") or "")
    title = frame
    key = activity_id_key(
        ledger_id=ledger_id,
        source=source,
        title=title,
        year=year,
        activity_type="other",
        venue="",
        origin=frame,
    )
    return empty_row(
        ACTIVITIES_FIELDS,
        activity_id=take_activity_id(counters.setdefault(ledger_id, {}), key),
        ledger_id=ledger_id,
        title=title,
        year=year,
        activity_type="other",
        source_url=source,
        source_type="PUBLIC_RECORD",
        collected_at=str(row.get("collected_at") or "")[:10],
        publishable="yes" if source.startswith("http") else "no",
        origin=frame,
    )
