# SPDX-License-Identifier: AGPL-3.0-only
"""CV activities the same-person rules read.

Extraction files live at ``data/work/cv_extract/<ledger_id>.json`` with an
``activities`` list (``title``, ``venue``, ``year``, ``source_id``). The file
name is the record the CV was read for, not its owner. A line belongs to the
owner of its CV source in the ledger (``cv_sources.ledger_id``), followed
through merges (``merged <ledger id>`` on the kept row). A line with no
registered source belongs to the file's ``ledger_id`` (or its name), followed
the same way. Why (software review, round 6, MAJOR-3): indexing by file name
dropped the absorbed record's CV after a merge whenever the survivor had a
file of its own, and after the ledger was restored from its backups a renamed
file still gave one person's CV to the other. A merge therefore no longer
renames an extraction file; the ledger says who owns each CV.

A configured directory of HTML CVs fills in people who have no extraction
yet, matched by ``data-name-ko`` and ``data-name-en``. The match has to be
unique so two people are not given the same page. Resolution reads only these
local files.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

from giye.config import Config
from giye.ledger.ledger import Ledger


class _CvParser(HTMLParser):
    """One ``<article>`` per person and one ``<li>`` per activity, from a local HTML CV."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.people: list[dict] = []
        self._person: dict | None = None
        self._in_li = False
        self._li_year = ""
        self._li_venue = ""
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open a person on ``article`` or an activity line on ``li``."""
        values = {key: value or "" for key, value in attrs}
        if tag == "article":
            self._person = {
                "name_ko": values.get("data-name-ko", ""),
                "name_en": values.get("data-name-en", ""),
                "activities": [],
            }
            self.people.append(self._person)
        elif tag == "li" and self._person is not None:
            self._in_li = True
            self._buf = []
            self._li_year = values.get("data-year", "")
            self._li_venue = values.get("data-venue", "")

    def handle_endtag(self, tag: str) -> None:
        """Close an activity line or the person article."""
        if tag == "li" and self._in_li and self._person is not None:
            title = " ".join("".join(self._buf).split())
            if title or self._li_year:
                self._person["activities"].append(
                    {"title": title, "venue": self._li_venue, "year": self._li_year}
                )
            self._in_li = False
        elif tag == "article":
            self._person = None

    def handle_data(self, data: str) -> None:
        """Keep text that sits inside the current activity line."""
        if self._in_li:
            self._buf.append(data)


def read_html_cvs(directory: Path) -> list[dict]:
    """Every ``*.html`` / ``*.htm`` under ``directory`` (not recursive into hidden names)."""
    if not directory.is_dir():
        return []
    people: list[dict] = []
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() not in {".html", ".htm"} or not path.is_file():
            continue
        parser = _CvParser()
        parser.feed(path.read_text(encoding="utf-8"))
        for person in parser.people:
            person["path"] = str(path)
            people.append(person)
    return people


def load_cv_activities(ledger: Ledger, config: Config) -> dict[str, list[dict]]:
    """Living ledger id → extracted activities, by CV ownership in the ledger. Extraction wins over HTML."""
    # Imported here: giye.resolve.candidates is a sibling module of this package's __init__.
    from giye.resolve.candidates import absorption_map

    artists = ledger.read("artists")
    live = {artist["ledger_id"] for artist in artists}
    absorbed = absorption_map(artists)

    def owner_of(lid: str) -> str:
        """The living record that holds ``lid`` now, or empty."""
        lid = absorbed.get(lid, lid)
        return lid if lid in live else ""

    sources = ledger.read("cv_sources") if ledger.path("cv_sources").exists() else []
    source_owner = {row["source_id"]: owner_of(row.get("ledger_id") or "") for row in sources if row.get("source_id")}
    found: dict[str, list[dict]] = {}
    directory = config.work / "cv_extract"
    files: list[tuple[str, dict]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                files.append((path.stem, data))
    # The apply stage's rule: a file left under a retired id whose sources a
    # living record's file already covers is an older reading, not a second CV.
    covered = {
        str(item.get("source_id"))
        for stem, data in files
        if stem in live
        for item in data.get("sources") or []
        if isinstance(item, dict)
    }
    for stem, data in files:
        listed = {str(item.get("source_id")) for item in data.get("sources") or [] if isinstance(item, dict)}
        if stem not in live and listed and listed <= covered:
            continue
        default = owner_of(str(data.get("ledger_id") or stem))
        for activity in data.get("activities") or []:
            owner = source_owner.get(str(activity.get("source_id") or "")) or default
            if owner:
                found.setdefault(owner, []).append(activity)
    if config.cv_dir is None:
        return found
    for person in read_html_cvs(config.cv_dir):
        hits = _matches(artists, person)
        if len(hits) == 1:
            if hits[0] not in found:
                found[hits[0]] = list(person.get("activities") or [])
            continue
        # A CV the names bind to no record, or to several, is not used. Said
        # aloud, because a skipped CV also skips the E2 merges it would give.
        name = " / ".join(part for part in (person.get("name_ko"), person.get("name_en")) if part) or "(no name)"
        where = Path(str(person.get("path") or "")).name or "cv_dir"
        if hits:
            print(f"NOTICE: CV {where} ({name}) matches {len(hits)} records ({', '.join(hits)}) by name; not used")
        else:
            print(f"NOTICE: CV {where} ({name}) matches no record by name; not used")
    return found


def fold_merged_cvs(ledger: Ledger) -> None:
    """Apply CV rows again after a merge, when the files are extraction output.

    Extract runs before resolve, so each CV is applied while the two records
    are still different people. A repeated event and a CV line that only
    restates the other record's roster row become duplicates only once they
    share a ledger id. Applying again folds them in this run. Files that are
    only an activities list (the resolver's evidence, not an extraction) are
    left alone: apply expects ``ledger_id`` and ``sources``.
    """
    directory = ledger.config.work / "cv_extract"
    if not directory.is_dir():
        return
    paths = sorted(directory.glob("*.json"))
    if not paths:
        return
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict) or "ledger_id" not in data or "sources" not in data:
            return
    # Imported here: giye.extract.apply imports the ledger, not this module.
    from giye.extract.apply import apply_extractions

    apply_extractions(ledger)


def _matches(artists: list[dict], person: dict) -> list[str]:
    """Every ledger id whose given names match the CV's (``name_ko`` and ``name_en`` when present).

    The caller uses the CV only when exactly one record matches; shared or
    missing names match nobody.
    """
    ko = (person.get("name_ko") or "").strip()
    en = (person.get("name_en") or "").strip()
    if not ko and not en:
        return []
    hits = []
    for artist in artists:
        if ko and (artist.get("name_ko") or "") != ko:
            continue
        if en and (artist.get("name_en") or "") != en:
            continue
        hits.append(artist["ledger_id"])
    return hits
