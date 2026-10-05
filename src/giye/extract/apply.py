# SPDX-License-Identifier: AGPL-3.0-only
"""Write validated CV rows into the ledger.

- A row's origin is ``cv:<source_id>``. Re-applying a file removes the rows
  those sources produced and writes the new reading, so a changed CV replaces
  the old one instead of stacking.
- The file is skipped when any of its sources has a different content hash in
  the registry than the hash stored in the file. Re-extract first.
- An institution's public row of the same event stays; the CV copy is skipped.
  A self-reported row (``source_type`` ``SELF_SUBMITTED``, or a note that starts
  with ``from_arko_career_text``) loses to the CV: ``publishable`` becomes
  ``no`` and the note gains ``superseded_by_cv``. The mark is cleared and
  recomputed on the next apply.
- ``publishable=no`` for education, employment, teaching, press, scholarship,
  and service, and for scholarship or peer-review titles. Upcoming entries are
  hidden only when the year is still in the future. This year's upcoming rows
  stay visible with ``(예정)`` on the role, because a CV label goes stale.
- Korean and English copies of one event, inside the rows this file is adding,
  collapse when the normalised titles match (or one contains the other) and the
  normalised venues match. The first row is kept. The prompt asks for the
  Korean wording first; the code does not reorder.
- A CV row belongs to the owner of its source, not to the ledger id written on
  the extraction file (a merge retires that id and updates ``cv_sources``).
- An extraction file whose ledger id is no longer an artist is skipped when a
  live artist's file already covers the same sources. Applying both would let
  file order decide which reading survives.
- Activity ids come from ``giye.ledger`` (uuid5 of the ledger id, source,
  title, year, type, and venue). Applying the same reading again rewrites
  those ids; that rewrite is not a new activity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from giye.ledger.ids import (
    activity_id_key,
    cv_activity_key,
    norm_activity_title,
    reissue_frame_activity_ids,
    take_activity_id,
    zip_activity_id_changes,
)
from giye.ledger.io import write_csv
from giye.ledger.ledger import Ledger, repoint_cv_activities

TYPES = {
    "solo_exhibition",
    "group_exhibition",
    "screening",
    "performance",
    "festival",
    "online_release",
    "award",
    "residency",
    "other",
}
PRIVATE_SECTIONS = {"education", "employment", "teaching", "press", "scholarship", "service"}
# Scholarships and academic service often land under "award" or "other".
# They are not practice records, so they stay out of the public site as well.
PRIVATE_TITLE = re.compile(
    r"장학금|scholarship|최우수생|peer tutoring|^review of\b|reviewer|program committee|web chair|local organiz",
    re.IGNORECASE,
)
SUPERSEDED = "superseded_by_cv"


@dataclass
class ApplyStats:
    """What one apply wrote. ``added`` counts CV rows inserted, including private ones."""

    added: int = 0
    repointed: int = 0
    superseded_files: int = 0
    superseded_rows: int = 0
    duplicates: int = 0
    skipped_stale: list[str] = field(default_factory=list)
    closed_reviews: int = 0
    id_changes: int = 0


def self_reported(row: dict[str, str]) -> bool:
    """Rows the artist wrote elsewhere. A CV entry may replace them.

    An institution's public record is not self-reported, and the CV copy is
    skipped so the public row stays the one on the site.
    """
    note = row.get("reviewer_note") or ""
    return note.startswith("from_arko_career_text") or row.get("source_type") == "SELF_SUBMITTED"


def same_activity(left: dict, right: dict) -> bool:
    """Same year and the same title, or one title contained in the other.

    Hangul packs a word into fewer characters, so the shorter title may be 4
    characters. A Latin title needs 6. A Korean line and an English line that
    repeat one event match, and so does a show title that also names the work.
    """
    if str(left.get("year") or "") != str(right.get("year") or ""):
        return False
    title_a = norm_activity_title(str(left.get("title") or ""))
    title_b = norm_activity_title(str(right.get("title") or ""))
    if not title_a or not title_b:
        return False
    shorter = min((title_a, title_b), key=len)
    floor = 4 if re.search(r"[가-힣]", shorter) else 6
    return title_a == title_b or (len(shorter) >= floor and (title_a in title_b or title_b in title_a))


def apply_extractions(ledger: Ledger, *, today: date | None = None) -> ApplyStats:
    """Merge ``data/work/cv_extract/*.json`` into ``activities.csv``.

    ``today`` decides which upcoming year is still in the future. The default
    is today's UTC date. Tests pass a fixed date.
    """
    config = ledger.config
    today = today or datetime.now(timezone.utc).date()
    registry = {row["source_id"]: row for row in ledger.read("cv_sources")}
    activities = ledger.read("activities")
    queue = ledger.read("review_queue")
    stats = ApplyStats()

    frame_origins = {row["frame_code"] for row in ledger.read("frame_membership") if row.get("frame_code")}
    id_changes = reissue_frame_activity_ids(activities, frame_origins)
    stats.repointed = repoint_cv_activities(activities, registry.values())

    extract_dir = config.work / "cv_extract"
    files = sorted(extract_dir.glob("*.json")) if extract_dir.is_dir() else []
    live = {row["ledger_id"] for row in ledger.read("artists")}
    covers: dict[str, set[str]] = {}
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        covers[path.stem] = {item["source_id"] for item in data.get("sources", [])}
    live_cover: set[str] = set()
    for stem, sources in covers.items():
        if stem in live:
            live_cover |= sources
    superseded_files = {stem for stem, sources in covers.items() if stem not in live and sources and sources <= live_cover}
    files = [path for path in files if path.stem not in superseded_files]
    stats.superseded_files = len(superseded_files)

    for path in files:
        added, dup, superseded = _apply_file(
            path,
            registry=registry,
            activities=activities,
            queue=queue,
            id_changes=id_changes,
            today=today,
            stats=stats,
        )
        # ``_apply_file`` replaces the list object when it drops old CV rows.
        activities = added
        stats.duplicates += dup
        stats.superseded_rows += superseded

    seen: set[str] = set()
    duplicate_ids = 0
    for row in activities:
        if row["activity_id"] in seen:
            duplicate_ids += 1
        seen.add(row["activity_id"])
    if duplicate_ids:
        raise SystemExit(f"duplicate activity_id rows={duplicate_ids}")

    stats.id_changes = len(id_changes)
    if id_changes:
        _write_id_map(ledger, id_changes)
    ledger.write("activities", activities, task="apply-cv")
    ledger.write("review_queue", queue, task="apply-cv")
    return stats


def _extraction_owner(data: dict, registry: dict[str, dict[str, str]]) -> str:
    """The source's person when every source in the file has the same owner.

    Several owners, or none, keep the ledger id written on the file. A merge
    retires that id and updates ``cv_sources``; a mixed file must not guess
    which person the rows belong to.
    """
    owners = {
        registry[item["source_id"]]["ledger_id"]
        for item in data.get("sources", [])
        if item.get("source_id") in registry
    }
    return owners.pop() if len(owners) == 1 else data["ledger_id"]


def _sources_stale(data: dict, registry: dict[str, dict[str, str]]) -> bool:
    """True when a source's registry hash is not the hash this file was extracted from."""
    return any(
        registry.get(item["source_id"], {}).get("content_sha256") != item.get("content_sha256")
        for item in data.get("sources", [])
    )


def _clear_superseded(others: list[dict[str, str]]) -> None:
    """Drop ``superseded_by_cv`` so this apply recomputes which rows the CV replaces."""
    for other in others:
        note = other.get("reviewer_note") or ""
        if SUPERSEDED in note:
            other["publishable"] = "yes"
            other["reviewer_note"] = re.sub(rf";?\s*{SUPERSEDED}", "", note).strip("; ")


def _repeats_new_row(activity: dict, new_rows: list[dict[str, str]]) -> bool:
    """Korean and English copies of one event inside the rows this file is adding."""
    venue_key = norm_activity_title(str(activity.get("venue") or ""))
    return any(
        same_activity(activity, existing) and venue_key == norm_activity_title(existing.get("venue") or "")
        for existing in new_rows
    )


def _supersede_self_reports(twins: list[dict[str, str]]) -> int:
    """Hide self-reported copies of this CV event. Returns how many were newly hidden."""
    superseded = 0
    for other in twins:
        if other.get("publishable") == "yes":
            other["publishable"] = "no"
            note = other.get("reviewer_note") or ""
            other["reviewer_note"] = f"{note}; {SUPERSEDED}".strip("; ")
            superseded += 1
    return superseded


def _rows_from_file(
    data: dict,
    *,
    registry: dict[str, dict[str, str]],
    others: list[dict[str, str]],
    ledger_id: str,
    today: date,
) -> tuple[list[dict[str, str]], int, int]:
    """CV rows to insert, plus how many copies and self-reports were skipped or hidden."""
    new_rows: list[dict[str, str]] = []
    id_counter: dict[str, int] = {}
    duplicates = 0
    superseded = 0
    for activity in data.get("activities", []):
        source = registry.get(activity.get("source_id") or "")
        year = activity.get("year")
        title = (activity.get("title") or "").strip()
        if not source or not title or not year:
            continue
        if _repeats_new_row(activity, new_rows):
            duplicates += 1
            continue
        twins = [other for other in others if same_activity(activity, other)]
        if any(not self_reported(other) for other in twins):
            duplicates += 1
            continue
        superseded += _supersede_self_reports(twins)
        new_rows.append(
            _cv_row(activity, source=source, ledger_id=ledger_id, year=year, title=title, today=today, counter=id_counter)
        )
    return new_rows, duplicates, superseded


def _close_cv_reviews(queue: list[dict[str, str]], ledger_id: str, stats: ApplyStats) -> None:
    """Mark open ``cv_new`` / ``cv_changed`` items done once their file has been applied."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for item in queue:
        if (
            item.get("ledger_id") == ledger_id
            and item.get("reason") in ("cv_new", "cv_changed")
            and item.get("status") == "open"
        ):
            item["status"] = "done"
            item["detail"] = f"{item.get('detail') or ''} | applied {now}".strip()
            stats.closed_reviews += 1


def _apply_file(
    path: Path,
    *,
    registry: dict[str, dict[str, str]],
    activities: list[dict[str, str]],
    queue: list[dict[str, str]],
    id_changes: list[tuple[str, str, str, str]],
    today: date,
    stats: ApplyStats,
) -> tuple[list[dict[str, str]], int, int]:
    """Replace this file's previous CV rows with the new reading. Returns activities, duplicates, superseded."""
    data = json.loads(path.read_text(encoding="utf-8"))
    ledger_id = _extraction_owner(data, registry)
    if _sources_stale(data, registry):
        stats.skipped_stale.append(ledger_id)
        return activities, 0, 0

    file_origins = {f"cv:{item['source_id']}" for item in data.get("sources", [])}
    old_cv = [row for row in activities if row.get("origin") in file_origins]
    kept = [row for row in activities if row.get("origin") not in file_origins]
    others = [row for row in kept if row.get("ledger_id") == ledger_id]
    _clear_superseded(others)
    new_rows, duplicates, superseded = _rows_from_file(
        data, registry=registry, others=others, ledger_id=ledger_id, today=today
    )
    # A CV line whose key is absent from this extraction is a dropped fact.
    # It is not given a new id: a vanished line is a deletion, not a new activity.
    id_changes.extend(zip_activity_id_changes(old_cv, new_rows, cv_activity_key))
    # ``added`` is an id the ledger did not already hold. Applying the same
    # extraction again rewrites those ids; that rewrite is not a new activity,
    # so a second run of the pipeline can report that nothing was added.
    prior_ids = {row.get("activity_id") for row in old_cv} | {row.get("activity_id") for row in kept}
    stats.added += sum(1 for row in new_rows if row["activity_id"] not in prior_ids)
    _close_cv_reviews(queue, ledger_id, stats)
    return kept + new_rows, duplicates, superseded


def _cv_row(
    activity: dict,
    *,
    source: dict[str, str],
    ledger_id: str,
    year: object,
    title: str,
    today: date,
    counter: dict[str, int],
) -> dict[str, str]:
    """One ledger activity. Education, service, and a future year stay unpublished."""
    section = (activity.get("cv_section") or "other").strip().lower()
    activity_type = activity.get("activity_type") if activity.get("activity_type") in TYPES else "other"
    private = section in PRIVATE_SECTIONS or bool(PRIVATE_TITLE.search(f"{title} {activity.get('role') or ''}"))
    upcoming = bool(activity.get("upcoming"))
    year_number = int(year)
    future = upcoming and year_number > today.year
    publishable = "no" if private or future else "yes"
    role = (activity.get("role") or "").strip()
    if upcoming and year_number == today.year:
        role = f"{role} (예정)".strip()
    venue = (activity.get("venue") or "").strip()
    key = activity_id_key(
        ledger_id=ledger_id,
        source=source["source_id"],
        title=title,
        year=str(year_number),
        activity_type=str(activity_type),
        venue=venue,
    )
    note = "; ".join(
        part
        for part in (
            f"cv_section={section}",
            "upcoming" if activity.get("upcoming") else "",
            " ".join((activity.get("note") or "").split()),
        )
        if part
    )
    collected = (source.get("last_changed_at") or today.isoformat())[:10]
    return {
        "activity_id": take_activity_id(counter, key),
        "ledger_id": ledger_id,
        "title": title,
        "venue": venue,
        "year": str(year_number),
        "activity_type": str(activity_type),
        "role": role,
        "source_url": source.get("url") or "",
        "source_type": "SELF_SUBMITTED",
        "collected_at": collected,
        "publishable": publishable,
        "reviewer_note": note,
        "origin": f"cv:{source['source_id']}",
    }


def _write_id_map(ledger: Ledger, id_changes: list[tuple[str, str, str, str]]) -> None:
    """One map per UTC day. A second apply the same day keeps the first map, so the morning's old-to-new ids stay."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = ledger.config.work / "backups" / f"activity_id_map-{stamp}.csv"
    if path.exists():
        return
    write_csv(
        path=path,
        fields=["old_activity_id", "new_activity_id", "ledger_id", "origin"],
        rows=[
            {"old_activity_id": old, "new_activity_id": new, "ledger_id": lid, "origin": origin}
            for old, new, lid, origin in id_changes
        ],
    )
