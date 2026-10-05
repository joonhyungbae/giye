# SPDX-License-Identifier: AGPL-3.0-only
"""Demo collectors: the RosterCollector API, offline, with source_url and collected_at on every row."""

from __future__ import annotations

import csv
import json
import shutil
import socket
from pathlib import Path

import pytest
import requests

from giye.cli import main
from giye.collect.base import load_collectors, run_configured
from giye.collect.fetch import Fetcher, RobotsDisallowed
from giye.config import load
from giye.ledger.ledger import Ledger
from tests.conftest import serve

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo"
UA = "GiyeTest/0.1 (+https://example.org/contact)"


def _config(tmp_path: Path, *, modules: bool = True) -> Path:
    modules_line = 'collector_modules = ["collectors.py"]\n' if modules else ""
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Synthetic media-art field (demo)"
territory = "KR"
languages = ["ko", "en"]

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{(DEMO / "frames.yml").as_posix()}"

[collect]
user_agent = "{UA}"
min_delay_s = 0.0
{modules_line}
[collect.offline_roots]
"https://example.org" = "{(DEMO / "fixtures").as_posix()}"
""",
        encoding="utf-8",
    )
    # collector_modules paths are relative to the config file. Point at the demo module by absolute path
    # when the config does not live next to collectors.py.
    if modules:
        text = path.read_text(encoding="utf-8").replace(
            'collector_modules = ["collectors.py"]',
            f'collector_modules = ["{(DEMO / "collectors.py").as_posix()}"]',
        )
        path.write_text(text, encoding="utf-8")
    return path


def test_demo_collectors_match_the_paper_api_and_keep_spelling_variants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom(self, url, *args, **kwargs):
        raise AssertionError(f"network used: {url}")

    monkeypatch.setattr(requests.Session, "get", boom)
    config = load(_config(tmp_path))
    classes = load_collectors(config)
    assert [cls.__name__ for cls in classes] == ["ExampleResidency", "ExampleWorkshop", "ExampleForum"]
    results = run_configured(config, collected_at="2026-10-04", run_id="2026-10-04T00:00:00Z")
    by_frame = {frame: rows for frame, rows, _path in results}
    residency = by_frame["EXAMPLE-RESIDENCY"]
    assert [row["name"] for row in residency] == [
        "김하늘",
        "박서연 (Seoyeon Park)",
        "정다운",
        "표은솔",
        "한별",
        "문지호",
        "최민수",
        "배수아",
        "노을 스튜디오",
        "서지우",
        "Kim Seoyeon",
    ]
    assert {row["name_ko"] for row in residency} >= {"김하늘"}
    assert {row["name_en"] for row in residency} >= {"Kim Seoyeon"}
    assert {row["source_url"] for row in residency} == {"https://example.org/residency/alumni"}
    assert {row["collected_at"] for row in residency} == {"2026-10-04"}
    workshop = by_frame["EXAMPLE-WORKSHOP"]
    assert [row["name"] for row in workshop] == [
        "한별",
        "Lee Haru",
        "이하루",
        "표은솔",
        "정다운",
        "문지호",
        "최민수",
        "배수아",
        "Haneul Kim",
        "Haru Lee",
        "Jiwoo Seo",
    ]
    assert {row["name_en"] for row in workshop} >= {"Haneul Kim"}
    csv_path = tmp_path / "data" / "work" / "rosters" / "EXAMPLE-RESIDENCY.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        assert next(iter(csv.DictReader(handle)))["source_url"].startswith("https://example.org/")
    manifest = next((tmp_path / "data" / "raw").rglob("EXAMPLE-RESIDENCY/snapshots/manifest.jsonl"))
    line = json.loads(manifest.read_text(encoding="utf-8").splitlines()[0])
    assert line["collector"] == "ExampleResidency"
    assert line["run_id"] == "2026-10-04T00:00:00Z"
    assert line["tls_unverified"] is False
    assert line["status"] == 200
    stored = (tmp_path / "data" / "raw" / line["path"]).read_text(encoding="utf-8")
    assert "김하늘" in stored


def test_cli_collect_runs_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def boom(self, url, *args, **kwargs):
        raise AssertionError(f"network used: {url}")

    monkeypatch.setattr(requests.Session, "get", boom)
    assert main(["collect", "--config", str(_config(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "EXAMPLE-RESIDENCY\t11\t" in out
    assert "EXAMPLE-WORKSHOP\t11\t" in out
    assert "EXAMPLE-FORUM\t1\t" in out


def test_cli_without_collectors_exits_loudly(tmp_path: Path):
    assert main(["collect", "--config", str(_config(tmp_path, modules=False))]) == 2


def test_demo_fixtures_are_served_locally_and_still_obey_robots():
    with serve(DEMO / "fixtures") as (base, server):
        fetcher = Fetcher(UA, min_delay_s=0, timeout_s=5, robots_timeout_s=5)
        with pytest.raises(RobotsDisallowed):
            fetcher.get(base + "/private/secret.html")
        page = fetcher.get(base + "/residency/alumni.html")
    assert "김하늘" in page.text
    paths = [hit[0] for hit in server.hits]
    assert "/private/secret.html" not in paths
    assert paths.count("/robots.txt") == 1


def _frame_entry(code: str, decision: str) -> str:
    return f"""
  - code: {code}
    name_en: {code} programme
    source_url: https://example.org/{code.lower()}
    roster_count: 1
    eligibility:
      decision: {decision}
      f1_purpose: States the field.
      f2_cohort: A jury selects a cohort.
      f3_territory: Held in the configured territory.
      f4_roster: A public page lists the participants.
      f5_period: Two editions.
