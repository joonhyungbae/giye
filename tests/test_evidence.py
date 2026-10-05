# SPDX-License-Identifier: AGPL-3.0-only
"""Evidence copies: existing Internet Archive captures only.

A robots.txt disallow records the capture URL and timestamp and does not keep the bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from giye.collect.evidence import archive_cited, cited_urls, settle_url
from giye.collect.fetch import Fetcher
from giye.collect.snapshot import SnapshotStore
from giye.config import Config

UA = "GiyeTest/0.1 (+https://example.org/contact)"
PAGE = "https://example.org/residency/alumni"
ARCHIVED = "<p>archived alumni 김하늘</p>".encode()
TS = "20200101120000"
CAPTURE = f"https://web.archive.org/web/{TS}/{PAGE}"


class FakeResponse:
    def __init__(self, status: int, body: bytes | str, url: str, content_type: str = "text/html; charset=utf-8"):
        self.status_code = status
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.text = self.content.decode("utf-8")
        self.url = url
        self.headers = {"Content-Type": content_type}
        self.ok = 200 <= status < 400


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self.handler(url, **kwargs)


def _fetcher(handler) -> tuple[Fetcher, FakeSession]:
    session = FakeSession(handler)
    fetcher = Fetcher(UA, min_delay_s=0, session=session)  # type: ignore[arg-type]
    return fetcher, session


def _allow_robots(url: str, *, disallow: bool = False) -> FakeResponse:
    body = "User-agent: *\nDisallow: /\n" if disallow else "User-agent: *\nAllow: /\n"
    return FakeResponse(200, body, url, "text/plain")


def _wayback(url: str):
    parts = urlparse(url)
    if parts.path == "/robots.txt":
        return _allow_robots(url)
    if parts.netloc == "archive.org" and parts.path == "/wayback/available":
        assert parse_qs(parts.query)["url"] == [PAGE]
        payload = {
            "archived_snapshots": {
                "closest": {"available": True, "timestamp": TS, "status": "200", "url": CAPTURE}
            }
        }
        return FakeResponse(200, json.dumps(payload), url, "application/json")
    if parts.netloc == "web.archive.org" and f"/web/{TS}id_/" in parts.path:
        return FakeResponse(200, ARCHIVED, url)
    raise AssertionError(url)


@pytest.mark.parametrize("status", [404, 410])
def test_gone_page_keeps_an_existing_capture_and_never_saves(tmp_path: Path, status: int):
    def handler(url, **kwargs):
        parts = urlparse(url)
        if parts.netloc == "example.org" and parts.path == "/robots.txt":
            return _allow_robots(url)
        if parts.netloc == "example.org" and parts.path == "/residency/alumni":
            return FakeResponse(status, b"gone", url)
        return _wayback(url)

    fetcher, session = _fetcher(handler)
    store = SnapshotStore(tmp_path)
    result = settle_url(PAGE, fetcher=fetcher, store=store)
    assert result["status"] == "archive_org"
    assert result["archived_at"] == TS
    saved = (tmp_path / result["path"]).read_bytes()
    assert saved == ARCHIVED
    assert b"gone" not in saved
    manifest = next((tmp_path).rglob("manifest.jsonl")).read_text(encoding="utf-8")
    line = json.loads(manifest.splitlines()[-1])
    assert line["via"] == "archive.org"
    assert line["archived_at"] == TS
    assert all("/save" not in call for call in session.calls)


def test_connection_failure_uses_the_same_existing_capture(tmp_path: Path):
    def handler(url, **kwargs):
        parts = urlparse(url)
        if parts.netloc == "example.org" and parts.path == "/robots.txt":
            return _allow_robots(url)
        if parts.netloc == "example.org":
            raise requests.exceptions.ConnectionError("refused")
        return _wayback(url)

    fetcher, session = _fetcher(handler)
    result = settle_url(PAGE, fetcher=fetcher, store=SnapshotStore(tmp_path))
    assert result["status"] == "archive_org"
    assert result["direct_failure"] == "ConnectionError"
    assert all("/save" not in call for call in session.calls)


def test_robots_disallow_records_the_archive_link_and_not_the_bytes(tmp_path: Path):
    def handler(url, **kwargs):
        parts = urlparse(url)
        if "id_" in url:
            raise AssertionError(f"raw capture was fetched: {url}")
        if parts.netloc == "example.org" and parts.path != "/robots.txt":
            raise AssertionError(f"disallowed host was fetched: {url}")
        if parts.netloc == "example.org":
            return _allow_robots(url, disallow=True)
        if parts.path == "/robots.txt":
            return _allow_robots(url)
        if parts.netloc == "archive.org" and parts.path == "/wayback/available":
            payload = {
                "archived_snapshots": {
                    "closest": {"available": True, "timestamp": TS, "status": "200", "url": CAPTURE}
                }
            }
            return FakeResponse(200, json.dumps(payload), url, "application/json")
        raise AssertionError(url)

    fetcher, session = _fetcher(handler)
    store = SnapshotStore(tmp_path)
    result = settle_url(PAGE, fetcher=fetcher, store=store)
    assert result["status"] == "archive_link_only"
    assert result["capture_url"] == CAPTURE
    assert result["archived_at"] == TS
    assert result["direct_failure"] == "robots"
    assert "id_" not in result["capture_url"]
    assert not any(path.is_file() for path in tmp_path.rglob("*"))
    assert all("id_" not in call and "/save" not in call for call in session.calls)
    assert any("archive.org/wayback/available" in call for call in session.calls)


def test_unreachable_robots_does_not_fall_back_to_the_archive(tmp_path: Path):
    from giye.collect.robots import VERDICT_UNREACHABLE

    def handler(url, **kwargs):
        if urlparse(url).path == "/robots.txt":
            return FakeResponse(503, "down", url, "text/plain")
        raise AssertionError(f"request sent despite unreachable robots: {url}")

    fetcher, session = _fetcher(handler)
    result = settle_url(PAGE, fetcher=fetcher, store=SnapshotStore(tmp_path))
    assert result["status"] == "robots_disallowed"
    assert result["reason"] == "robots_unreachable"
    assert result["robots"] == VERDICT_UNREACHABLE
    assert session.calls == ["https://example.org/robots.txt"]


def test_robots_disallow_without_a_capture_keeps_no_bytes(tmp_path: Path):
    def handler(url, **kwargs):
        parts = urlparse(url)
        if "id_" in url:
            raise AssertionError(url)
        if parts.netloc == "example.org" and parts.path != "/robots.txt":
            raise AssertionError(url)
        if parts.path == "/robots.txt":
            return _allow_robots(url, disallow=parts.netloc == "example.org")
        if parts.netloc == "archive.org" and parts.path == "/wayback/available":
            payload = {"archived_snapshots": {}}
            return FakeResponse(200, json.dumps(payload), url, "application/json")
        raise AssertionError(url)

    fetcher, session = _fetcher(handler)
    result = settle_url(PAGE, fetcher=fetcher, store=SnapshotStore(tmp_path))
    assert result["status"] == "unavailable"
    assert result["reason"] == "robots"
    assert all("id_" not in call for call in session.calls)


def test_live_page_is_kept_directly_without_the_archive(tmp_path: Path):
    def handler(url, **kwargs):
        parts = urlparse(url)
        if parts.path == "/robots.txt":
            return _allow_robots(url)
        if "archive.org" in parts.netloc:
            raise AssertionError(f"archive used for a live page: {url}")
        return FakeResponse(200, b"<p>live</p>", url)

    fetcher, _session = _fetcher(handler)
    result = settle_url(PAGE, fetcher=fetcher, store=SnapshotStore(tmp_path))
    assert result["status"] == "fetched"
    assert (tmp_path / result["path"]).read_bytes() == b"<p>live</p>"


def test_social_platforms_are_not_requested(tmp_path: Path):
    def handler(url, **kwargs):
        raise AssertionError(url)

    fetcher, session = _fetcher(handler)
    result = settle_url("https://www.instagram.com/example", fetcher=fetcher, store=SnapshotStore(tmp_path))
    assert result["status"] == "platform_excluded"
    assert session.calls == []


def test_cited_urls_and_resumable_status(tmp_path: Path):
    data = tmp_path / "data"
    ledger = data / "ledger"
    ledger.mkdir(parents=True)
    (ledger / "artists.csv").write_text(
        "ledger_id,source_url,reviewer_note\n"
        "L1,https://example.org/residency/alumni,see https://example.org/notes/1\n",
        encoding="utf-8",
    )
    frames = tmp_path / "frames.yml"
    frames.write_text(
        "frames:\n- code: EXAMPLE-RESIDENCY\n  source_url: https://example.org/workshop/fellows\n",
        encoding="utf-8",
    )
    raw = data / "raw" / "EXAMPLE-RESIDENCY" / "snapshots"
    raw.mkdir(parents=True)
    (raw / "manifest.jsonl").write_text(
        json.dumps({"url": "https://example.org/residency/alumni", "sha256": "abc"}) + "\n",
        encoding="utf-8",
    )
    config = Config(
        root=tmp_path,
        name="Synthetic",
        data=data,
        frames=frames,
        user_agent=UA,
    )
    found = cited_urls(config)
    assert "artists" in found["https://example.org/residency/alumni"]
    assert "artists.reviewer_note" in found["https://example.org/notes/1"]
    assert any(source.startswith("raw:") for source in found["https://example.org/workshop/fellows"])

    def handler(url, **kwargs):
        parts = urlparse(url)
        if "id_" in url:
            raise AssertionError(url)
        if parts.path == "/robots.txt":
            return _allow_robots(url, disallow="example.org" in parts.netloc)
        if parts.netloc == "archive.org" and parts.path == "/wayback/available":
            target = parse_qs(parts.query)["url"][0]
            payload = {
                "archived_snapshots": {
                    "closest": {
                        "available": True,
                        "timestamp": TS,
                        "status": "200",
                        "url": f"https://web.archive.org/web/{TS}/{target}",
                    }
                }
            }
            return FakeResponse(200, json.dumps(payload), url, "application/json")
        raise AssertionError(url)

    fetcher, session = _fetcher(handler)
    status = archive_cited(config, fetcher=fetcher, store=SnapshotStore(data / "raw"))
    assert status["https://example.org/residency/alumni"]["status"] == "collector_or_cv_snapshot"
    assert status["https://example.org/notes/1"]["status"] == "archive_link_only"
    assert status["https://example.org/notes/1"]["direct_failure"] == "robots"
    assert "id_" not in status["https://example.org/notes/1"]["capture_url"]
    calls_after_first = len(session.calls)
    again = archive_cited(config, fetcher=fetcher, store=SnapshotStore(data / "raw"))
    assert again["https://example.org/notes/1"]["status"] == "archive_link_only"
    assert len(session.calls) == calls_after_first


def test_withdrawn_manifest_line_is_not_a_body(tmp_path: Path):
    """A later withdrawn line hides the earlier bytes from export and from coverage."""
    from giye.export.warc import export_warc

    data = tmp_path / "data"
    raw = data / "raw" / "_evidence" / "snapshots"
    raw.mkdir(parents=True)
    body = b"<p>not served</p>"
    stored = raw / "page.html"
    stored.write_bytes(body)
    rel = "_evidence/snapshots/page.html"
    lines = [
        {
            "url": PAGE,
            "final_url": "https://example.org/redirected",
            "path": rel,
            "sha256": "abc",
            "bytes": len(body),
            "direct_failure": "robots",
            "via": "archive.org",
        },
        {
            "url": PAGE,
            "withdrawn": True,
            "direct_failure": "robots",
            "capture_url": CAPTURE,
            "archived_at": TS,
        },
    ]
    (raw / "manifest.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    config = Config(root=tmp_path, name="Synthetic", data=data, user_agent=UA, frames=tmp_path / "frames.yml")
    (tmp_path / "frames.yml").write_text("frames: []\n", encoding="utf-8")

    def handler(url, **kwargs):
        parts = urlparse(url)
        if "id_" in url or (parts.netloc == "example.org" and parts.path != "/robots.txt"):
            raise AssertionError(f"withdrew body was fetched again: {url}")
        if parts.path == "/robots.txt":
            return _allow_robots(url, disallow="example.org" in parts.netloc)
        if parts.netloc == "archive.org" and parts.path == "/wayback/available":
            payload = {
                "archived_snapshots": {
                    "closest": {"available": True, "timestamp": TS, "status": "200", "url": CAPTURE}
                }
            }
            return FakeResponse(200, json.dumps(payload), url, "application/json")
        raise AssertionError(url)

    fetcher, _session = _fetcher(handler)
    status = archive_cited(config, urls={PAGE: ["test"]}, fetcher=fetcher, store=SnapshotStore(data / "raw"))
    assert status[PAGE]["status"] == "archive_link_only"
    assert stored.is_file()
    exported = export_warc(config, tmp_path / "snapshots.warc.gz")
    from warcio.archiveiterator import ArchiveIterator

    responses = []
    with exported.warc.open("rb") as handle:
        for record in ArchiveIterator(handle):
            if record.rec_type == "response":
                responses.append(record.content_stream().read())
    assert responses == []


def test_offline_config_never_contacts_the_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Offline roots make the evidence pass offline: a gone page is not sent to the Internet Archive."""
    import socket

    from giye.collect.evidence import wayback_allowed

    def refuse(*_args, **_kwargs):
        raise AssertionError("evidence pass tried to open a socket")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    data = tmp_path / "data"
    ledger = data / "ledger"
    ledger.mkdir(parents=True)
    fixtures = tmp_path / "fixtures"
    (fixtures / "residency").mkdir(parents=True)
    (fixtures / "residency" / "alumni").write_text("<p>alumni</p>", encoding="utf-8")
    gone = "https://example.org/residency/gone"
    (ledger / "artists.csv").write_text(f"ledger_id,source_url\nL1,{gone}\n", encoding="utf-8")
    frames = tmp_path / "frames.yml"
    frames.write_text("frames: []\n", encoding="utf-8")
    config = Config(
        root=tmp_path,
        name="Synthetic",
        data=data,
        frames=frames,
        user_agent=UA,
        offline_roots=(("https://example.org", fixtures),),
    )
    assert not wayback_allowed(config)
    status = archive_cited(config)
    assert status[gone]["status"] == "unavailable"
    assert status[gone]["wayback"] == "offline"
    assert "capture_url" not in status[gone]


def test_settle_url_without_wayback_makes_no_archive_request(tmp_path: Path):
    def handler(url, **kwargs):
        parts = urlparse(url)
        if parts.path == "/robots.txt":
            return _allow_robots(url)
        if "archive.org" in parts.netloc:
            raise AssertionError(url)
        return FakeResponse(404, "gone", url)

    fetcher, session = _fetcher(handler)
    result = settle_url(PAGE, fetcher=fetcher, store=SnapshotStore(tmp_path / "raw"), wayback=False)
    assert result["status"] == "unavailable" and result["wayback"] == "offline"
    assert not any("archive.org" in call for call in session.calls)
