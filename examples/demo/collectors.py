# SPDX-License-Identifier: MIT
"""Demo roster collectors for a synthetic field. No real people, no network.

    giye collect --config examples/demo/giye.toml

Spelling variants (김하늘 / Haneul Kim, 이하루 / Haru Lee) stay on separate rows.
Deciding they are the same person is rules X1 and E1–E4, which run in a later stage.
"""

from __future__ import annotations

from html.parser import HTMLParser

from giye.collect import Edition, Person, RosterCollector


class _Sections(HTMLParser):
    """Read ``<section data-year>`` blocks whose ``<li>`` items are roster names."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.editions: list[tuple[int | str, list[str]]] = []
        self._year: str | None = None
        self._in_li = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value for key, value in attrs}
        if tag == "section" and values.get("data-year"):
            self._year = values["data-year"] or ""
            label: int | str = int(self._year) if self._year.isdigit() else self._year
            self.editions.append((label, []))
        elif tag == "li" and self._year is not None:
            self._in_li = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self._in_li:
            name = " ".join("".join(self._buf).split())
            if name and self.editions:
                self.editions[-1][1].append(name)
            self._in_li = False
        elif tag == "section":
            self._year = None

    def handle_data(self, data: str) -> None:
        if self._in_li:
            self._buf.append(data)


def parse_alumni(html: str) -> list[tuple[int | str, list[str]]]:
    """Return ``(year, names)`` for each ``data-year`` section. Shared by the demo pages."""
    parser = _Sections()
    parser.feed(html)
    return parser.editions


class ExampleResidency(RosterCollector):
    frame = "EXAMPLE-RESIDENCY"

    def editions(self):
        url = "https://example.org/residency/alumni"
        page = self.fetch(url)
        for year, names in parse_alumni(page.text):
            people = [Person(name=n) for n in names]
            yield Edition(year=year, people=people, source_url=page.url)


class ExampleWorkshop(RosterCollector):
    frame = "EXAMPLE-WORKSHOP"

    def editions(self):
        url = "https://example.org/workshop/fellows"
        page = self.fetch(url)
        for year, names in parse_alumni(page.text):
            people = [Person(name=n) for n in names]
            yield Edition(year=year, people=people, source_url=page.url)
