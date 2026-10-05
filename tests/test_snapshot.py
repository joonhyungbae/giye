# SPDX-License-Identifier: AGPL-3.0-only
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


def _legacy_line(tmp_path: Path, frame: str, url: str, body: bytes, **keys: object) -> None:
    """A manifest line in the shape the pre-package collectors wrote (path relative to the frame)."""
    sha = hashlib.sha256(body).hexdigest()
    folder = tmp_path / frame / "snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"page__{sha[:10]}.html").write_bytes(body)
    line = {"url": url, "fetched_at": "2026-09-21T00:00:00Z", "sha256": sha, "bytes": len(body),
            "path": f"snapshots/page__{sha[:10]}.html", "new": True, "collector": "old.py",
            "run_started": "2026-09-21T00:00:00Z", **keys}
    with (folder / "manifest.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")


def test_recall_reads_older_status_keys(tmp_path: Path):
    _legacy_line(tmp_path, "OLD", "https://example.org/a", b"<p>a</p>", http_status=200, final_url="https://example.org/a")
    _legacy_line(tmp_path, "OLD", "https://example.org/b", b"<p>b</p>")
    _legacy_line(tmp_path, "OLD", "https://example.org/c", b"<p>c</p>", http_status=203, status=201)
    store = SnapshotStore(tmp_path)
    assert store.recall("https://example.org/a").status == 200
    # Oldest lines carry no status at all; their kept body replays as 200.
    assert store.recall("https://example.org/b").status == 200
    assert store.recall("https://example.org/c").status == 201
    # A versioned line that left status out is not guessed.
    store.keep("NEW", "https://example.org/d", b"<p>d</p>")
    assert store.recall("https://example.org/d").status == 0


def test_recall_tells_two_posts_to_one_url_apart(tmp_path: Path):
    store = SnapshotStore(tmp_path)
    url = "https://example.org/search"
    one, two = hashlib.sha256(b"page=1").hexdigest(), hashlib.sha256(b"page=2").hexdigest()
    store.keep("F", url, b"<p>get</p>", status=200)
    store.keep("F", url, b"<p>first</p>", status=200, method="POST", body_sha256=one)
    store.keep("F", url, b"<p>second</p>", status=200, method="POST", body_sha256=two)
    assert store.recall(url).content == b"<p>get</p>"
    assert store.recall(url, method="POST", body_sha256=one).content == b"<p>first</p>"
    assert store.recall(url, method="POST", body_sha256=two).content == b"<p>second</p>"
    assert store.recall(url, method="POST", body_sha256="0" * 64) is None
    rows = [json.loads(line) for line in (tmp_path / "F" / "snapshots" / "manifest.jsonl").read_text().splitlines()]
    assert "method" not in rows[0] and "body_sha256" not in rows[0]
    assert rows[1]["method"] == "POST" and rows[1]["body_sha256"] == one


def test_recall_reads_a_legacy_post_line(tmp_path: Path):
    url = "https://example.org/search"
    folder = tmp_path / "F" / "snapshots"
    folder.mkdir(parents=True)
    (folder / "legacy.html").write_bytes(b"<p>legacy</p>")
    line = {"url": f"POST {url}", "fetched_at": "2026-09-21T00:00:00Z", "path": "snapshots/legacy.html"}
    (folder / "manifest.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    store = SnapshotStore(tmp_path)
    kept = store.recall(url, method="POST", body_sha256=hashlib.sha256(b"page=1").hexdigest())
    assert kept is not None and kept.content == b"<p>legacy</p>" and kept.url == url
    assert store.recall(url) is None
