# SPDX-License-Identifier: AGPL-3.0-only
"""Audit-sheet CSV: one row per case, empty ``label`` and ``note`` until a coder saves them.

The three commands share this file. ``sample`` creates it, ``score`` reads it,
and ``serve`` rewrites ``label`` and ``note`` in place. A write goes to a
temporary file in the same directory and is renamed over the sheet, so a
crash mid-save leaves the previous sheet.
"""

from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

# Buttons and keys 1/2/3 write these strings. ``cannot tell`` is two words, matching the protocol.
LABELS = ("correct", "incorrect", "cannot tell")

_COMMON = ["item_id", "kind", "stratum", "seed", "label", "note"]

COLUMNS = {
    "cv": _COMMON
    + [
        "activity_id",
        "ledger_id",
        "gy_id",
        "name_ko",
        "name_en",
        "activity_type",
        "title",
        "venue",
        "year",
        "role",
        "source_id",
        "source_url",
        "excerpt",
    ],
    "people": _COMMON
    + [
        "kept_ledger_id",
        "kept_gy_id",
        "kept_name_ko",
        "kept_name_en",
        "kept_rosters",
        "dropped_ledger_id",
        "dropped_gy_id",
        "dropped_name_ko",
        "dropped_name_en",
        "dropped_rosters",
        "evidence",
        "rule",
    ],
    "venues": _COMMON
    + [
        "rule",
        "kept_spelling",
        "kept_row_count",
        "joined_spelling",
        "joined_row_count",
        "kept_examples",
        "joined_examples",
    ],
    "attach": _COMMON
    + [
        "ledger_id",
        "gy_id",
        "name_ko",
        "name_en",
        "aliases",
        "attach_rule",
        "frame_code",
        "source_url",
        "collected_at",
        "other_rosters",
    ],
    "splink": _COMMON
    + [
        "match_probability",
        "left_ledger_id",
        "left_gy_id",
        "left_name_ko",
        "left_name_en",
        "left_rosters",
        "right_ledger_id",
        "right_gy_id",
        "right_name_ko",
        "right_name_en",
        "right_rosters",
        "shared_editions",
        "shared_websites",
    ],
}

KINDS = tuple(COLUMNS)


def columns_for(kind: str) -> list[str]:
    """Column names for an audit sheet of ``kind`` (see ``COLUMNS``)."""
    try:
        return list(COLUMNS[kind])
    except KeyError:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}") from None


def read_sheet(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Header and rows. A missing column is an error; an empty file is not a sheet."""
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        if "item_id" not in fields or "label" not in fields or "note" not in fields:
            raise ValueError(f"{path} is not an audit sheet (need item_id, label, note)")
        rows = []
        for row in reader:
            rows.append({key: (value if value is not None else "") for key, value in row.items()})
        return fields, rows


def write_sheet(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
    """Replace ``path`` atomically. Only ``fields`` are written, in that order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: "" if row.get(key) is None else row.get(key, "") for key in fields})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def update_label(path: Path, item_id: str, label: str, note: str) -> None:
    """Set one row's ``label`` and ``note``. Other cells stay as they were."""
    if label not in LABELS:
        raise ValueError(f"label must be one of {', '.join(LABELS)}")
    fields, rows = read_sheet(path)
    found = False
    for row in rows:
        if row.get("item_id") == item_id:
            row["label"] = label
            row["note"] = note
            found = True
            break
    if not found:
        raise KeyError(item_id)
    write_sheet(path, fields, rows)
