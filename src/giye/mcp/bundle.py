# SPDX-License-Identifier: AGPL-3.0-only
"""Load one career bundle into memory.

What: reads the eight files written by ``giye.career`` and indexes them for
tool look-ups. The columns are whatever ``manifest.json`` lists.

Why: the server must not open the ledger, the processed tables or the site.
The bundle directory is the whole contract. A wrong shape fails at startup
rather than as a made-up number later.

How to run: imported by ``python -m giye.mcp serve``. This module is not a
script.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

# The phase 1 writer uses these names. vocab and manifest are JSON; the rest
# are the tables whose columns the manifest records.
TABLES = (
    "reference_position.csv",
    "next_window.csv",
    "programme_profile.csv",
    "programme_entry.csv",
    "programme_transitions.csv",
    "field_trend.csv",
)
FILES = TABLES + ("vocab.json", "manifest.json")

_MISSING = object()


class BundleError(Exception):
    """The directory is not a career bundle this server can read. One line."""


class Bundle:
    """The tables, the vocabulary and the manifest, already checked."""

    def __init__(self, directory: Path, manifest: dict, vocab: dict, tables: dict[str, list[dict[str, str]]]):
        self.directory = directory
        self.manifest = manifest
        self.vocab = vocab
        self.tables = tables
        self.k = int(manifest["k"])
        self.pct_min = int(manifest["pct_min"])
        self.territory = str(manifest.get("territory") or "")
        self.current_year = int(manifest["current_year"])
        ledger = manifest["ledger_version"]
        built = str(manifest["built_at"])
        # ledger_version is an object (phase 1). The wire version is the rules
        # id plus the build day, so a client can cite which bundle answered.
        self.version = f"{ledger['rules_version']}/{built[:10]}"
        self._reference = {
            (row["career_age"], row["generation"], row["measure"]): row for row in tables["reference_position.csv"]
        }
        self._next: dict[tuple[str, str], list[dict[str, str]]] = {}
        for row in tables["next_window.csv"]:
            self._next.setdefault((row["age_band"], row["generation"]), []).append(row)
        self._profile = {row["programme"]: row for row in tables["programme_profile.csv"]}
        self._entry: dict[str, list[dict[str, str]]] = {}
        for row in tables["programme_entry.csv"]:
            self._entry.setdefault(row["programme"], []).append(row)
        self._transitions = tables["programme_transitions.csv"]
        self._trend: dict[str, list[dict[str, str]]] = {}
        for row in tables["field_trend.csv"]:
            self._trend.setdefault(row["measure"], []).append(row)

    def reference_row(self, age: int, generation: str, measure: str) -> dict[str, str] | None:
        """One reference-position cell, or None when that key was not written."""
        return self._reference.get((str(age), generation, measure))

    def next_rows(self, band: str, generation: str, mix_bucket: str | None) -> list[dict[str, str]]:
        """Next-window rows for a band and generation, optionally one mix bucket."""
        rows = self._next.get((band, generation), [])
        if not mix_bucket:
            return list(rows)
        return [row for row in rows if row["mix_bucket"] == mix_bucket]

    def profile_row(self, programme: str) -> dict[str, str] | None:
        return self._profile.get(programme)

    def entry_rows(self, programme: str) -> list[dict[str, str]]:
        return list(self._entry.get(programme, []))

    def transition_rows(self, programme: str) -> list[dict[str, str]]:
        """Transitions that start or end at ``programme``, in file order."""
        return [
            row
            for row in self._transitions
            if row["programme_from"] == programme or row["programme_to"] == programme
        ]

    def trend_rows(self, measure: str) -> list[dict[str, str]] | None:
        """Rows for ``measure``, or None when the measure is not in the table.

        An empty list would look like a measure that exists and has no
        generations. Missing and empty are different: only a known measure
        answers.
        """
        found = self._trend.get(measure, _MISSING)
        if found is _MISSING:
            return None
        return list(found)

    def measure_names(self, table: str) -> list[str]:
        """Published measure or outcome names for one table, from ``vocab.json``."""
        return list(self.vocab["measures"][table])

    def programme_codes(self) -> list[str]:
        return [item["code"] for item in self.vocab["programmes"]]

    def suppressed_total(self) -> int:
        """Cells the build withheld under k, summed across tables.

        Tools that do not select a cell still report this, so an answer from a
        bundle that publishes nothing names that fact.
        """
        cells = self.manifest["counts"]["suppressed_cells"]
        return sum(int(value) for value in cells.values())


def load_bundle(directory: str | Path) -> Bundle:
    """Read and check ``directory``. Raises ``BundleError`` on a bad shape."""
    path = Path(directory)
    if not path.is_dir():
        raise BundleError(f"bundle directory not found: {path}")
    missing = [name for name in FILES if not (path / name).is_file()]
    if missing:
        raise BundleError("bundle is missing " + ", ".join(missing))
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        vocab = json.loads((path / "vocab.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BundleError(f"bundle JSON is not readable: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("bundle_format") != 1:
        raise BundleError("bundle_format must be 1")
    tables_meta = manifest.get("tables")
    if not isinstance(tables_meta, dict):
        raise BundleError("manifest has no tables")
    tables: dict[str, list[dict[str, str]]] = {}
    for name in TABLES:
        meta = tables_meta.get(name)
        if not isinstance(meta, dict) or not isinstance(meta.get("columns"), list):
            raise BundleError(f"manifest does not list columns for {name}")
        expected = [str(column) for column in meta["columns"]]
        with (path / name).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            header = list(reader.fieldnames or [])
            if header != expected:
                raise BundleError(f"columns of {name} do not match the manifest")
            tables[name] = [{key: "" if value is None else str(value) for key, value in row.items()} for row in reader]
    return Bundle(path, manifest, vocab, tables)
