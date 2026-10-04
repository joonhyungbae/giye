# SPDX-License-Identifier: AGPL-3.0-only
"""RFC 9309 robots.txt status and path rules, including the browser document guard. No network.

Adapted from the production ``tests/test_robots.py``. Fetch refusal goes through
``Fetcher`` and ``SnapshotStore``. The document guard is the same functions, so a
browser collector can pause each document hop. These tests do not open a browser
or a socket.
"""

from __future__ import annotations

import json
import unittest
import unittest.mock
from pathlib import Path

from giye.collect.base import RosterCollector
from giye.collect.fetch import Fetcher
from giye.collect.robots import (
    VERDICT_ALLOWED,
    VERDICT_DISALLOWED,
    VERDICT_UNAVAILABLE,
    VERDICT_UNREACHABLE,
    RobotsRefused,
    clear_cache,
    decide,
    guarded_request,
    handle_document_route,
    install_document_guard,
)
from giye.collect.snapshot import SnapshotStore
from giye.config import Config

UA = "GiyeArchiveBot/0.2 (+https://example.org/contact)"


class Response:
    def __init__(self, status: int, text: str = "", headers: dict | None = None, url: str = ""):
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers or {}
        self.url = url
        self.encoding = "utf-8"


def _rules(text: str) -> str:
    return text.strip() + "\n"


class RobotsStatusTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_cache()

    def test_200_parses_allow_and_disallow(self) -> None:
        body = _rules("User-agent: *\nDisallow: /private")

        def get(url, timeout):
            self.assertTrue(url.endswith("/robots.txt"))
            return Response(200, body, url=url)

        blocked = decide("https://example.test/private/x", UA, get=get, use_cache=False)
        open_ = decide("https://example.test/public", UA, get=get, use_cache=False)
        self.assertEqual(blocked.verdict, VERDICT_DISALLOWED)
        self.assertFalse(blocked.permits)
        self.assertEqual(blocked.http_status, 200)
        self.assertEqual(open_.verdict, VERDICT_ALLOWED)
        self.assertTrue(open_.permits)

    def test_404_is_unavailable_allowed(self) -> None:
        def get(url, timeout):
            return Response(404, url=url)

        verdict = decide("https://example.test/any", UA, get=get, use_cache=False)
        self.assertEqual(verdict.verdict, VERDICT_UNAVAILABLE)
        self.assertTrue(verdict.permits)
        self.assertEqual(verdict.http_status, 404)

    def test_403_is_unavailable_allowed(self) -> None:
        # Every 4xx is unavailable, including the codes urllib.robotparser used to disallow.
        verdict = decide(
            "https://example.test/any",
            UA,
            get=lambda url, timeout: Response(403, url=url),
            use_cache=False,
        )
        self.assertEqual(verdict.verdict, VERDICT_UNAVAILABLE)
        self.assertTrue(verdict.permits)

    def test_500_is_unreachable_disallow(self) -> None:
        verdict = decide(
            "https://example.test/any",
            UA,
            get=lambda url, timeout: Response(500, url=url),
            use_cache=False,
        )
        self.assertEqual(verdict.verdict, VERDICT_UNREACHABLE)
        self.assertFalse(verdict.permits)
        self.assertEqual(verdict.http_status, 500)

    def test_timeout_is_unreachable_disallow(self) -> None:
        def get(url, timeout):
            raise TimeoutError("timed out")

        verdict = decide("https://example.test/any", UA, get=get, use_cache=False)
        self.assertEqual(verdict.verdict, VERDICT_UNREACHABLE)
        self.assertFalse(verdict.permits)
        self.assertEqual(verdict.error, "timeout")

    def test_network_error_is_unreachable_disallow(self) -> None:
        def get(url, timeout):
            raise ConnectionError("refused")

        verdict = decide("https://example.test/any", UA, get=get, use_cache=False)
        self.assertEqual(verdict.verdict, VERDICT_UNREACHABLE)
        self.assertEqual(verdict.error, "network")

    def test_five_redirects_then_200_is_parsed(self) -> None:
        body = _rules("User-agent: *\nDisallow: /secret")
        seen: list[str] = []

        def get(url, timeout):
            seen.append(url)
            if len(seen) <= 5:
                return Response(302, headers={"Location": f"https://cdn.example.test/robots.txt?h={len(seen)}"})
            return Response(200, body, url=url)

        verdict = decide("https://example.test/secret", UA, get=get, use_cache=False)
        self.assertEqual(len(seen), 6)
        self.assertEqual(verdict.verdict, VERDICT_DISALLOWED)
        self.assertEqual(verdict.http_status, 200)

    def test_sixth_redirect_is_unavailable(self) -> None:
        def get(url, timeout):
            return Response(301, headers={"Location": url + "x"})

        verdict = decide("https://example.test/page", UA, get=get, use_cache=False)
        self.assertEqual(verdict.verdict, VERDICT_UNAVAILABLE)
        self.assertTrue(verdict.permits)
        self.assertEqual(verdict.error, "too_many_redirects")

    def test_unreachable_is_cached_for_the_run(self) -> None:
        calls = {"n": 0}

        def get(url, timeout):
            calls["n"] += 1
            return Response(503, url=url)

        clear_cache()
        first = decide("https://cached.example.test/a", UA, get=get)
        second = decide("https://cached.example.test/b", UA, get=get)
        self.assertEqual(first.verdict, VERDICT_UNREACHABLE)
        self.assertEqual(second.verdict, VERDICT_UNREACHABLE)
        self.assertEqual(calls["n"], 1)

    def test_bad_url_does_not_fetch(self) -> None:
        def get(url, timeout):
            raise AssertionError("should not fetch")

        verdict = decide("http://", UA, get=get, use_cache=False)
        self.assertEqual(verdict.verdict, VERDICT_UNREACHABLE)
        self.assertEqual(verdict.error, "bad_url")


