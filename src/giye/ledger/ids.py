# SPDX-License-Identifier: AGPL-3.0-only
"""Permanent person ids and content-derived activity ids.

``gy_id`` is issued once, as one past the highest number ever used, including
ids that a merge has retired. It is not the row index: sorting the file does
not renumber anyone, and a retired number is never given to someone else.

Activity ids are uuid5 of a fixed namespace so the same fact hashes to the
same id in every process. Do not change ``ACTIVITY_NAMESPACE`` or the key
order: either change would change every activity id (docs/RULES.md).
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

# Derived from a fixed URL so every process computes the same namespace.
# Changing this string changes every activity id.
ACTIVITY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://giye.org/ns/activity")
_ACTIVITY_KEY_SEP = "\x1f"  # does not occur in ledger text fields, so parts cannot bleed together


def norm_activity_title(value: str) -> str:
    """Title as identity: lower case, punctuation and underscores removed.

    Case and punctuation are how a line was typed, not a different fact.
    """
    return re.sub(r"[\W_]+", "", (value or "").lower())


def activity_id_key(
    *,
    ledger_id: str,
    source: str,
    title: str,
    year: str,
    activity_type: str,
    venue: str,
    origin: str = "",
) -> str:
    """Identity string hashed into an activity id.

    Parts, in order, joined by U+001F:

    ledger_id
        Which person. The same listing for two people is two facts.
    source
        Which document the row was read from. A CV row uses its source_id
        (the ko and en readings are separate rows). A collector or import row
        uses the source URL, because those rows have no source_id.
    normalised title
        The work or event name. See ``norm_activity_title``.
    year
        A later edition is a different fact. Empty when the source gives no year.
    activity_type
        The same title can be filed as two types.
    venue
        One row per stop of a tour. Stripped only; not passed through the title
        normaliser, so a differently spelled stop stays a different fact.
    origin
        Only for rows a collector rewrites (the frame code). A collector run
        replaces that frame's rows and no others, so the id has to be
        recomputable from that frame alone. Without the frame code, two frames
        that cite the same URL would hash to one id. CV rows leave this empty:
        source_id already names the document.
    """
    parts = [
        ledger_id,
        source,
        norm_activity_title(title),
        str(year or ""),
        activity_type or "",
        (venue or "").strip(),
    ]
    if origin:
        parts.append(origin)
    return _ACTIVITY_KEY_SEP.join(parts)


def activity_id_for(key: str, ordinal: int = 0) -> str:
    """uuid5 of the key. Ordinal 0 is the first row of that key.

    A later row with the same key, for the same person, in the same write,
    gets the ordinal appended (1, 2, …) so the two rows do not share an id.
    The ordinal is the position in write order: extraction order for a CV,
    the collector's activity list for a frame. The same input order yields
    the same ids.
    """
    material = key if ordinal == 0 else f"{key}{_ACTIVITY_KEY_SEP}{ordinal}"
    return str(uuid.uuid5(ACTIVITY_NAMESPACE, material))


def take_activity_id(counter: dict[str, int], key: str) -> str:
    """Mint the next id for this key and advance the per-key counter."""
    n = counter.get(key, 0)
    counter[key] = n + 1
    return activity_id_for(key, n)


def mint_id(key: str) -> str:
    """uuid5 of an arbitrary stable key.

    Not an activity row. The key's prefix (``site-frame``, ``site-version``,
    ``site-link``) keeps it out of the activity-id use of the same namespace.
    """
    return str(uuid.uuid5(ACTIVITY_NAMESPACE, key))


def gy_number(gy: str, *, prefix: str = "GY") -> int:
    """The integer in ``PREFIX-000123``, or 0 when ``gy`` is not that shape.

    The prefix comes from ``archive.id_prefix`` (default ``GY``) so another
    field can use another code. The width stays six digits. A string that is
    not that shape is 0, so it is not a gap to fill.
    """
    match = re.fullmatch(rf"{re.escape(prefix)}-(\d{{6}})", gy or "")
    return int(match.group(1)) if match else 0


def allocate_gy_id(existing: Iterable[str], *, prefix: str = "GY") -> str:
    """The next permanent id: one past the highest number in ``existing``.

    ``existing`` must include live rows and retired ids. A gap is not filled.
    Filling it would reuse a number that might still be cited. The result does
    not depend on how the rows are ordered in the file.
    """
    next_no = max((gy_number(value, prefix=prefix) for value in existing), default=0) + 1
    return f"{prefix}-{next_no:06d}"


def cv_activity_key(row: dict[str, Any]) -> str:
    """Key of a CV-derived row. ``source`` is the source_id stored after ``cv:``."""
    origin = row.get("origin") or ""
    source = origin[3:] if origin.startswith("cv:") else ""
    return activity_id_key(
        ledger_id=row.get("ledger_id") or "",
        source=source,
        title=row.get("title") or "",
        year=row.get("year") or "",
        activity_type=row.get("activity_type") or "",
        venue=row.get("venue") or "",
    )


def collector_activity_key(row: dict[str, Any], *, origin: str | None = None) -> str:
    """Key of a collector-written row. ``source`` is the source URL; ``origin`` is the frame code."""
    frame = row.get("origin") or "" if origin is None else origin
    return activity_id_key(
        ledger_id=row.get("ledger_id") or "",
        source=row.get("source_url") or "",
        title=row.get("title") or "",
        year=row.get("year") or "",
        activity_type=row.get("activity_type") or "other",
        venue=row.get("venue") or "",
        origin=frame,
    )


def reissue_frame_activity_ids(
    rows: list[dict[str, Any]], frame_origins: set[str]
) -> list[tuple[str, str, str, str]]:
    """Give collector rows a deterministic id, in file order within each frame.

    A row is reissued only when its origin is a frame code. Collectors delete
    those rows and insert them again on every run, so a random id would change
    every run. Survey and other one-time origins are not frame codes; nothing
    rewrites them, so their current id stays.

    Returns ``(old_id, new_id, ledger_id, origin)`` for rows whose id changed.
    """
    # Ordinal is per (person, frame, key) in the order the rows already have.
    # A later collector run of that one frame walks its own list; with no
    # duplicate key inside the frame the ordinal is 0 either way.
    counters: dict[tuple[str, str], dict[str, int]] = {}
    changes: list[tuple[str, str, str, str]] = []
    for row in rows:
        origin = row.get("origin") or ""
        if origin.startswith("cv:") or origin not in frame_origins:
            continue
        lid = row.get("ledger_id") or ""
        key = collector_activity_key(row, origin=origin)
        counter = counters.setdefault((lid, origin), {})
        new_id = take_activity_id(counter, key)
        old_id = row.get("activity_id") or ""
        if old_id != new_id:
            changes.append((old_id, new_id, lid, origin))
            row["activity_id"] = new_id
    return changes


def zip_activity_id_changes(
    old_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
    key_of: Callable[[dict[str, Any]], str],
) -> list[tuple[str, str, str, str]]:
    """Pair old ids to the new ids of the same key.

    Write order on each side. One new row covers every old row of that key
    (a re-apply can collapse duplicates). Old rows with no new row of that
    key are dropped facts and are not mapped.
    """
    old_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    new_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in old_rows:
        old_by[key_of(row)].append(row)
    for row in new_rows:
        new_by[key_of(row)].append(row)
    changes: list[tuple[str, str, str, str]] = []
    for key, olds in old_by.items():
        news = new_by.get(key) or []
        if not news:
            continue
        for index, old in enumerate(olds):
            new = news[index] if index < len(news) else news[-1]
            old_id = old.get("activity_id") or ""
            new_id = new.get("activity_id") or ""
            if old_id != new_id:
                changes.append((old_id, new_id, old.get("ledger_id") or "", old.get("origin") or ""))
    return changes
