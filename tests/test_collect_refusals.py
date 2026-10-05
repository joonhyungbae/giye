# SPDX-License-Identifier: AGPL-3.0-only
"""A refused fetch inside ``giye collect`` is recorded, not a traceback that ends the run."""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from giye.cli import main
from giye.config import load
from giye.ledger.ledger import Ledger

COLLECTORS = '''
from giye.collect.base import Edition, Person, RosterCollector


class Residency(RosterCollector):
    frame = "EXAMPLE-RESIDENCY"

    def editions(self):
        page = self.fetch("https://example.org/residency/alumni")
        yield Edition(year="2019", people=[Person(name="김하늘")], source_url=page.url)
        # robots.txt disallows /private/: the 2019 edition above is still written.
        self.fetch("https://example.org/private/2020")
        yield Edition(year="2020", people=[Person(name="한별")], source_url=page.url)


class Workshop(RosterCollector):
    frame = "EXAMPLE-WORKSHOP"

    def editions(self):
        self.fetch("https://www.instagram.com/example")
        yield from ()


class Forum(RosterCollector):
    frame = "EXAMPLE-FORUM"

    def editions(self):
        page = self.fetch("https://example.org/forum/guests")
        yield Edition(year="2023", people=[Person(name="이하루")], source_url=page.url)
'''


def _frame(code: str) -> str:
    return f"""  - code: {code}
    name_en: {code.title()}
    source_url: https://example.org/{code.lower()}
    roster_count: 1
    included_count: 1
    eligibility:
      decision: included
      f1_purpose: States the field.
      f2_cohort: A jury selects a cohort.
      f3_territory: Held in the configured territory.
      f4_roster: Public page.
      f5_period: One edition.
"""


def _field(tmp_path: Path, collectors: str) -> Path:
    fixtures = tmp_path / "fixtures"
    (fixtures / "residency").mkdir(parents=True)
    (fixtures / "forum").mkdir()
    (fixtures / "robots.txt").write_text("User-agent: *\nDisallow: /private/\n", encoding="utf-8")
    (fixtures / "residency" / "alumni.html").write_text("<li>김하늘</li>", encoding="utf-8")
    (fixtures / "forum" / "guests.html").write_text("<li>이하루</li>", encoding="utf-8")
    codes = ("EXAMPLE-RESIDENCY", "EXAMPLE-WORKSHOP", "EXAMPLE-FORUM")
    (tmp_path / "frames.yml").write_text("version: 1\nframes:\n" + "".join(_frame(code) for code in codes), encoding="utf-8")
    (tmp_path / "collectors.py").write_text(collectors, encoding="utf-8")
    config = tmp_path / "giye.toml"
    config.write_text(
        """
[archive]
name = "Synthetic media-art field (demo)"
territory = "KR"

[paths]
data = "data"
frames = "frames.yml"

[collect]
user_agent = "GiyeTest/0.1 (+https://example.org/contact)"
min_delay_s = 0.0
collector_modules = ["collectors.py"]

[collect.offline_roots]
"https://example.org" = "fixtures"
""",
        encoding="utf-8",
    )
    return config


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch):
    def boom(self, url, *args, **kwargs):
        raise AssertionError(f"network used: {url}")

    monkeypatch.setattr(requests.Session, "get", boom)


def test_refusals_are_recorded_and_the_other_collectors_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    config = _field(tmp_path, COLLECTORS)
    assert main(["collect", "--config", str(config)]) == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "refused EXAMPLE-RESIDENCY" in captured.err and "disallowed" in captured.err
    assert "refused EXAMPLE-WORKSHOP" in captured.err and "platform_excluded" in captured.err
    assert "collect: 3 collectors, 2 rows, 2 refused fetches, 1 collectors with no rows after a refusal" in captured.err
    lines = dict(line.split("\t")[:2] for line in captured.out.splitlines())
    assert lines == {"EXAMPLE-RESIDENCY": "1", "EXAMPLE-WORKSHOP": "0", "EXAMPLE-FORUM": "1"}
    names = {row["name_ko"] for row in Ledger.open(load(config)).read("artists")}
    assert names == {"김하늘", "이하루"}


def test_collect_fails_only_when_every_collector_failed(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    only_refused = COLLECTORS.split("class Forum")[0].replace(
        'yield Edition(year="2019", people=[Person(name="김하늘")], source_url=page.url)\n', ""
    )
    config = _field(tmp_path, only_refused)
    frames = (tmp_path / "frames.yml").read_text(encoding="utf-8")
    (tmp_path / "frames.yml").write_text(frames.split("  - code: EXAMPLE-FORUM")[0], encoding="utf-8")
    assert main(["collect", "--config", str(config)]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and "2 collectors with no rows after a refusal" in err
