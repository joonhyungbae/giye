# SPDX-License-Identifier: AGPL-3.0-only
"""CV activities the same-person rules read.

Production stores one JSON file per person at ``data/work/cv_extract/<ledger_id>.json``
with an ``activities`` list (``title``, ``venue``, ``year``). That file wins when
it exists. A configured directory of HTML CVs fills in people who have no file
yet, matched by ``data-name-ko`` and ``data-name-en``. The match has to be
unique. Stages 3–7 do not fetch the network; these are local files.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

from giye.config import Config
from giye.ledger.ledger import Ledger


class _CvParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.people: list[dict] = []
        self._person: dict | None = None
        self._in_li = False
        self._li_year = ""
        self._li_venue = ""
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
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
    """Ledger id → extracted activities. JSON for that id wins over HTML."""
    artists = ledger.read("artists")
    found: dict[str, list[dict]] = {}
    directory = config.work / "cv_extract"
    if directory.is_dir():
        for artist in artists:
            path = directory / f"{artist['ledger_id']}.json"
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            found[artist["ledger_id"]] = list(data.get("activities") or [])
    if config.cv_dir is None:
        return found
    for person in read_html_cvs(config.cv_dir):
        lid = _match(artists, person)
        if lid and lid not in found:
            found[lid] = list(person.get("activities") or [])
    return found


def move_extract_file(config: Config, keep: str, drop: str) -> None:
    """Point a dropped person's extraction file at the survivor, when the survivor has none."""
    directory = config.work / "cv_extract"
    source = directory / f"{drop}.json"
    dest = directory / f"{keep}.json"
    if source.is_file() and not dest.is_file():
        dest.write_text(source.read_text(encoding="utf-8").replace(drop, keep), encoding="utf-8")
        source.unlink()


def _match(artists: list[dict], person: dict) -> str | None:
    ko = (person.get("name_ko") or "").strip()
    en = (person.get("name_en") or "").strip()
    if not ko and not en:
        return None
    hits = []
    for artist in artists:
        if ko and (artist.get("name_ko") or "") != ko:
            continue
        if en and (artist.get("name_en") or "") != en:
            continue
        hits.append(artist["ledger_id"])
    if len(hits) == 1:
        return hits[0]
    return None
