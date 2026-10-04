# SPDX-License-Identifier: AGPL-3.0-only
"""Demo roster collectors for a synthetic field. No real people, no network.

    giye collect --config examples/demo/giye.toml

김하늘 is on the residency roster and Haneul Kim on the workshop roster. ``giye demo``
merges that pair when the English CV names the residency (X1+E2). Kim Haneul on
the forum shares the name key and has no such evidence, so the pair is queued.
``giye resolve`` applies E1–E4, X1 and the team guard T1.

Membership is ``<FRAME>-<YYYY>`` (``EXAMPLE-RESIDENCY-2019``), the production
edition code. Rule R1 reads that suffix and does not fall back to the activity
year, so the bare frame code would leave the demo ring undated.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from giye.collect import Edition, Person, RosterCollector
from giye.ledger import Ledger

_YEAR = re.compile(r"\d{4}$")


class _Sections(HTMLParser):
    """Read ``<section data-year>`` blocks whose ``<li>`` items are roster names.

        Optional attributes on ``<li>``: ``data-name-en``, ``data-website``, ``data-role``,
        ``data-members``, ``data-aliases``, ``data-identity``. They are how the demo
        makes E1–E4, T1, and a collector identity pin fire.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.editions: list[tuple[int | str, list[Person]]] = []
        self._year: str | None = None
        self._in_li = False
        self._attrs: dict[str, str] = {}
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "section" and values.get("data-year"):
            self._year = values["data-year"]
            label: int | str = int(self._year) if self._year.isdigit() else self._year
            self.editions.append((label, []))
        elif tag == "li" and self._year is not None:
            self._in_li = True
            self._attrs = values
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self._in_li:
            name = " ".join("".join(self._buf).split())
            if name and self.editions:
                attrs = self._attrs
                self.editions[-1][1].append(
                    Person(
                        name=name,
                        name_en=attrs.get("data-name-en", ""),
                        website=attrs.get("data-website", ""),
                        role=attrs.get("data-role", ""),
                        members=attrs.get("data-members", ""),
                        aliases=attrs.get("data-aliases", ""),
                        identity=attrs.get("data-identity", ""),
                    )
                )
            self._in_li = False
        elif tag == "section":
            self._year = None

    def handle_data(self, data: str) -> None:
        if self._in_li:
            self._buf.append(data)


def parse_alumni(html: str) -> list[tuple[int | str, list[Person]]]:
    """Return ``(year, people)`` for each ``data-year`` section. Shared by the demo pages."""
    parser = _Sections()
    parser.feed(html)
    return parser.editions


def edition_code(frame: str, year: int | str) -> str:
    """``FRAME-YYYY`` when ``year`` is four digits, otherwise the bare frame.

    A label that is not a year must not grow a fake suffix. R1 would treat that
    suffix as an entry year.
    """
    text = str(year)
    if _YEAR.fullmatch(text):
        return f"{frame}-{text}"
    return frame


class _EditionRoster(RosterCollector):
    """Write one membership per edition year, not one per programme.

    ``apply_roster`` stores the code it is given as the membership and as the
    activity origin. One call per edition keeps those two strings equal, which
    is what team expansion and the published roster role match on. The snapshot
    path stays the bare frame: ``fetch`` uses ``self.frame``, not the edition.
    """

    def run(self, *, collected_at: str | None = None) -> list[dict[str, str]]:
        stamp = collected_at or datetime.now(timezone.utc).date().isoformat()
        rows: list[dict[str, str]] = []
        batches: dict[str, list[dict[str, str]]] = {}
        for edition in self.editions():
            code = edition_code(self.frame, edition.year)
            batch = batches.setdefault(code, [])
            for person in edition.people:
                row = {
                    "frame_code": code,
                    "year": str(edition.year),
                    "name": person.name,
                    "name_ko": person.name_ko,
                    "name_en": person.name_en,
                    "source_url": edition.source_url,
                    "collected_at": stamp,
                    "website": person.website,
                    "role": person.role,
                    "members": person.members,
                    "aliases": person.aliases,
                    "identity": person.identity,
                }
                rows.append(row)
                batch.append(row)
        ledger = Ledger.open(self.config)
        for code, batch in batches.items():
            ledger.apply_roster(code, batch, task="collect")
        self.write_csv(rows)
        return rows


class ExampleResidency(_EditionRoster):
    frame = "EXAMPLE-RESIDENCY"

    def editions(self):
        url = "https://example.org/residency/alumni"
        page = self.fetch(url)
        for year, people in parse_alumni(page.text):
            yield Edition(year=year, people=people, source_url=page.url)


class ExampleWorkshop(_EditionRoster):
    frame = "EXAMPLE-WORKSHOP"

    def editions(self):
        url = "https://example.org/workshop/fellows"
        page = self.fetch(url)
        for year, people in parse_alumni(page.text):
            yield Edition(year=year, people=people, source_url=page.url)


class ExampleForum(_EditionRoster):
    """One guest, Kim Haneul, who shares a name key with 김하늘 and no evidence."""

    frame = "EXAMPLE-FORUM"

    def editions(self):
        url = "https://example.org/forum/guests"
        page = self.fetch(url)
        for year, people in parse_alumni(page.text):
            yield Edition(year=year, people=people, source_url=page.url)
