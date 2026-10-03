# SPDX-License-Identifier: MIT
"""Fetcher: robots.txt before every request, contact User-Agent, per-host delay, TLS retry."""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from giye.collect.fetch import Fetcher, RobotsDisallowed
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


def test_robots_cache_survives_a_later_disallow(tmp_path: Path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (root / "a.html").write_text("<p>a</p>", encoding="utf-8")
    (root / "b.html").write_text("<p>b</p>", encoding="utf-8")
    fetcher = Fetcher(UA, min_delay_s=0, offline_roots={"https://example.org": root})
    assert fetcher.get("https://example.org/a").text == "<p>a</p>"
    (root / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    # Cached robots.txt still allows the second page. A new Fetcher would refuse it.
    assert "b" in fetcher.get("https://example.org/b").text


def test_missing_or_http_error_robots_allows_the_page():
    def handler(url, **kwargs):
        if url.endswith("/robots.txt"):
            return FakeResponse(503, "unavailable", url, "text/plain")
        return FakeResponse(200, "<p>page</p>", url)

    session = FakeSession(handler)
    fetcher = Fetcher(UA, min_delay_s=0, timeout_s=11, robots_timeout_s=7, session=session)  # type: ignore[arg-type]
    page = fetcher.get("https://example.org/page")
    assert page.status == 200
    assert session.calls[0]["timeout"] == 7
    assert session.calls[1]["timeout"] == 11
    assert session.calls[1]["url"] == "https://example.org/page"


def test_robots_connection_error_allows_the_page():
    def handler(url, **kwargs):
        if url.endswith("/robots.txt"):
            raise requests.exceptions.ConnectionError("down")
        return FakeResponse(200, "ok", url)

    session = FakeSession(handler)
    fetcher = Fetcher(UA, min_delay_s=0, session=session)  # type: ignore[arg-type]
    assert fetcher.get("https://example.org/page").status == 200


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
