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


def test_full_sha256_reuses_the_manifest_path_and_not_a_prefix_decoy(tmp_path: Path):
    """Production lookup is the full hash on the manifest line, not the short name."""
    content = b"hello-full-hash"
    sha = hashlib.sha256(content).hexdigest()
    folder = tmp_path / "frame" / "snapshots"
    folder.mkdir(parents=True)
    real = folder / f"real__{sha[:10]}.html"
    real.write_bytes(content)
    decoy = folder / f"decoy__{sha[:10]}.html"
    decoy.write_bytes(b"not-the-same-bytes")
    old = {
        "url": "https://example.org/old",
        "fetched_at": "2026-09-21T00:00:00Z",
        "sha256": sha,
        "path": f"snapshots/{real.name}",
        "bytes": len(content),
        "new": True,
    }
    manifest = folder / "manifest.jsonl"
    manifest.write_text(json.dumps(old) + "\n", encoding="utf-8")

    store = SnapshotStore(tmp_path)
    path = store.keep(
        "frame",
        "https://example.org/new",
        content,
        status=200,
        final_url="https://example.org/new",
        content_type="text/html; charset=utf-8",
        robots="not_checked",
    )
    assert path == real.resolve()
    assert decoy.read_bytes() == b"not-the-same-bytes"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == old
    fresh = json.loads(lines[1])
    assert fresh["new"] is False
    assert fresh["sha256"] == sha
    assert fresh["robots"] == "not_checked"
    assert fresh["status"] == 200
    assert fresh["final_url"] == "https://example.org/new"
    assert fresh["content_type"] == "text/html; charset=utf-8"
    assert fresh["manifest_version"] == 1


def test_prefix_named_file_without_a_manifest_line_is_not_the_match(tmp_path: Path):
    content = b"brand-new-bytes"
    sha = hashlib.sha256(content).hexdigest()
    folder = tmp_path / "frame" / "snapshots"
    folder.mkdir(parents=True)
    decoy = folder / f"decoy__{sha[:10]}.html"
    decoy.write_bytes(b"different")
    store = SnapshotStore(tmp_path)
    path = store.keep("frame", "https://example.org/z", content, status=201, robots="allowed")
    assert path is not None
    assert path.read_bytes() == content
    assert path.resolve() != decoy.resolve()
    assert decoy.read_bytes() == b"different"
    row = json.loads((folder / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["status"] == 201
    assert row["final_url"] == "https://example.org/z"
    assert row["content_type"] == ""
    assert row["robots"] == "allowed"
    assert row["new"] is True
    assert sha in path.name


def test_empty_or_oversized_body_is_not_stored(tmp_path: Path):
    store = SnapshotStore(tmp_path)
    assert store.keep("EXAMPLE-RESIDENCY", "https://example.org/empty", b"") is None
    assert not (tmp_path / "EXAMPLE-RESIDENCY" / "snapshots" / "manifest.jsonl").exists()
