# SPDX-License-Identifier: AGPL-3.0-only
"""Fetcher limits and politeness: size cap, total deadline, Crawl-delay, Retry-After, robots redirects."""

from __future__ import annotations

import pytest

from giye.collect import robots
from giye.collect.fetch import BodyRefused, Fetcher, HostBusy
from tests.test_fetch import UA, Clock, FakeResponse, FakeSession


@pytest.fixture(autouse=True)
def _fresh_robots():
    robots.clear_cache()
    yield
    robots.clear_cache()


def _fetcher(handler, clock: Clock | None = None, **kwargs) -> tuple[Fetcher, FakeSession]:
    session = FakeSession(handler)
    fetcher = Fetcher(UA, session=session, **{"min_delay_s": 0, **kwargs})  # type: ignore[arg-type]
    if clock is not None:
        fetcher._now = clock.now
        fetcher._sleep = clock.sleep
    return fetcher, session


def _robots(body: str = "User-agent: *\nAllow: /\n"):
    def answer(url):
        return FakeResponse(200, body, url, "text/plain")

    return answer


def test_declared_oversize_body_is_refused_before_reading():
    def handler(url, **kwargs):
        if url.endswith("/robots.txt"):
            return _robots()(url)
        response = FakeResponse(200, b"x", url)
        response.headers["Content-Length"] = str(200 * 1024 * 1024)
        return response

    fetcher, _ = _fetcher(handler)
    with pytest.raises(BodyRefused):
        fetcher.get("https://example.org/big")


def test_streamed_body_over_the_cap_or_the_deadline_is_refused(monkeypatch):
    from giye.collect import fetch

    clock = Clock()

    class Streamed(FakeResponse):
        def iter_content(self, size):
            for _ in range(10):
                clock.t += 1.0
                yield b"y" * 10

    def handler(url, **kwargs):
        if url.endswith("/robots.txt"):
            return _robots()(url)
        return Streamed(200, b"", url)

    monkeypatch.setattr(fetch, "MAX_BYTES", 50)
    fetcher, _ = _fetcher(handler, clock, timeout_s=100)
    with pytest.raises(BodyRefused, match="limit"):
        fetcher.get("https://example.org/stream")
    monkeypatch.setattr(fetch, "MAX_BYTES", 10_000)
    fetcher, _ = _fetcher(handler, clock, timeout_s=0.5)
    with pytest.raises(BodyRefused, match="in total"):
        fetcher.get("https://example.org/stream")


def test_crawl_delay_is_honoured():
    clock = Clock()
    seen: list[float] = []

    def handler(url, **kwargs):
        if url.endswith("/robots.txt"):
            return _robots("User-agent: *\nCrawl-delay: 10\nAllow: /\n")(url)
        seen.append(clock.t)
        return FakeResponse(200, "ok", url)

    fetcher, _ = _fetcher(handler, clock, min_delay_s=2)
    fetcher.get("https://example.org/a")
    fetcher.get("https://example.org/b")
    assert seen[1] - seen[0] >= 10


def test_long_retry_after_refuses_the_host_for_this_run():
    clock = Clock()

    def handler(url, **kwargs):
        if url.endswith("/robots.txt"):
            return _robots()(url)
        response = FakeResponse(503, "busy", url)
        response.headers["Retry-After"] = "3600"
        return response

    fetcher, session = _fetcher(handler, clock)
    assert fetcher.get("https://example.org/roster").status == 503
    with pytest.raises(HostBusy):
        fetcher.get("https://example.org/roster?page=2")
    assert sum(1 for call in session.calls if "roster" in call["url"]) == 1


def test_short_retry_after_waits():
    clock = Clock()

    def handler(url, **kwargs):
        if url.endswith("/robots.txt"):
            return _robots()(url)
        response = FakeResponse(429, "slow down", url)
        response.headers["Retry-After"] = "30"
        return response

    fetcher, _ = _fetcher(handler, clock)
    fetcher.get("https://example.org/a")
    fetcher.get("https://example.org/b")
    assert clock.t >= 30


def test_robots_redirect_onto_a_blocked_host_is_not_sent():
    sent: list[str] = []

    def handler(url, **kwargs):
        sent.append(url)
        if url == "https://example.org/robots.txt":
            response = FakeResponse(301, "", url)
            response.headers["Location"] = "https://www.instagram.com/robots.txt"
            return response
        return FakeResponse(200, "ok", url)

    fetcher, _ = _fetcher(handler)
    assert fetcher.get("https://example.org/page").status == 200
    assert not any("instagram" in url for url in sent)
