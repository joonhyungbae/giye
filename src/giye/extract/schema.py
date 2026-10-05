# SPDX-License-Identifier: AGPL-3.0-only
"""Activity schema for a CV extraction.

``Entry`` and ``Extraction`` are the objects the model must return.
``extra="forbid"`` matches the API schema's ``additionalProperties: false``.
A response that does not match — an invented ``activity_type``, a non-integer
year, an unknown field — is rejected as a whole, so that artist is skipped
rather than half-written.

A row whose ``source_id`` is not one of the documents sent with the prompt is
dropped after validation. That is the check against a row the model attached
to a document it was not given. The year and the venue are not also required
to occur in the CV text: the schema checks shape, not quotation.
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


def _model_facing_schema(schema: dict) -> None:
    """The JSON schema sent to a model: every field required, no ``note``.

    ``note`` and a missing ``role`` are accepted when a stored reading is
    parsed (see ``Entry``), but a live call still asks for exactly the eight
    fields the prompt names. The schema sent to a provider is therefore the
    same as before ``note`` was accepted.
    """
    properties = schema.get("properties", {})
    properties.pop("note", None)
    properties.get("role", {}).pop("default", None)
    schema["required"] = list(properties)
    # The class docstring explains the lenient parse; the model is told the strict contract.
    schema["description"] = "One dated CV line. Every field is required; unknown fields are rejected."


class Entry(BaseModel):
    """One dated CV line. Unknown fields are rejected.

    Two fields are lenient, because the reference archive's readings use them
    and the replay cache stores those readings verbatim. ``note`` is an
    optional comment on the row (the original wording, why a year was read
    that way); apply appends it to ``reviewer_note``. ``role`` defaults to ""
    when a reading omitted it, which is what the prompt asks for when no role
    is stated. Any other extra field (``reviewer_note``, for one) still rejects
    the whole response.
    """

    model_config = ConfigDict(extra="forbid", json_schema_extra=_model_facing_schema)

    title: str
    venue: str
    year: int
    activity_type: ActivityType
    role: str = ""
    cv_section: Section
    upcoming: bool
    source_id: str
    note: str = ""


class Extraction(BaseModel):
    """The whole model response: a list of entries and nothing else."""

    model_config = ConfigDict(extra="forbid")

    activities: list[Entry] = Field(default_factory=list)


def parse_extraction(raw: str) -> Extraction:
    """Validate ``raw`` JSON. Raises ``ValidationError`` when the schema rejects it."""
    return Extraction.model_validate_json(raw)


def without_unknown_sources(extraction: Extraction, source_ids: set[str]) -> tuple[Extraction, list[Entry]]:
    """Drop rows whose ``source_id`` was not in the documents just read.

    Returns ``(kept, dropped)``. Filtering happens before the extraction file
    is written, so a made-up source id never becomes a ledger row.
    """
    kept: list[Entry] = []
    dropped: list[Entry] = []
    for row in extraction.activities:
        if row.source_id in source_ids:
            kept.append(row)
        else:
            dropped.append(row)
    return extraction.model_copy(update={"activities": kept}), dropped
