# SPDX-License-Identifier: MIT
"""Ledger table schemas.

People live in ``artists.csv``. ``ledger_id`` is the internal key (``LED-…``).
``gy_id`` (``GY-000001``) is the published id: permanent, never reused, never the
row's position in the file. A merge removes the dropped row and records its
``gy_id`` in ``gy_retired.csv``.

Column order matches the production ledger so a file written here is readable
there. Pipe-separated cells (``aliases``) use ``split_pipe`` / ``join_pipe``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

# One row per person. gy_id stays with this row for the life of the archive.
ARTISTS_FIELDS = [
    "ledger_id",
    "gy_id",
    "name_ko",
    "name_en",
    "aliases",
    "affiliation",
    "active_since",
    "country",
    "region",
    "category",
    "field",
    "cv_link_ok",
    "frame_status",
    "verification",
    "status",
    "source_url",
    "source_type",
    "collected_at",
    "reviewer_note",
    "updated_at",
]

# One dated fact. activity_id is a uuid5 of the row's identity key (see ids.py),
# not a random value, so the same fact keeps its id across runs.
ACTIVITIES_FIELDS = [
    "activity_id",
    "ledger_id",
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
    "origin",
]

# External links and the last time they were checked.
LINKS_FIELDS = [
    "link_id",
    "ledger_id",
    "label",
    "url",
    "link_type",
    "last_checked_at",
    "http_status",
    "page_title",
    "is_dead",
    "origin",
]

# Who is on which programme. The key is (ledger_id, frame_code): editions of one
# programme are activities, not extra membership rows.
MEMBERSHIP_FIELDS = [
    "ledger_id",
    "frame_code",
    "source_url",
    "collected_at",
]

# Items no rule can decide. A person keeps their row until a human closes it.
REVIEW_FIELDS = [
    "queue_id",
    "ledger_id",
    "reason",
    "detail",
    "status",
    "created_at",
]

# Scientists and engineers recorded beside an artist. They are not artists and
# do not enter artists.csv or frame counts.
COLLABORATORS_FIELDS = [
    "collaborator_id",
    "name_ko",
    "name_en",
    "affiliation",
    "lab",
    "role",
    "source_url",
    "note",
]

# One row per (artist, collaborator, frame edition).
COLLABORATIONS_FIELDS = [
    "collaboration_id",
    "ledger_id",
    "collaborator_id",
    "frame_code",
    "year",
    "topic",
    "source_url",
    "collected_at",
    "publishable",
    "origin",
]

# Where a person publishes their own CV. One row per source (for example ko and en).
# A CV-derived activity's origin is ``cv:<source_id>`` and the row belongs to
# whatever ledger_id this table currently stores for that source.
CV_SOURCES_FIELDS = [
    "source_id",
    "ledger_id",
    "lang",
    "kind",
    "url",
    "fetch_url",
    "active",
    "last_pulled_at",
    "last_status",
    "last_changed_at",
    "content_sha256",
    "snapshot_path",
    "note",
]

# A published id is never reissued. merged_into_ledger_id is rewritten to the
# final survivor when that survivor is itself merged, so one lookup redirects.
GY_RETIRED_FIELDS = ["gy_id", "merged_into_ledger_id", "retired_at"]

# In or out of the archive's scope, with the criterion that decided it.
SCOPE_FIELDS = ["ledger_id", "name", "scope", "criterion", "reason", "decided_by", "decided_at"]

# table name → (filename, columns). "artists" is the people table.
TABLES: dict[str, tuple[str, list[str]]] = {
    "artists": ("artists.csv", ARTISTS_FIELDS),
    "activities": ("activities.csv", ACTIVITIES_FIELDS),
    "links": ("links.csv", LINKS_FIELDS),
    "frame_membership": ("frame_membership.csv", MEMBERSHIP_FIELDS),
    "review_queue": ("review_queue.csv", REVIEW_FIELDS),
    "collaborators": ("collaborators.csv", COLLABORATORS_FIELDS),
    "collaborations": ("collaborations.csv", COLLABORATIONS_FIELDS),
    "cv_sources": ("cv_sources.csv", CV_SOURCES_FIELDS),
    "gy_retired": ("gy_retired.csv", GY_RETIRED_FIELDS),
    "scope": ("scope.csv", SCOPE_FIELDS),
}


def empty_row(fields: Sequence[str], **values: object) -> dict[str, str]:
    """A row with every schema column, missing values as empty strings."""
    row = {name: "" for name in fields}
    for key, value in values.items():
        row[key] = "" if value is None else str(value)
    return row


def split_pipe(value: str | None) -> list[str]:
    """Split a pipe-separated cell. Empty pieces are dropped."""
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def join_pipe(values: Iterable[str]) -> str:
    """Join pieces with pipes, dropping empties and keeping the first of any duplicate."""
    return "|".join(dict.fromkeys(item for item in values if item))
