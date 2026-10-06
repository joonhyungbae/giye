# SPDX-License-Identifier: AGPL-3.0-only
"""Network errors, error pages and unsourced rows are failures: reported, the run goes on, exit 1."""

from __future__ import annotations

from pathlib import Path

import pytest

from giye.cli import main
from giye.config import load
from giye.ledger.ledger import Ledger
from tests.test_collect_refusals import _field, _no_network  # noqa: F401  (autouse fixture)

COLLECTORS = '''
import requests

from giye.collect.base import Edition, Person, RosterCollector


class Residency(RosterCollector):
    frame = "EXAMPLE-RESIDENCY"

    def editions(self):
        page = self.fetch("https://example.org/residency/alumni")
        yield Edition(year="2019", people=[Person(name="김하늘")], source_url=page.url)
        raise requests.ReadTimeout("read timed out")


class Workshop(RosterCollector):
    frame = "EXAMPLE-WORKSHOP"

    def editions(self):
        page = self.fetch("https://example.org/workshop/missing")
        yield Edition(year="2020", people=[Person(name="한별")], source_url="")
        yield Edition(year="2021", people=[Person(name="박서윤")], source_url=page.url, collected_at="sometime")


class Forum(RosterCollector):
    frame = "EXAMPLE-FORUM"

    def editions(self):
        page = self.fetch("https://example.org/forum/guests")
        yield Edition(year="2023", people=[Person(name="이하루")], source_url=page.url)
'''


def test_failures_are_reported_and_the_other_collectors_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    config = _field(tmp_path, COLLECTORS)
    report = tmp_path / "data" / "work" / "rosters" / "EXAMPLE-WORKSHOP.csv"
    report.parent.mkdir(parents=True)
    report.write_text("previous report\n", encoding="utf-8")
    assert main(["collect", "--config", str(config)]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "failed EXAMPLE-RESIDENCY: network error ReadTimeout" in err
    assert "http 404" in err
    assert "without an http(s) source_url" in err
    assert "not YYYY-MM-DD" in err
    names = {row["name_ko"] for row in Ledger.open(load(config)).read("artists")}
    # The 2019 edition read before the timeout and the later collector are both written.
    assert names == {"김하늘", "이하루"}
    assert report.read_text(encoding="utf-8") == "previous report\n"


def test_missing_collector_module_is_one_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    config = _field(tmp_path, COLLECTORS)
    (tmp_path / "collectors.py").unlink()
    assert main(["collect", "--config", str(config)]) == 2
    assert capsys.readouterr().err.startswith("giye: error: collector module not found")
