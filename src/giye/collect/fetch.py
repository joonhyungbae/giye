# SPDX-License-Identifier: MIT
"""HTTP fetcher that checks robots.txt before every request.

Ported from ``scripts/archive_evidence.py`` (``allowed``, ``get_tls_lenient``) and the
per-host pause collectors used around it. Decisions kept from production:

- robots.txt is fetched once per ``scheme://host`` and cached. HTTP status >= 400, or a
  connection error while reading robots.txt, is treated as "no robots file" and the URL
  is allowed (production ``allowed()``). RFC 9309 would treat a 5xx as a disallow; that
  difference is recorded in the port report.
- A certificate failure is retried once with verification off. The page result carries
  ``tls_unverified=True``. The robots check itself is unchanged.
- The configured User-Agent is sent on every request, including robots.txt. It must
  contain a contact URL or email so an operator can be reached (production sent
  ``GiyeArchiveBot/0.2 (+https://… )``).

A URL robots.txt disallows raises ``RobotsDisallowed`` and is never requested.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.robotparser import RobotFileParser

import requests


class RobotsDisallowed(Exception):
    """robots.txt disallows this URL. No request for the URL was sent."""

    def __init__(self, url: str):
        self.url = url
        super().__init__(f"robots.txt disallows fetching {url}")


@dataclass
class Page:
    """One fetch. ``url`` is the final URL (after redirects); ``text`` is the decoded body.

    The collector example uses ``page.text`` and ``page.url``.
    """

    url: str
    status: int
    content: bytes
    content_type: str = ""
    tls_unverified: bool = False
    requested_url: str = ""

    def __post_init__(self) -> None:
        if not self.requested_url:
            self.requested_url = self.url

    @property
    def ok(self) -> bool:
        """True for HTTP status below 400, matching ``requests.Response.ok``."""
        return 200 <= self.status < 400

    @property
    def text(self) -> str:
        charset = "utf-8"
        for part in self.content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip(" \"'")
                break
        return self.content.decode(charset, errors="replace")


def require_contact(user_agent: str) -> str:
    """The archive's User-Agent has to carry a contact URL or email address."""
    ua = (user_agent or "").strip()
    if "http://" not in ua and "https://" not in ua and "@" not in ua:
        raise ValueError(
            "user_agent must include a contact URL or email "
            "(for example 'GiyeArchive/0.1 (+https://example.org/contact)')"
        )
    return ua


