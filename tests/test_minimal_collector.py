# SPDX-License-Identifier: AGPL-3.0-only
"""The collector shown in the paper dates each edition without overriding ``run``."""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from giye.collect.base import Edition, Person, RosterCollector
from giye.config import load
from giye.explore.rim import build_rim_order
from giye.ledger.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]


def parse_alumni(text: str):
    """``(year, names)`` from ``<section data-year>`` blocks. The paper's loop uses the names."""
    import re

    for year, body in re.findall(r'data-year="(\d{4})"(.*?)</section>', text, flags=re.DOTALL):
        yield year, re.findall(r"<li>(.*?)</li>", body, flags=re.DOTALL)


class ExampleResidency(RosterCollector):
    frame = "EXAMPLE-RESIDENCY"

    def editions(self):
        url = "https://example.org/residency/alumni"
        page = self.fetch(url)
        for year, names in parse_alumni(page.text):
            people = [Person(name=n) for n in names]
            yield Edition(year=year, people=people, source_url=page.url)


def test_paper_collector_dates_membership_and_the_rim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom(self, url, *args, **kwargs):
        raise AssertionError(f"network used: {url}")

    monkeypatch.setattr(requests.Session, "get", boom)
    fixtures = tmp_path / "fixtures"
    page = fixtures / "residency"
    page.mkdir(parents=True)
    (fixtures / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (page / "alumni.html").write_text(
        "<section data-year=\"2019\"><ul><li>김하늘</li></ul></section>\n"
        "<section data-year=\"2020\"><ul><li>한별</li></ul></section>\n",
        encoding="utf-8",
    )
    frames = (ROOT / "tests" / "fixtures" / "frames_valid.yml").as_posix()
    config_path = tmp_path / "giye.toml"
    config_path.write_text(
        f"""
[archive]
name = "Synthetic media-art field (demo)"
territory = "KR"
languages = ["ko", "en"]

[paths]
data = "data"
frames = "{frames}"

[collect]
user_agent = "GiyeTest/0.1 (+https://example.org/contact)"
min_delay_s = 0.0

[collect.offline_roots]
"https://example.org" = "fixtures"
""",
        encoding="utf-8",
    )
    config = load(config_path)
    rows = ExampleResidency(config).run(collected_at="2026-10-04")
    assert [(row["frame_code"], row["year"], row["name"]) for row in rows] == [
        ("EXAMPLE-RESIDENCY-2019", "2019", "김하늘"),
        ("EXAMPLE-RESIDENCY-2020", "2020", "한별"),
    ]
    ledger = Ledger.open(config)
    membership = ledger.read("frame_membership")
    assert {row["frame_code"] for row in membership} == {"EXAMPLE-RESIDENCY-2019", "EXAMPLE-RESIDENCY-2020"}
    activities = ledger.read("activities")
    assert {row["origin"] for row in activities} == {"EXAMPLE-RESIDENCY-2019", "EXAMPLE-RESIDENCY-2020"}
    assert {row["year"] for row in activities} == {"2019", "2020"}
    rim = build_rim_order(config)
    by_year = {row["year"]: row["family"] for row in rim["artists"]}
    assert by_year[2019] == "GEN-2015"
    assert "GEN-UNDATED" not in by_year.values()
