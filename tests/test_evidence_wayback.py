# SPDX-License-Identifier: AGPL-3.0-only
"""Wayback captures: served timestamp, error-page captures, and the evidence command's exit status."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import requests

from giye.cli import main
from giye.collect import robots
from giye.collect.evidence import WAYBACK_AVAILABLE, settle_url
from giye.collect.fetch import Page
from giye.collect.snapshot import SnapshotStore

ORIG = "https://example.org/cv"


class FakeFetcher:
    def __init__(self, closest_status: str, served: str):
        self.closest_status, self.served = closest_status, served

    def get(self, url):
        if url.startswith(ORIG):
            raise requests.ConnectionError("gone")
        if url.startswith(WAYBACK_AVAILABLE):
            closest = {"available": True, "status": self.closest_status, "timestamp": "20150101000000",
                       "url": f"http://web.archive.org/web/20150101000000/{ORIG}"}
            body = json.dumps({"archived_snapshots": {"closest": closest}}).encode()
            return Page(url=url, status=200, content=body, content_type="application/json")
        return Page(url=self.served, status=200, content=b"<p>archived</p>", content_type="text/html")


def test_archived_at_is_the_capture_actually_served(tmp_path: Path):
    served = f"https://web.archive.org/web/20190707070707id_/{ORIG}"
    result = settle_url(ORIG, fetcher=FakeFetcher("200", served), store=SnapshotStore(tmp_path))
    assert result["status"] == "archive_org"
    assert result["archived_at"] == "20190707070707"


def test_a_capture_of_an_error_page_is_not_kept(tmp_path: Path):
    served = f"https://web.archive.org/web/20150101000000id_/{ORIG}"
    result = settle_url(ORIG, fetcher=FakeFetcher("404", served), store=SnapshotStore(tmp_path))
    assert result["status"] == "unavailable"
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_evidence_exits_non_zero_when_nothing_was_kept(tmp_path: Path, monkeypatch, capsys):
    data = tmp_path / "data" / "ledger"
    data.mkdir(parents=True)
    (data / "artists.csv").write_text("ledger_id,source_url\nLED-1,https://example.org/gone\n", encoding="utf-8")
    (tmp_path / "frames.yml").write_text("frames: []\n", encoding="utf-8")
    config = tmp_path / "giye.toml"
    config.write_text(
        '[archive]\nname = "Synthetic"\n[collect]\nuser_agent = "GiyeTest/0.1 (+https://example.org/contact)"\n'
        "min_delay_s = 0\n",
        encoding="utf-8",
    )

    def unreachable(self, url, *args, **kwargs):
        if urlparse(url).netloc != "example.org":
            raise AssertionError(url)
        raise requests.ConnectTimeout("timed out")

    monkeypatch.setattr(requests.Session, "get", unreachable)
    robots.clear_cache()
    assert main(["evidence", "--config", str(config), "--retry-unavailable"]) == 1
    assert "robots_unreachable=1" in capsys.readouterr().out