class Fetcher:
    """GET with a robots.txt check, a per-host delay, timeouts, and one TLS-lenient retry.

    ``offline_roots`` maps a URL prefix to a local directory (the demo). Those reads never
    open a socket, but they still pass through the robots.txt file in that directory.
    """

    def __init__(
        self,
        user_agent: str,
        *,
        min_delay_s: float = 2.0,
        timeout_s: float = 45.0,
        robots_timeout_s: float = 20.0,
        session: requests.Session | None = None,
        offline_roots: tuple[tuple[str, Path], ...] | dict[str, Path] | None = None,
    ) -> None:
        self.user_agent = require_contact(user_agent)
        self.min_delay_s = float(min_delay_s)
        self.timeout_s = float(timeout_s)
        self.robots_timeout_s = float(robots_timeout_s)
        self.session = session or requests.Session()
        roots: list[tuple[str, Path]] = []
        for prefix, dest in dict(offline_roots or ()).items():
            roots.append((str(prefix).rstrip("/"), Path(dest)))
        roots.sort(key=lambda item: len(item[0]), reverse=True)
        self.offline_roots = tuple(roots)
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        # Replaced in tests to avoid sleeping on the wall clock.
        self._now = time.monotonic
        self._sleep = time.sleep

    def get(self, url: str) -> Page:
        """Fetch ``url``. Raises ``RobotsDisallowed`` before any request for a disallowed URL."""
        if not self._allowed(url):
            raise RobotsDisallowed(url)
        offline = self._offline_file(url)
        if offline is not None:
            self._throttle(urlparse(url).netloc)
            self._last[urlparse(url).netloc] = self._now()
            if not offline.is_file():
                return Page(url=url, status=404, content=b"", content_type="text/html", requested_url=url)
            return Page(
                url=url,
                status=200,
                content=offline.read_bytes(),
                content_type=_content_type(offline),
                tls_unverified=False,
                requested_url=url,
            )
        response, unverified = self._raw_get(url, self.timeout_s)
        content_type = ""
        headers = getattr(response, "headers", None) or {}
        if hasattr(headers, "get"):
            content_type = headers.get("Content-Type") or headers.get("content-type") or ""
        body = getattr(response, "content", b"") or b""
        final = getattr(response, "url", None) or url
        return Page(
            url=final,
            status=int(response.status_code),
            content=body if isinstance(body, bytes) else bytes(body),
            content_type=content_type,
            tls_unverified=unverified,
            requested_url=url,
        )

    def _allowed(self, url: str) -> bool:
        parser = self._robots_for(url)
        if parser is None:
            return True
        return bool(parser.can_fetch(self.user_agent, url))

    def _robots_for(self, url: str) -> RobotFileParser | None:
        offline = self._offline_root(url)
        if offline is not None:
            key = "offline:" + offline[0]
            if key not in self._robots:
                self._robots[key] = _parser_from_file(offline[1] / "robots.txt")
            return self._robots[key]
        parts = urlparse(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            self._robots[host] = self._fetch_robots(host)
        return self._robots[host]

    def _fetch_robots(self, host: str) -> RobotFileParser | None:
        """Load ``/robots.txt``. A missing file or an HTTP error allows every URL on the host.

        Production ``allowed()``: status >= 400, or ``RequestException``, stores ``None``
        and ``can_fetch`` is skipped (allowed). The robots.txt request is not itself
        gated on robots.txt.
        """
        try:
            response, _unverified = self._raw_get(f"{host}/robots.txt", self.robots_timeout_s)
        except requests.RequestException:
            return None
        if int(response.status_code) >= 400:
            return None
        parser = RobotFileParser()
        parser.parse(_response_text(response).splitlines())
        return parser

    def _raw_get(self, url: str, timeout: float) -> tuple[requests.Response, bool]:
        """GET, retrying once without TLS verification after ``SSLError``.

        The retry is immediate (production ``get_tls_lenient`` does not pause between
        the two attempts). The per-host delay applies before the first attempt.
        """
        netloc = urlparse(url).netloc
        self._throttle(netloc)
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
        }
        try:
            try:
                response = self.session.get(url, timeout=timeout, headers=headers)
                return response, False
            except requests.exceptions.SSLError:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                response = self.session.get(url, timeout=timeout, headers=headers, verify=False)
                return response, True
        finally:
            self._last[netloc] = self._now()

    def _throttle(self, netloc: str) -> None:
        """Wait so two requests to the same host start at least ``min_delay_s`` apart."""
        if self.min_delay_s <= 0 or not netloc:
            return
        last = self._last.get(netloc)
        if last is None:
            return
        wait = self.min_delay_s - (self._now() - last)
        if wait > 0:
            self._sleep(wait)

    def _offline_root(self, url: str) -> tuple[str, Path] | None:
        for prefix, root in self.offline_roots:
            if url == prefix or url.startswith(prefix + "/"):
                return prefix, root
        return None

    def _offline_file(self, url: str) -> Path | None:
        matched = self._offline_root(url)
        if matched is None:
            return None
        prefix, root = matched
        rest = url[len(prefix):]
        rest = rest.split("?", 1)[0].split("#", 1)[0]
        rel = unquote(rest.lstrip("/"))
        if rel == "" or rel.endswith("/"):
            rel = rel + "index.html"
        exact = _under(root, rel)
        # The collector URL has no file extension (`/residency/alumni`); the fixture may be `alumni.html`.
        if not exact.is_file() and not Path(rel).suffix:
            html = _under(root, rel + ".html")
            if html.is_file():
                return html
        return exact


def _parser_from_file(path: Path) -> RobotFileParser | None:
    """``None`` means allow, matching a missing or unreadable robots.txt in production."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    return parser


def _under(root: Path, rel: str) -> Path:
    parts = Path(rel).parts
    if any(part == ".." for part in parts):
        raise ValueError(f"offline URL escapes the fixture root: {rel}")
    path = (root / rel).resolve()
    root_resolved = root.resolve()
    if path != root_resolved and root_resolved not in path.parents:
        raise ValueError(f"offline URL escapes the fixture root: {rel}")
    return path


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        return "text/html; charset=utf-8"
    if suffix == ".json":
        return "application/json"
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".txt":
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def _response_text(response: requests.Response) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", b"") or b""
    if isinstance(content, str):
        return content
    return content.decode("utf-8", errors="replace")


def fetcher_from_config(config: object) -> Fetcher:
    """Build a ``Fetcher`` from a ``giye.config.Config``."""
    return Fetcher(
        config.user_agent,  # type: ignore[attr-defined]
        min_delay_s=config.min_delay_s,  # type: ignore[attr-defined]
        timeout_s=config.timeout_s,  # type: ignore[attr-defined]
        robots_timeout_s=config.robots_timeout_s,  # type: ignore[attr-defined]
        offline_roots=config.offline_roots,  # type: ignore[attr-defined]
    )