"""


def _admission_config(tmp_path: Path, decisions: dict[str, str]) -> Path:
    frames = tmp_path / "frames.yml"
    body = "version: 1\nframes:" + "".join(_frame_entry(code, decision) for code, decision in decisions.items())
    frames.write_text(body, encoding="utf-8")
    classes = []
    for code in decisions:
        classes.append(
            f"""
class Collect{code}(RosterCollector):
    frame = {code!r}

    def editions(self):
        raise AssertionError({code!r} + " was collected")
"""
        )
    module = tmp_path / "collectors.py"
    module.write_text(
        "from giye.collect.base import Edition, Person, RosterCollector\n" + "\n".join(classes),
        encoding="utf-8",
    )
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Synthetic admission field"

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{frames.as_posix()}"

[collect]
user_agent = "{UA}"
min_delay_s = 0.0
collector_modules = ["{module.as_posix()}"]
""",
        encoding="utf-8",
    )
    return path


def _yielding_collector(tmp_path: Path, code: str, decision: str, name: str) -> Path:
    """One collector that yields a row without fetching, under ``decision``."""
    frames = tmp_path / "frames.yml"
    frames.write_text("version: 1\nframes:" + _frame_entry(code, decision), encoding="utf-8")
    module = tmp_path / "collectors.py"
    module.write_text(
        f"""
from giye.collect.base import Edition, Person, RosterCollector

class Collect{code}(RosterCollector):
    frame = {code!r}

    def editions(self):
        yield Edition(year=2019, people=[Person(name={name!r})], source_url="https://example.org/{code.lower()}")
""",
        encoding="utf-8",
    )
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Synthetic admission field"

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{frames.as_posix()}"

[collect]
user_agent = "{UA}"
min_delay_s = 0.0
collector_modules = ["{module.as_posix()}"]
""",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("decision", ["excluded", "planned", "no_public_roster"])
def test_non_admitted_frame_is_skipped_with_one_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], decision: str
):
    def boom(self, url, *args, **kwargs):
        raise AssertionError(f"network used: {url}")

    monkeypatch.setattr(requests.Session, "get", boom)
    config = load(_admission_config(tmp_path, {"DROPPED": decision}))
    assert run_configured(config, collected_at="2026-10-04", run_id="2026-10-04T00:00:00Z") == []
    notice = capsys.readouterr().err.splitlines()
    assert notice == [f"skip DROPPED: eligibility.decision is {decision}; not collected"]
    assert not (tmp_path / "data" / "ledger").exists()
    assert not (tmp_path / "data" / "work" / "rosters" / "DROPPED.csv").exists()
    assert list((tmp_path / "data").rglob("manifest.jsonl")) == []


def test_included_and_adjacent_are_collected_and_the_other_frame_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    def boom(self, url, *args, **kwargs):
        raise AssertionError(f"network used: {url}")

    monkeypatch.setattr(requests.Session, "get", boom)
    frames = tmp_path / "frames.yml"
    frames.write_text(
        "version: 1\nframes:"
        + _frame_entry("KEPT", "included")
        + _frame_entry("NEAR", "adjacent")
        + _frame_entry("DROPPED", "excluded"),
        encoding="utf-8",
    )
    module = tmp_path / "collectors.py"
    module.write_text(
        """
from giye.collect.base import Edition, Person, RosterCollector

class CollectKept(RosterCollector):
    frame = "KEPT"

    def editions(self):
        yield Edition(year=2019, people=[Person(name="김하늘")], source_url="https://example.org/kept")

class CollectNear(RosterCollector):
    frame = "NEAR"

    def editions(self):
        yield Edition(year=2021, people=[Person(name="한별")], source_url="https://example.org/near")

class CollectDropped(RosterCollector):
    frame = "DROPPED"

    def editions(self):
        raise AssertionError("excluded frame was collected")
