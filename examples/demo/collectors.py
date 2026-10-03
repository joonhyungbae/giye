# SPDX-License-Identifier: MIT
"""Demo roster collectors for a synthetic field. No real people, no network.

    giye collect --config examples/demo/giye.toml

Spelling variants (김하늘 / Haneul Kim, 이하루 / Haru Lee) stay on separate rows.
``giye resolve`` applies E1–E4, X1 and the team guard T1.
"""

from __future__ import annotations

from html.parser import HTMLParser

from giye.collect import Edition, Person, RosterCollector


class _Sections(HTMLParser):
    """Read ``<section data-year>`` blocks whose ``<li>`` items are roster names.

    Optional attributes on ``<li>``: ``data-name-en``, ``data-website``, ``data-role``,
    ``data-members``, ``data-aliases``. They are how the demo makes E1–E4 and T1 fire.
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


class ExampleResidency(RosterCollector):
    frame = "EXAMPLE-RESIDENCY"

    def editions(self):
        url = "https://example.org/residency/alumni"
        page = self.fetch(url)
        for year, people in parse_alumni(page.text):
            yield Edition(year=year, people=people, source_url=page.url)


class ExampleWorkshop(RosterCollector):
    frame = "EXAMPLE-WORKSHOP"

    def editions(self):
        url = "https://example.org/workshop/fellows"
        page = self.fetch(url)
        for year, people in parse_alumni(page.text):
            yield Edition(year=year, people=people, source_url=page.url)
