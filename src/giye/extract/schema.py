# SPDX-License-Identifier: AGPL-3.0-only
"""Activity schema for a CV extraction.

The shape is the production ``Entry`` / ``Extraction`` models
(``scripts/extract_cvs_llm.py``). ``extra="forbid"`` is required there because
the API schema sets ``additionalProperties: false``. A response that does not
match — an invented ``activity_type``, a non-integer year, an extra field — is
rejected as a whole, the same way a ``ValidationError`` made production skip
that artist.

A row whose ``source_id`` is not one of the documents sent with the prompt is
dropped after validation (production keeps ``source_id in ids`` only). That is
the coded check against a row the model attached to a document it was not
given. Production does not also test that the year or the venue string occurs
in the CV text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Section = Literal[
    "exhibition",
    "performance",
    "screening",
    "festival",
    "award",
    "grant",
    "residency",
    "commission",
    "collection",
    "project",
    "workshop",
    "talk",
    "publication",
    "press",
    "education",
    "employment",
    "teaching",
    "scholarship",
    "service",
    "other",
]
ActivityType = Literal[
    "solo_exhibition",
    "group_exhibition",
    "screening",
    "performance",
    "festival",
    "online_release",
    "award",
    "residency",
    "other",
]


class Entry(BaseModel):
    """One dated CV line. Every field is required; unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    title: str
    venue: str
    year: int
    activity_type: ActivityType
    role: str
    cv_section: Section
    upcoming: bool
    source_id: str


class Extraction(BaseModel):
    """The whole model response: a list of entries and nothing else."""

    model_config = ConfigDict(extra="forbid")

    activities: list[Entry] = Field(default_factory=list)


def parse_extraction(raw: str) -> Extraction:
    """Validate ``raw`` JSON. Raises ``ValidationError`` when the schema rejects it."""
    return Extraction.model_validate_json(raw)


def without_unknown_sources(extraction: Extraction, source_ids: set[str]) -> tuple[Extraction, list[Entry]]:
    """Drop rows whose ``source_id`` was not in the documents just read.

    Returns ``(kept, dropped)``. Production filters before it writes the
    extraction file, so a made-up source id never becomes a ledger row.
    """
    kept: list[Entry] = []
    dropped: list[Entry] = []
    for row in extraction.activities:
        if row.source_id in source_ids:
            kept.append(row)
        else:
            dropped.append(row)
    return extraction.model_copy(update={"activities": kept}), dropped
