# SPDX-License-Identifier: MIT
"""Snapshot store: one object per SHA-256, one manifest line per fetch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from giye.collect.snapshot import SnapshotStore


def test_identical_content_is_stored_once_and_logged_twice(tmp_path: Path):
    store = SnapshotStore(tmp_path)
    body = "<p>김하늘</p>".encode()
    first = store.keep(
        "EXAMPLE-RESIDENCY",
        "https://example.org/residency/alumni",
        body,
        final_url="https://example.org/residency/alumni",
        status=200,
        content_type="text/html; charset=utf-8",
        collector="ExampleResidency",
        run_id="2026-10-04T00:00:00Z",
        tls_unverified=False,
    )
    second = store.keep(
        "EXAMPLE-RESIDENCY",
        "https://example.org/residency/alumni?again=1",
        body,
        status=200,
        content_type="text/html; charset=utf-8",
        collector="ExampleResidency",
        run_id="2026-10-04T01:00:00Z",
        tls_unverified=True,
    )
    assert first == second
    assert first is not None
    assert first.read_bytes() == body
    assert first.name.startswith(hashlib.sha256(body).hexdigest())
    lines = (tmp_path / "EXAMPLE-RESIDENCY" / "snapshots" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    required = {
        "url",
        "final_url",
        "status",
        "fetched_at",
        "sha256",
        "bytes",
        "content_type",
        "collector",
        "run_id",
        "tls_unverified",
    }
    assert required <= rows[0].keys()
    assert rows[0]["sha256"] == hashlib.sha256(body).hexdigest()
    assert rows[0]["new"] is True
    assert rows[1]["new"] is False
    assert rows[1]["tls_unverified"] is True
    assert rows[0]["fetched_at"].endswith("Z")
    assert rows[1]["path"] == rows[0]["path"]
    objects = [path for path in (tmp_path / "EXAMPLE-RESIDENCY").rglob("*") if path.is_file() and path.suffix == ".html"]
    assert objects == [first]


def test_empty_or_oversized_body_is_not_stored(tmp_path: Path):
    store = SnapshotStore(tmp_path)
    assert store.keep("EXAMPLE-RESIDENCY", "https://example.org/empty", b"") is None
    assert not (tmp_path / "EXAMPLE-RESIDENCY" / "snapshots" / "manifest.jsonl").exists()
