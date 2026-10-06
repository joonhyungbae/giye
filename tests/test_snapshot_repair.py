# SPDX-License-Identifier: AGPL-3.0-only
"""A truncated object is rewritten, and replay never serves an Internet Archive copy."""

from __future__ import annotations

import hashlib
from pathlib import Path

from giye.collect.fetch import Fetcher
from giye.collect.snapshot import SnapshotStore

UA = "GiyeTest/0.1 (+https://example.org/contact)"


def test_partial_object_without_a_manifest_line_is_rewritten(tmp_path: Path):
    body = b"<html>roster page</html>" * 100
    sha = hashlib.sha256(body).hexdigest()
    obj = tmp_path / "FRAME" / "snapshots" / "sha256" / sha[:2] / f"{sha}.html"
    obj.parent.mkdir(parents=True)
    obj.write_bytes(body[:100])  # a write cut short before its manifest line
    path = SnapshotStore(tmp_path).keep("FRAME", "https://example.org/r", body, status=200, content_type="text/html")
    assert path.read_bytes() == body
    assert SnapshotStore(tmp_path).recall("https://example.org/r").content == body


def test_replay_does_not_serve_an_internet_archive_capture(tmp_path: Path):
    store = SnapshotStore(tmp_path)
    store.keep(
        "_evidence",
        "https://example.org/roster",
        b"<li>Alex Example</li>",
        final_url="https://web.archive.org/web/20150101000000id_/https://example.org/roster",
        status=200,
        content_type="text/html",
        via="archive.org",
        archived_at="20150101000000",
    )
    fetcher = Fetcher(UA, from_snapshots=True, snapshot_root=tmp_path, prefer_frame="MYFRAME")
    page = fetcher.get("https://example.org/roster")
    assert page.status == 404 and page.reason == "not kept"
