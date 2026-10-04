# SPDX-License-Identifier: AGPL-3.0-only
"""Demo collectors: the RosterCollector API, offline, with source_url and collected_at on every row."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import requests

from giye.cli import main
from giye.collect.base import load_collectors, run_configured
from giye.collect.fetch import Fetcher, RobotsDisallowed
from giye.config import load
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
