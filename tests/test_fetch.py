# SPDX-License-Identifier: AGPL-3.0-only
"""Fetcher: robots.txt before every request, contact User-Agent, per-host delay, TLS retry."""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from giye.collect.fetch import Fetcher, RobotsDisallowed, TermsRefused, is_social
from tests.conftest import serve

UA = "GiyeTest/0.1 (+https://example.org/contact)"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo" / "fixtures"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


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
        self.calls: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.handler(url, **kwargs)


def test_disallowed_url_is_never_requested():
    with serve(FIXTURES / "site") as (base, server):
        fetcher = Fetcher(UA, min_delay_s=0, timeout_s=5, robots_timeout_s=5)
        with pytest.raises(RobotsDisallowed):
            fetcher.get(base + "/private/secret.html")
        page = fetcher.get(base + "/hello.html")
    assert b"public-ok" in page.content
    paths = [hit[0] for hit in server.hits]
    assert paths.count("/robots.txt") == 1
    assert "/private/secret.html" not in paths
    assert "/hello.html" in paths
    assert all("example.org/contact" in (hit[1] or "") for hit in server.hits)


def test_offline_fixture_still_checks_robots_and_does_not_use_the_network():
    fetcher = Fetcher(UA, min_delay_s=0, offline_roots={"https://example.org": DEMO})

    def boom(url, **kwargs):
        raise AssertionError(f"network used: {url}")

    fetcher.session = FakeSession(boom)  # type: ignore[assignment]
    with pytest.raises(RobotsDisallowed):
        fetcher.get("https://example.org/private/secret.html")
    page = fetcher.get("https://example.org/residency/alumni")
    assert "김하늘" in page.text
    assert page.url == "https://example.org/residency/alumni"
    assert fetcher.session.calls == []  # type: ignore[attr-defined]