""",
        encoding="utf-8",
    )
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Synthetic admission field"

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{frames.as_posix()}"

[collect]
user_agent = "{UA}"
min_delay_s = 0.0
collector_modules = ["{module.as_posix()}"]
""",
        encoding="utf-8",
    )
    results = run_configured(load(path), collected_at="2026-10-04", run_id="2026-10-04T00:00:00Z")
    assert [frame for frame, _rows, _path in results] == ["KEPT", "NEAR"]
    by_frame = {frame: rows for frame, rows, _path in results}
    assert [row["name"] for row in by_frame["KEPT"]] == ["김하늘"]
    assert [row["name"] for row in by_frame["NEAR"]] == ["한별"]
    assert capsys.readouterr().err.splitlines() == ["skip DROPPED: eligibility.decision is excluded; not collected"]
    membership = Ledger.open(load(path)).read("frame_membership")
    assert {row["frame_code"] for row in membership} == {"KEPT-2019", "NEAR-2021"}
    assert not (tmp_path / "data" / "work" / "rosters" / "DROPPED.csv").exists()


def test_direct_run_skips_a_frame_that_is_not_admitted(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """``run`` is the check a caller that does not use ``run_configured`` still hits."""
    config = load(_yielding_collector(tmp_path, "DROPPED", "excluded", "김하늘"))
    classes = load_collectors(config)
    assert classes[0](config).run(collected_at="2026-10-04") == []
    assert capsys.readouterr().err.splitlines() == ["skip DROPPED: eligibility.decision is excluded; not collected"]
    assert not (tmp_path / "data" / "ledger").exists()


def _block_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def opened(*_args, **_kwargs):
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "socket", opened)
    monkeypatch.setattr(socket, "create_connection", opened)
    monkeypatch.setattr(socket, "getaddrinfo", opened)


def _ledger_rows(data: Path) -> dict[str, list[dict[str, str]]]:
    """Ledger tables without ``updated_at``.

    That column is the time of the roster write, not the collection date, so a
    re-run stamps it again. Every other column is a collection fact.
    """
    found: dict[str, list[dict[str, str]]] = {}
    for path in sorted((data / "ledger").glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]
        for row in rows:
            row.pop("updated_at", None)
        found[path.name] = rows
    return found


def _manifest_text(data: Path) -> dict[str, str]:
    root = data / "raw"
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.glob("*/snapshots/manifest.jsonl"))
    }


def _roster_text(data: Path) -> dict[str, str]:
    folder = data / "work" / "rosters"
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(folder.glob("*.csv"))}


def test_from_snapshots_flag_is_only_on_collect_and_run():
    from giye.cli import _parser

    parser = _parser()
    assert parser.parse_args(["collect", "--from-snapshots"]).from_snapshots is True
    assert parser.parse_args(["run", "--from-snapshots"]).from_snapshots is True
    assert parser.parse_args(["collect"]).from_snapshots is False
    with pytest.raises(SystemExit):
        parser.parse_args(["extract", "--from-snapshots"])


def test_from_snapshots_rebuilds_the_same_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Online collect against offline fixtures, then the same pages from the store.

    The copy is a second archive. Snapshot lines are not appended. Ledger rows
    match, including ``collected_at`` taken from each manifest ``fetched_at``.
    """
    online = tmp_path / "online"
    online.mkdir()
    assert main(["collect", "--config", str(_config(online))]) == 0
    assert _manifest_text(online / "data")
    replay = tmp_path / "replay"
    shutil.copytree(online / "data", replay / "data")
    manifests = _manifest_text(replay / "data")
    dates = {
        json.loads(line)["fetched_at"][:10]
        for text in manifests.values()
        for line in text.splitlines()
        if line.strip()
    }
    _block_sockets(monkeypatch)
    assert main(["collect", "--from-snapshots", "--config", str(_config(replay))]) == 0
    assert _manifest_text(replay / "data") == manifests
    assert _roster_text(replay / "data") == _roster_text(online / "data")
    assert _ledger_rows(replay / "data") == _ledger_rows(online / "data")
    artists = Ledger.open(load(_config(replay))).read("artists")
    assert {row["collected_at"] for row in artists} == dates
    assert "김하늘" in {row["name_ko"] for row in artists}


def test_from_snapshots_dates_rows_from_the_manifest_not_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    online = tmp_path / "online"
    online.mkdir()
    assert main(["collect", "--config", str(_config(online))]) == 0
    replay = tmp_path / "replay"
    shutil.copytree(online / "data" / "raw", replay / "data" / "raw")
    for manifest in (replay / "data" / "raw").glob("*/snapshots/manifest.jsonl"):
        rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
        for row in rows:
            row["fetched_at"] = "2019-03-15T04:05:06Z"
        manifest.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    before = _manifest_text(replay / "data")
    _block_sockets(monkeypatch)
    assert main(["collect", "--from-snapshots", "--config", str(_config(replay))]) == 0
    assert _manifest_text(replay / "data") == before
    ledger = Ledger.open(load(_config(replay)))
    assert {row["collected_at"] for row in ledger.read("artists")} == {"2019-03-15"}
    assert {row["collected_at"] for row in ledger.read("activities")} == {"2019-03-15"}
    assert {row["collected_at"] for row in ledger.read("frame_membership")} == {"2019-03-15"}
    assert "김하늘" in {row["name_ko"] for row in ledger.read("artists")}