def _verdict(body: str, url: str, ua: str = UA):
    text = body if body.endswith("\n") else body + "\n"

    def get(fetch_url, timeout):
        return Response(200, text, url=fetch_url)

    return decide(url, ua, get=get, use_cache=False)


# RFC 9309 §5.1. foobot is a specific group and does not inherit the "*" rules.
_RFC_51 = """
User-Agent: *
Disallow: *.gif$
Disallow: /example/
Allow: /publications/

User-Agent: foobot
Disallow:/
Allow:/example/page.html
Allow:/example/allowed.gif

User-Agent: barbot
User-Agent: bazbot
Disallow: /example/page.html

User-Agent: quxbot

"""


class PathMatchTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_cache()

    def test_rfc_51_star_group(self) -> None:
        other = "OtherBot/1.0"
        # /publications/ is longer than *.gif$, so a gif under that prefix is allowed.
        self.assertEqual(
            _verdict(_RFC_51, "https://example.test/publications/a.gif", other).verdict, VERDICT_ALLOWED
        )
        self.assertEqual(_verdict(_RFC_51, "https://example.test/publications/a", other).verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(_RFC_51, "https://example.test/example/a", other).verdict, VERDICT_DISALLOWED)
        self.assertEqual(_verdict(_RFC_51, "https://example.test/foo.gif", other).verdict, VERDICT_DISALLOWED)
        # '$' means the path must end at .gif.
        self.assertEqual(_verdict(_RFC_51, "https://example.test/foo.gif.bak", other).verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(_RFC_51, "https://example.test/other", other).verdict, VERDICT_ALLOWED)

    def test_rfc_51_foobot_does_not_inherit_star(self) -> None:
        ua = "foobot/1.0"
        self.assertEqual(_verdict(_RFC_51, "https://example.test/example/page.html", ua).verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(_RFC_51, "https://example.test/example/allowed.gif", ua).verdict, VERDICT_ALLOWED)
        # The "*" group's Allow: /publications/ does not apply inside foobot's group.
        self.assertEqual(_verdict(_RFC_51, "https://example.test/publications/x", ua).verdict, VERDICT_DISALLOWED)
        self.assertEqual(_verdict(_RFC_51, "https://example.test/other.gif", ua).verdict, VERDICT_DISALLOWED)

    def test_rfc_51_shared_group_and_empty_group(self) -> None:
        self.assertEqual(
            _verdict(_RFC_51, "https://example.test/example/page.html", "barbot/1").verdict, VERDICT_DISALLOWED
        )
        self.assertEqual(
            _verdict(_RFC_51, "https://example.test/example/page.html/more", "BazBot/2").verdict, VERDICT_DISALLOWED
        )
        self.assertEqual(_verdict(_RFC_51, "https://example.test/x.gif", "bazbot/1").verdict, VERDICT_ALLOWED)
        # quxbot's group has no rules, so it does not fall through to "*".
        self.assertEqual(_verdict(_RFC_51, "https://example.test/example/a.gif", "quxbot/1").verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(_RFC_51, "https://example.test/anything", "quxbot").verdict, VERDICT_ALLOWED)

    def test_rfc_52_longest_match(self) -> None:
        # The RFC prose names "disallow.gif"; the rule that must win is the longer one written below.
        body = "User-Agent: foobot\nAllow: /example/page/\nDisallow: /example/page/disallowed.gif\n"
        ua = "foobot/1.0"
        self.assertEqual(
            _verdict(body, "https://example.test/example/page/disallowed.gif", ua).verdict, VERDICT_DISALLOWED
        )
        self.assertEqual(_verdict(body, "https://example.test/example/page/other", ua).verdict, VERDICT_ALLOWED)

    def test_empty_disallow_allows_everything(self) -> None:
        body = "User-agent: *\nDisallow:\n"
        self.assertEqual(_verdict(body, "https://example.test/anything").verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/").verdict, VERDICT_ALLOWED)

    def test_empty_disallow_does_not_cancel_a_real_rule(self) -> None:
        body = "User-agent: *\nDisallow:\nDisallow: /hidden\n"
        self.assertEqual(_verdict(body, "https://example.test/hidden/x").verdict, VERDICT_DISALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/open").verdict, VERDICT_ALLOWED)

    def test_allow_wins_equal_length(self) -> None:
        body = "User-agent: *\nDisallow: /folder\nAllow: /folder\n"
        self.assertEqual(_verdict(body, "https://example.test/folder").verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/folder/x").verdict, VERDICT_ALLOWED)

    def test_dollar_anchor_is_more_specific_than_the_prefix(self) -> None:
        body = "User-agent: *\nAllow: /$\nDisallow: /\n"
        self.assertEqual(_verdict(body, "https://example.test/").verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/index").verdict, VERDICT_DISALLOWED)

    def test_dollar_only_at_end_of_pattern(self) -> None:
        body = "User-agent: *\nDisallow: /file.gif$\n"
        self.assertEqual(_verdict(body, "https://example.test/file.gif").verdict, VERDICT_DISALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/file.gif/extra").verdict, VERDICT_ALLOWED)

    def test_star_wildcard(self) -> None:
        body = "User-agent: *\nDisallow: /\nAllow: /this/*/exactly\n"
        self.assertEqual(_verdict(body, "https://example.test/this/ab/exactly").verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/this/ab/cd/exactly").verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/nope").verdict, VERDICT_DISALLOWED)

    def test_most_specific_product_token_then_star(self) -> None:
        body = """
        User-agent: *
        Disallow: /star

        User-agent: Giye
        Disallow: /short

        User-agent: GiyeArchiveBot
        Disallow: /long

        User-agent: GiyeArchiveBot
        Disallow: /also
        """
        self.assertEqual(_verdict(body, "https://example.test/long").verdict, VERDICT_DISALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/also").verdict, VERDICT_DISALLOWED)
        # "Giye" is a different product token, not a less-specific match for ours.
        self.assertEqual(_verdict(body, "https://example.test/short").verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/star").verdict, VERDICT_ALLOWED)
        other = _verdict(body, "https://example.test/star", "OtherBot/1.0")
        self.assertEqual(other.verdict, VERDICT_DISALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/long", "OtherBot/1.0").verdict, VERDICT_ALLOWED)

    def test_product_token_match_is_case_insensitive(self) -> None:
        body = "User-agent: " + "GiyeArchiveBot".lower() + "\nDisallow: /Private\n"
        self.assertEqual(_verdict(body, "https://example.test/Private").verdict, VERDICT_DISALLOWED)
        # The path itself stays case-sensitive.
        self.assertEqual(_verdict(body, "https://example.test/private").verdict, VERDICT_ALLOWED)

    def test_rules_before_the_first_group_are_ignored(self) -> None:
        body = "Disallow: /secret\nUser-agent: *\nDisallow: /hidden\n"
        self.assertEqual(_verdict(body, "https://example.test/secret").verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/hidden").verdict, VERDICT_DISALLOWED)

    def test_sitemap_does_not_end_the_group(self) -> None:
        body = "User-agent: *\nSitemap: https://example.test/sitemap.xml\nDisallow: /hidden\n"
        self.assertEqual(_verdict(body, "https://example.test/hidden").verdict, VERDICT_DISALLOWED)

    def test_robots_txt_is_always_allowed(self) -> None:
        body = "User-agent: *\nDisallow: /\n"
        self.assertEqual(_verdict(body, "https://example.test/robots.txt").verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/robots.txt?x=1").verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/Robots.txt").verdict, VERDICT_DISALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/other").verdict, VERDICT_DISALLOWED)

    def test_percent_encoding_unreserved_and_non_ascii(self) -> None:
        # RFC 9309 Figure 4: %62%61%7A is "baz"; U+30C4 encodes as UTF-8 E3 83 84.
        encoded = "User-agent: *\nDisallow: /foo/bar/%62%61%7A\n"
        self.assertEqual(_verdict(encoded, "https://example.test/foo/bar/baz").verdict, VERDICT_DISALLOWED)
        self.assertEqual(_verdict(encoded, "https://example.test/foo/bar/%62%61%7A").verdict, VERDICT_DISALLOWED)
        self.assertEqual(_verdict(encoded, "https://example.test/foo/bar/qux").verdict, VERDICT_ALLOWED)
        raw = "User-agent: *\nDisallow: /foo/bar/ツ\n"
        self.assertEqual(_verdict(raw, "https://example.test/foo/bar/%E3%83%84").verdict, VERDICT_DISALLOWED)
        self.assertEqual(_verdict(raw, "https://example.test/foo/bar/%e3%83%84").verdict, VERDICT_DISALLOWED)

    def test_query_reserves_are_encoded_before_compare(self) -> None:
        # Figure 4: the query value https://foo.bar is matched as https%3A%2F%2Ffoo.bar.
        body = "User-agent: *\nDisallow: /foo/bar?baz=https%3A%2F%2Ffoo.bar\n"
        self.assertEqual(
            _verdict(body, "https://example.test/foo/bar?baz=https://foo.bar").verdict, VERDICT_DISALLOWED
        )
        self.assertEqual(
            _verdict(body, "https://example.test/foo/bar?baz=https%3A%2F%2Ffoo.bar").verdict, VERDICT_DISALLOWED
        )
        self.assertEqual(_verdict(body, "https://example.test/foo/bar?baz=other").verdict, VERDICT_ALLOWED)

    def test_literal_star_and_dollar_are_percent_encoded(self) -> None:
        # RFC 9309 Figure 6.
        star = "User-agent: *\nDisallow: /path/file-with-a-%2A.html\n"
        self.assertEqual(
            _verdict(star, "https://example.test/path/file-with-a-*.html").verdict, VERDICT_DISALLOWED
        )
        self.assertEqual(
            _verdict(star, "https://example.test/path/file-with-a-x.html").verdict, VERDICT_ALLOWED
        )
        dollar = "User-agent: *\nDisallow: /path/foo-%24\n"
        self.assertEqual(_verdict(dollar, "https://example.test/path/foo-$").verdict, VERDICT_DISALLOWED)
        self.assertEqual(_verdict(dollar, "https://example.test/path/foo-x").verdict, VERDICT_ALLOWED)

    def test_inline_comment_does_not_become_part_of_the_path(self) -> None:
        body = "User-agent: *\nDisallow: /\nAllow: /public # still public\n"
        self.assertEqual(_verdict(body, "https://example.test/public/x").verdict, VERDICT_ALLOWED)
        self.assertEqual(_verdict(body, "https://example.test/secret").verdict, VERDICT_DISALLOWED)


class TlsRetryTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_cache()

    def test_tls_failure_is_retried_once_and_recorded(self) -> None:
        import requests

        class Resp:
            status_code = 200
            headers: dict | None = None
            url = "https://example.test/robots.txt"

            def __init__(self) -> None:
                self.headers = {}

            def iter_content(self, n):
                yield b"User-agent: *\nDisallow: /secret\n"

            def close(self):
                return None

        class Sess:
            def __init__(self) -> None:
                self.verified: list[bool] = []

            def get(self, url, timeout, allow_redirects, stream, verify, headers):
                self.verified.append(verify)
                if verify:
                    raise requests.exceptions.SSLError("expired")
                return Resp()

        verdict = decide("https://example.test/secret", UA, session=Sess(), use_cache=False)
        self.assertEqual(verdict.verdict, VERDICT_DISALLOWED)
        self.assertTrue(verdict.tls_unverified)
        self.assertFalse(verdict.permits)


class _Frame(RosterCollector):
    frame = "frame"

    def editions(self):
        return iter(())


class _Session:
    """Stand-in for ``requests.Session``. ``get`` is what ``Fetcher`` calls."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.headers: dict[str, str] = {}
        self.max_redirects = 30

    def get(self, url, **kwargs):
        return self.handler(url, **kwargs)


class SessionRefuseTests(unittest.TestCase):
    """The public fetcher refuses before send and records the last hop's verdict."""

    def setUp(self) -> None:
        import tempfile

        clear_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = SnapshotStore(self.root)
        self.config = Config(root=self.root, name="Synthetic", data=self.root, user_agent=UA, min_delay_s=0)

    def tearDown(self) -> None:
        clear_cache()
        self._tmp.cleanup()

    def _collector(self, session: _Session) -> _Frame:
        fetcher = Fetcher(UA, min_delay_s=0, session=session)  # type: ignore[arg-type]
        return _Frame(self.config, fetcher=fetcher, store=self.store)

    def test_disallowed_is_not_sent(self) -> None:
        sent: list[str] = []

        def handler(url, **kwargs):
            sent.append(str(url))
            if str(url).endswith("/robots.txt"):
                return Response(200, "User-agent: *\nDisallow: /private\n", url=url)
            raise AssertionError("request was sent")

        with (
            self.assertLogs("giye.collect.robots", level="WARNING") as logs,
            self.assertRaises(RobotsRefused) as caught,
        ):
            self._collector(_Session(handler)).fetch("https://example.test/private/x")
        self.assertEqual(sent, ["https://example.test/robots.txt"])
        self.assertEqual(caught.exception.verdict, VERDICT_DISALLOWED)
        self.assertTrue(any("disallowed" in line for line in logs.output))
        self.assertFalse((self.root / "frame").exists())

    def test_allowed_fetch_records_the_verdict(self) -> None:
        def handler(url, **kwargs):
            if str(url).endswith("/robots.txt"):
                return Response(200, "User-agent: *\nDisallow: /private\n", url=url)
            page = Response(200, "<html>public</html>", url=url)
            page.headers = {"Content-Type": "text/html; charset=utf-8"}
            return page

        self._collector(_Session(handler)).fetch("https://example.test/public")
        manifest = self.root / "frame" / "snapshots" / "manifest.jsonl"
        row = json.loads(manifest.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(row["robots"], VERDICT_ALLOWED)
        self.assertEqual(row["status"], 200)
        self.assertEqual(row["content_type"], "text/html; charset=utf-8")
        self.assertEqual(row["final_url"], "https://example.test/public")
        self.assertNotEqual(row["robots"], "not_checked")

    def test_unreachable_is_not_sent(self) -> None:
        sent: list[str] = []

        def handler(url, **kwargs):
            sent.append(str(url))
            if str(url).endswith("/robots.txt"):
                return Response(503, url=url)
            raise AssertionError("request was sent")

        with self.assertRaises(RobotsRefused) as caught:
            self._collector(_Session(handler)).fetch("https://down.example.test/page")
        self.assertEqual(sent, ["https://down.example.test/robots.txt"])
        self.assertEqual(caught.exception.verdict, VERDICT_UNREACHABLE)

    def test_disallowed_redirect_hop_is_not_sent(self) -> None:
        sent: list[str] = []

        def handler(url, **kwargs):
            sent.append(str(url))
            self.assertIs(kwargs.get("allow_redirects"), False)
            if str(url).endswith("/robots.txt") and "other.example.test" in str(url):
                return Response(200, "User-agent: *\nDisallow: /secret\n", url=url)
            if str(url).endswith("/robots.txt"):
                return Response(200, "User-agent: *\nDisallow:\n", url=url)
            if str(url) == "https://example.test/start":
                return _Hop(302, str(url), location="https://other.example.test/secret")
            raise AssertionError(f"disallowed hop was requested: {url}")

        with (
            self.assertLogs("giye.collect.robots", level="WARNING") as logs,
            self.assertRaises(RobotsRefused) as caught,
        ):
            self._collector(_Session(handler)).fetch("https://example.test/start")
        self.assertEqual(
            sent,
            [
                "https://example.test/robots.txt",
                "https://example.test/start",
                "https://other.example.test/robots.txt",
            ],
        )
        self.assertEqual(caught.exception.url, "https://other.example.test/secret")
        self.assertEqual(caught.exception.verdict, VERDICT_DISALLOWED)
        self.assertTrue(any("other.example.test/secret" in line for line in logs.output))
        self.assertFalse((self.root / "frame").exists())

    def test_allowed_redirect_records_the_final_hop(self) -> None:
        sent: list[str] = []

        def handler(url, **kwargs):
            sent.append(str(url))
            if str(url).endswith("/robots.txt") and "other.example.test" in str(url):
                return Response(404, "", url=url)
            if str(url).endswith("/robots.txt"):
                return Response(200, "User-agent: *\nDisallow: /private\n", url=url)
            if str(url) == "https://example.test/public":
                return _Hop(302, str(url), location="https://other.example.test/landed")
            return _Hop(200, str(url), body=b"<html>landed</html>")

        self._collector(_Session(handler)).fetch("https://example.test/public")
        self.assertEqual(
            sent,
            [
                "https://example.test/robots.txt",
                "https://example.test/public",
                "https://other.example.test/robots.txt",
                "https://other.example.test/landed",
            ],
        )
        manifest = self.root / "frame" / "snapshots" / "manifest.jsonl"
        row = json.loads(manifest.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(row["final_url"], "https://other.example.test/landed")
        self.assertEqual(row["robots"], VERDICT_UNAVAILABLE)
        self.assertEqual(row["url"], "https://example.test/public")


class _Hop:
    def __init__(self, status: int, url: str, location: str = "", body: bytes = b"") -> None:
        self.status_code = status
        self.url = url
        self.headers = {"Location": location} if location else {"Content-Type": "text/html"}
        self.content = body
        self.ok = status < 400
        self.closed = False

    def close(self) -> None:
        self.closed = True


class RedirectHopTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_cache()

    def _prime(self, url: str, body: str, status: int = 200) -> None:
        decide(url, UA, get=lambda fetch_url, timeout: Response(status, body, url=fetch_url))

    def test_same_host_redirect_to_a_disallowed_path_is_not_sent(self) -> None:
        import requests

        self._prime("https://a.example.test/start", "User-agent: *\nDisallow: /private\n")
        session = requests.Session()
        session.headers["User-Agent"] = UA
        sent: list[str] = []

        def send_checked(self, method, url, *args, **kwargs):
            sent.append(str(url))
            if kwargs.get("allow_redirects", True) is not False:
                raise AssertionError("automatic redirects were left on")
            return _Hop(302, str(url), location="/private")

        with (
            self.assertLogs("giye.collect.robots", level="WARNING") as logs,
            unittest.mock.patch.object(requests.Session, "request", send_checked),
            self.assertRaises(RobotsRefused) as caught,
        ):
            guarded_request(session, "GET", "https://a.example.test/start")
        self.assertEqual(sent, ["https://a.example.test/start"])
        self.assertEqual(caught.exception.url, "https://a.example.test/private")
        self.assertEqual(caught.exception.verdict, VERDICT_DISALLOWED)
        self.assertTrue(any("disallowed" in line and "/private" in line for line in logs.output))

    def test_allowed_redirect_is_followed(self) -> None:
        import requests

        self._prime("https://a.example.test/start", "User-agent: *\nDisallow:\n")
        self._prime("https://b.example.test/landed", "User-agent: *\nDisallow:\n")
        session = requests.Session()
        session.headers["User-Agent"] = UA
        sent: list[str] = []

        def send(self, method, url, *args, **kwargs):
            sent.append(str(url))
            if str(url).endswith("/start"):
                return _Hop(302, str(url), location="https://b.example.test/landed")
            return _Hop(200, str(url), body=b"ok")

        with unittest.mock.patch.object(requests.Session, "request", send):
            fetched = guarded_request(session, "GET", "https://a.example.test/start")
        self.assertEqual(sent, ["https://a.example.test/start", "https://b.example.test/landed"])
        self.assertEqual(fetched.verdict, VERDICT_ALLOWED)
        self.assertEqual(fetched.response.status_code, 200)

    def test_redirect_limit_does_not_request_past_the_ceiling(self) -> None:
        import requests

        self._prime("https://a.example.test/start", "User-agent: *\nDisallow:\n")
        session = requests.Session()
        session.headers["User-Agent"] = UA
        session.max_redirects = 0
        sent: list[str] = []

        def send(self, method, url, *args, **kwargs):
            sent.append(str(url))
            return _Hop(302, str(url), location="https://a.example.test/next")

        with (
            unittest.mock.patch.object(requests.Session, "request", send),
            self.assertRaises(requests.exceptions.TooManyRedirects),
        ):
            guarded_request(session, "GET", "https://a.example.test/start")
        self.assertEqual(sent, ["https://a.example.test/start"])


class _FakeRoute:
    """A Playwright route, or one paused CDP document request, reduced to the calls we make."""

    def __init__(self, url: str, resource_type: str, headers: dict | None = None) -> None:
        self.request = type("Req", (), {})()
        self.request.url = url
        self.request.resource_type = resource_type
        self.request.headers = {"User-Agent": UA} if headers is None else headers
        self.actions: list[str] = []

    def abort(self) -> None:
        self.actions.append("abort")

    def continue_(self) -> None:
        self.actions.append("continue")


class DocumentRouteTests(unittest.TestCase):
    """The handler refuses document navigations. Redirect hops and frames are documents."""

    def tearDown(self) -> None:
        clear_cache()

    def _prime(self, url: str, body: str, status: int = 200) -> None:
        decide(url, UA, get=lambda fetch_url, timeout: Response(status, body, url=fetch_url))

    def test_disallowed_document_is_aborted_and_logged(self) -> None:
        self._prime("https://example.test/private", "User-agent: *\nDisallow: /private\n")
        route = _FakeRoute("https://example.test/private/x", "document")
        with self.assertLogs("giye.collect.robots", level="WARNING") as logs:
            handle_document_route(route)
        self.assertEqual(route.actions, ["abort"])
        self.assertTrue(any("disallowed" in line and "/private/x" in line for line in logs.output))

    def test_unreachable_document_is_aborted_and_logged(self) -> None:
        self._prime("https://example.test/page", "User-agent: *\nDisallow:\n", status=500)
        route = _FakeRoute("https://example.test/page", "Document")
        with self.assertLogs("giye.collect.robots", level="WARNING") as logs:
            handle_document_route(route)
        self.assertEqual(route.actions, ["abort"])
        self.assertTrue(any("unreachable_disallowed" in line for line in logs.output))

    def test_allowed_document_is_continued(self) -> None:
        self._prime("https://example.test/public", "User-agent: *\nDisallow: /private\n")
        route = _FakeRoute("https://example.test/public", "document", headers={"user-agent": UA})
        with self.assertNoLogs("giye.collect.robots", level="WARNING"):
            handle_document_route(route)
        self.assertEqual(route.actions, ["continue"])

    def test_unavailable_robots_still_loads_the_document(self) -> None:
        self._prime("https://example.test/public", "", status=404)
        route = _FakeRoute("https://example.test/public", "document")
        handle_document_route(route)
        self.assertEqual(route.actions, ["continue"])

    def test_redirect_hop_and_frame_are_judged(self) -> None:
        # Each hop and each frame arrives as its own document request.
        self._prime("https://a.example.test/start", "User-agent: *\nDisallow: /private\n")
        first = _FakeRoute("https://a.example.test/start", "document")
        hop = _FakeRoute("https://a.example.test/private", "Document")
        frame = _FakeRoute("https://a.example.test/private/embed", "document")
        handle_document_route(first)
        with self.assertLogs("giye.collect.robots", level="WARNING"):
            handle_document_route(hop)
            handle_document_route(frame)
        self.assertEqual(first.actions, ["continue"])
        self.assertEqual(hop.actions, ["abort"])
        self.assertEqual(frame.actions, ["abort"])

    def test_subresources_are_not_judged(self) -> None:
        with unittest.mock.patch("giye.collect.robots.decide") as decide_mock:
            for resource_type in ("image", "stylesheet", "script"):
                route = _FakeRoute("https://example.test/private/x", resource_type)
                handle_document_route(route)
                self.assertEqual(route.actions, ["continue"])
        decide_mock.assert_not_called()


class _CdpSession:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.handlers: dict = {}

    def send(self, method: str, params: dict | None = None) -> None:
        self.sent.append((method, params or {}))

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler


class _CdpPage:
    def __init__(self) -> None:
        self.context = self
        self.sessions: list[_CdpSession] = []

    def new_cdp_session(self, page) -> _CdpSession:
        session = _CdpSession()
        self.sessions.append(session)
        return session


class DocumentGuardInstallTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_cache()

    def test_install_pauses_documents_and_fails_a_disallowed_one(self) -> None:
        decide(
            "https://example.test/private",
            UA,
            get=lambda fetch_url, timeout: Response(
                200, "User-agent: *\nDisallow: /private\n", url=fetch_url
            ),
        )
        page = _CdpPage()
        install_document_guard(page)
        self.assertEqual(len(page.sessions), 1)
        session = page.sessions[0]
        self.assertEqual(
            session.sent[0],
            (
                "Fetch.enable",
                {
                    "patterns": [
                        {"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}
                    ]
                },
            ),
        )
        handler = session.handlers["Fetch.requestPaused"]
        with self.assertLogs("giye.collect.robots", level="WARNING"):
            handler(
                {
                    "requestId": "r1",
                    "resourceType": "Document",
                    "request": {
                        "url": "https://example.test/private/x",
                        "headers": {"User-Agent": UA},
                    },
                }
            )
        self.assertIn(
            ("Fetch.failRequest", {"requestId": "r1", "errorReason": "Aborted"}),
            session.sent,
        )
        handler(
            {
                "requestId": "r2",
                "resourceType": "Image",
                "request": {
                    "url": "https://example.test/private/x.png",
                    "headers": {"User-Agent": UA},
                },
            }
        )
        self.assertIn(("Fetch.continueRequest", {"requestId": "r2"}), session.sent)

    def test_install_is_idempotent(self) -> None:
        page = _CdpPage()
        install_document_guard(page)
        install_document_guard(page)
        self.assertEqual(len(page.sessions), 1)


if __name__ == "__main__":
    unittest.main()