def test_robots_cache_is_kept_for_the_process(tmp_path: Path):
    from giye.collect.robots import clear_cache

    root = tmp_path / "site"
    root.mkdir()
    (root / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (root / "a.html").write_text("<p>a</p>", encoding="utf-8")
    (root / "b.html").write_text("<p>b</p>", encoding="utf-8")
    fetcher = Fetcher(UA, min_delay_s=0, offline_roots={"https://example.org": root})
    assert fetcher.get("https://example.org/a").text == "<p>a</p>"
    (root / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    # The outcome is remembered for this process, including a second Fetcher.
    assert "b" in fetcher.get("https://example.org/b").text
    again = Fetcher(UA, min_delay_s=0, offline_roots={"https://example.org": root})
    assert "b" in again.get("https://example.org/b").text
    clear_cache()
    blocked = Fetcher(UA, min_delay_s=0, offline_roots={"https://example.org": root})
    with pytest.raises(RobotsDisallowed):
        blocked.get("https://example.org/b")


def test_robots_404_is_unavailable_and_the_page_is_fetched():
    """RFC 9309: a 4xx robots.txt is unavailable, so the URL may be fetched."""
    from giye.collect.robots import VERDICT_UNAVAILABLE

    def handler(url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        if url.endswith("/robots.txt"):
            return FakeResponse(404, "missing", url, "text/plain")
        return FakeResponse(200, "<p>page</p>", url)

    session = FakeSession(handler)
    fetcher = Fetcher(UA, min_delay_s=0, timeout_s=11, robots_timeout_s=7, session=session)  # type: ignore[arg-type]
    page = fetcher.get("https://example.org/page")
    assert page.status == 200
    assert page.robots == VERDICT_UNAVAILABLE
    assert session.calls[0]["timeout"] == 7
    assert session.calls[1]["timeout"] == 11
    assert session.calls[1]["url"] == "https://example.org/page"


def test_robots_5xx_refuses_before_the_page_is_sent():
    """RFC 9309: a 5xx robots.txt is unreachable, a complete disallow for this process."""
    from giye.collect.robots import VERDICT_UNREACHABLE, RobotsRefused

    def handler(url, **kwargs):
        if url.endswith("/robots.txt"):
            return FakeResponse(503, "unavailable", url, "text/plain")
        raise AssertionError(url)

    session = FakeSession(handler)
    fetcher = Fetcher(UA, min_delay_s=0, session=session)  # type: ignore[arg-type]
    with pytest.raises(RobotsRefused) as caught:
        fetcher.get("https://example.org/page")
    assert caught.value.verdict == VERDICT_UNREACHABLE
    assert [call["url"] for call in session.calls] == ["https://example.org/robots.txt"]


def test_robots_connection_error_refuses_the_page():
    from giye.collect.robots import VERDICT_UNREACHABLE, RobotsRefused

    def handler(url, **kwargs):
        if url.endswith("/robots.txt"):
            raise requests.exceptions.ConnectionError("down")
        raise AssertionError(url)

    session = FakeSession(handler)
    fetcher = Fetcher(UA, min_delay_s=0, session=session)  # type: ignore[arg-type]
    with pytest.raises(RobotsRefused) as caught:
        fetcher.get("https://example.org/page")
    assert caught.value.verdict == VERDICT_UNREACHABLE
    assert [call["url"] for call in session.calls] == ["https://example.org/robots.txt"]


def test_tls_failure_retries_unverified_and_sets_the_flag():
    def handler(url, **kwargs):
        if url.endswith("/robots.txt"):
            return FakeResponse(200, "User-agent: *\nAllow: /\n", url, "text/plain")
        if kwargs.get("verify", True):
            raise requests.exceptions.SSLError("expired certificate")
        return FakeResponse(200, "<p>lenient</p>", url)

    session = FakeSession(handler)
    fetcher = Fetcher(UA, min_delay_s=0, session=session)  # type: ignore[arg-type]
    page = fetcher.get("https://example.org/page")
    assert page.tls_unverified is True
    assert "lenient" in page.text
    page_calls = [call for call in session.calls if not call["url"].endswith("/robots.txt")]
    assert "verify" not in page_calls[0] or page_calls[0]["verify"] is True
    assert page_calls[1]["verify"] is False


def test_per_host_delay_does_not_apply_across_hosts():
    clock = Clock()
    seen: list[tuple[str, float]] = []

    def handler(url, **kwargs):
        seen.append((url.split("/")[2], clock.t))
        body = "User-agent: *\nAllow: /\n" if url.endswith("/robots.txt") else "ok"
        return FakeResponse(200, body, url)

    session = FakeSession(handler)
    fetcher = Fetcher(UA, min_delay_s=2, session=session)  # type: ignore[arg-type]
    fetcher._now = clock.now
    fetcher._sleep = clock.sleep
    fetcher.get("https://a.example/page")
    fetcher.get("https://b.example/page")
    assert seen == [
        ("a.example", 0.0),
        ("a.example", 2.0),
        ("b.example", 2.0),
        ("b.example", 4.0),
    ]


def test_user_agent_must_carry_a_contact():
    with pytest.raises(ValueError):
        Fetcher("GiyeTest/0.1")


def test_terms_block_matches_the_host_not_a_lookalike():
    assert is_social("https://www.instagram.com/anna")
    assert is_social("https://instagram.com/anna")
    assert is_social("https://WWW.INSTAGRAM.COM:443/anna")
    assert is_social("https://m.facebook.com/anna")
    assert is_social("https://x.com/anna")
    assert not is_social("https://instagram.com.example.org/anna")
    assert not is_social("https://notinstagram.com/anna")
    assert not is_social("https://example.org/instagram.com")


def test_blocked_host_is_refused_before_robots_and_is_not_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom_decide(*_args, **_kwargs):
        raise AssertionError("robots.txt was consulted")

    monkeypatch.setattr("giye.collect.fetch.decide", boom_decide)

    def boom(url, **_kwargs):
        raise AssertionError(f"network used: {url}")

    root = tmp_path / "ig"
    root.mkdir()
    (root / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (root / "anna.html").write_text("<p>should not be read</p>", encoding="utf-8")
    fetcher = Fetcher(
        UA,
        min_delay_s=0,
        offline_roots={"https://www.instagram.com": root, "https://instagram.com": root},
    )
    fetcher.session = FakeSession(boom)  # type: ignore[assignment]
    for url in (
        "https://www.instagram.com/anna",
        "https://instagram.com/anna",
        "https://WWW.INSTAGRAM.COM:443/anna",
    ):
        with pytest.raises(TermsRefused) as caught:
            fetcher.get(url)
        assert caught.value.url == url
        assert caught.value.verdict == "platform_excluded"
        assert str(caught.value).startswith("not fetched (platform_excluded):")
    assert fetcher.session.calls == []  # type: ignore[attr-defined]
    assert "should not be read" in (root / "anna.html").read_text(encoding="utf-8")
    assert list(tmp_path.rglob("manifest.jsonl")) == []


def test_redirect_onto_a_blocked_host_is_not_sent():
    calls: list[str] = []

    def handler(url, **_kwargs):
        calls.append(url)
        if str(url).endswith("/robots.txt"):
            return FakeResponse(200, "User-agent: *\nAllow: /\n", url, "text/plain")
        if url == "https://example.org/start":
            response = FakeResponse(302, "", url)
            response.headers = {"Location": "https://www.instagram.com/anna"}
            return response
        raise AssertionError(f"blocked hop was requested: {url}")

    session = FakeSession(handler)
    fetcher = Fetcher(UA, min_delay_s=0, session=session)  # type: ignore[arg-type]
    with pytest.raises(TermsRefused) as caught:
        fetcher.get("https://example.org/start")
    assert caught.value.url == "https://www.instagram.com/anna"
    assert calls == ["https://example.org/robots.txt", "https://example.org/start"]


def test_instagram_cv_is_registered_and_never_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A CV on a blocked host stays in the registry. The fetcher never asks for it."""
    from datetime import date

    from giye.collect.evidence import settle_url
    from giye.collect.snapshot import SnapshotStore
    from giye.config import load
    from giye.extract.service import extract
    from giye.ledger.ledger import Ledger
    from giye.ledger.schemas import ARTISTS_FIELDS, empty_row

    def boom(self, url, *args, **kwargs):
        raise AssertionError(f"network used: {url}")

    monkeypatch.setattr(requests.Session, "get", boom)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    def boom_decide(*_args, **_kwargs):
        raise AssertionError("robots.txt was consulted")

    monkeypatch.setattr("giye.collect.fetch.decide", boom_decide)

    site = tmp_path / "site"
    site.mkdir()
    (site / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (site / "anna.html").write_text("<p>Anna Example, Example Hall, 2019</p>", encoding="utf-8")
    frames = Path(__file__).resolve().parents[1] / "examples" / "demo" / "frames.yml"
    config_path = tmp_path / "giye.toml"
    config_path.write_text(
        f"""
[archive]
name = "Synthetic extract test"
id_prefix = "GY"

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{frames.as_posix()}"

[collect]
user_agent = "{UA}"
min_delay_s = 0.0

[collect.offline_roots]
"https://www.instagram.com" = "{site.as_posix()}"

[extract]
cache = "{(tmp_path / "cache").as_posix()}"

[[extract.sources]]
ledger_id = "LED-anna"
lang = "en"
url = "https://www.instagram.com/anna"
source_id = "CV-ANNA-en"
""",
        encoding="utf-8",
    )
    ledger = Ledger.open(load(config_path))
    ledger.write(
        "artists",
        [empty_row(ARTISTS_FIELDS, ledger_id="LED-anna", name_ko="Anna Example", name_en="Anna Example", status="STAGED")],
        task="test",
    )
    result = extract(load(config_path), replay_only=True, today=date(2026, 10, 4))
    assert result.registered == 1
    assert result.pull.get("error") == 1
    row = Ledger.open(load(config_path)).read("cv_sources")[0]
    assert row["url"] == "https://www.instagram.com/anna"
    assert row["source_id"] == "CV-ANNA-en"
    assert row["snapshot_path"] == ""
    assert row["last_status"] == "error"
    queue = Ledger.open(load(config_path)).read("review_queue")
    assert queue[0]["reason"] == "cv_pull_failed"
    assert "TermsRefused" in queue[0]["detail"]
    assert "platform_excluded" in queue[0]["detail"]
    assert not (tmp_path / "data" / "raw" / "cv").exists()
    assert list((tmp_path / "data").rglob("manifest.jsonl")) == []

    def refuse(url, **_kwargs):
        raise AssertionError(f"network used: {url}")

    fetcher = Fetcher(UA, min_delay_s=0, offline_roots={"https://www.instagram.com": site})
    fetcher.session = FakeSession(refuse)  # type: ignore[assignment]
    settled = settle_url("https://www.instagram.com/anna", fetcher=fetcher, store=SnapshotStore(tmp_path / "evidence"))
    assert settled["status"] == "platform_excluded"
    assert settled["reason"] == "terms"
    assert settled["robots"] == "platform_excluded"
    assert list((tmp_path / "evidence").rglob("manifest.jsonl")) == []
