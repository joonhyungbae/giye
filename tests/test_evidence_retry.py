# SPDX-License-Identifier: AGPL-3.0-only
"""giye evidence: dead hosts, transient robots failures, and repeated runs."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import requests

from giye.collect import robots
from giye.collect.evidence import archive_cited, settle_url
from giye.collect.snapshot import SnapshotStore
from giye.config import Config
from tests.test_evidence import PAGE, TS, UA, FakeResponse, _fetcher, _wayback


def _config(tmp_path: Path) -> Config:
    (tmp_path / "frames.yml").write_text("frames: []\n", encoding="utf-8")
    return Config(root=tmp_path, name="Synthetic", data=tmp_path / "data", user_agent=UA, frames=tmp_path / "frames.yml")


def test_dead_host_gets_the_existing_capture(tmp_path: Path):
    robots.clear_cache()

    def handler(url, **kwargs):
        if urlparse(url).netloc == "example.org":
            raise requests.ConnectionError("connection refused")
        return _wayback(url)

    fetcher, session = _fetcher(handler)
    result = settle_url(PAGE, fetcher=fetcher, store=SnapshotStore(tmp_path))
    assert result["status"] == "archive_org"
    assert result["direct_failure"] == "host unreachable"
    assert result["archived_at"] == TS
    assert any("wayback/available" in call for call in session.calls)


def test_transient_robots_failure_is_retried_on_the_next_run(tmp_path: Path):
    config = _config(tmp_path)
    state = {"robots": 503}

    def handler(url, **kwargs):
        parts = urlparse(url)
        if parts.path == "/robots.txt":
            return FakeResponse(state["robots"], "", url, "text/plain")
        return FakeResponse(200, "<p>page</p>", url)

    robots.clear_cache()
    fetcher, _session = _fetcher(handler)
    first = archive_cited(config, [PAGE], fetcher=fetcher, store=SnapshotStore(config.raw))
    assert first[PAGE]["status"] == "robots_unreachable"
    state["robots"] = 404
    robots.clear_cache()
    fetcher, _session = _fetcher(handler)
    second = archive_cited(config, [PAGE], fetcher=fetcher, store=SnapshotStore(config.raw))
    assert second[PAGE]["status"] == "fetched"


def test_legacy_unreachable_status_is_retried(tmp_path: Path):
    config = _config(tmp_path)
    status_path = config.work / "evidence" / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({PAGE: {"status": "robots_disallowed", "reason": "robots_unreachable"}}))

    def handler(url, **kwargs):
        if urlparse(url).path == "/robots.txt":
            return FakeResponse(404, "", url, "text/plain")
        return FakeResponse(200, "<p>page</p>", url)

    robots.clear_cache()
    fetcher, _session = _fetcher(handler)
    assert archive_cited(config, [PAGE], fetcher=fetcher, store=SnapshotStore(config.raw))[PAGE]["status"] == "fetched"


def test_second_run_keeps_its_own_capture_status(tmp_path: Path):
    config = _config(tmp_path)

    def handler(url, **kwargs):
        if urlparse(url).netloc == "example.org" and urlparse(url).path != "/robots.txt":
            return FakeResponse(404, "gone", url)
        return _wayback(url)

    robots.clear_cache()
    fetcher, _session = _fetcher(handler)
    first = archive_cited(config, [PAGE], fetcher=fetcher, store=SnapshotStore(config.raw))
    assert first[PAGE]["status"] == "archive_org"
    second = archive_cited(config, [PAGE], fetcher=fetcher, store=SnapshotStore(config.raw))
    assert second[PAGE]["status"] == "archive_org"
    assert second[PAGE]["archived_at"] == TS


def test_a_subset_run_keeps_the_other_statuses(tmp_path: Path):
    config = _config(tmp_path)
    other = "https://example.org/other"
    status_path = config.work / "evidence" / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({other: {"status": "fetched", "path": "x"}}))

    def handler(url, **kwargs):
        if urlparse(url).path == "/robots.txt":
            return FakeResponse(404, "", url, "text/plain")
        return FakeResponse(200, "<p>page</p>", url)

    robots.clear_cache()
    fetcher, _session = _fetcher(handler)
    status = archive_cited(config, [PAGE], fetcher=fetcher, store=SnapshotStore(config.raw))
    assert status[other]["status"] == "fetched"
    assert json.loads(status_path.read_text())[other]["status"] == "fetched"
