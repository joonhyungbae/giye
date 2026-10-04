# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP fetcher that checks robots.txt before every request and every redirect hop.

Ported from ``scripts/collectors/robots.py`` (``decide``, ``guarded_request``) and the
per-host pause collectors used around the evidence keeper. Decisions kept from production:

- robots.txt is judged by RFC 9309 fetch status, not by ``urllib.robotparser``. HTTP 2xx is
  parsed. HTTP 4xx, including 404 and 403, is ``unavailable_allowed`` (the URL may be fetched).
  HTTP 5xx, a timeout, or a network error is ``unreachable_disallowed`` for this process only:
  nothing is written to disk, and a later process fetches robots.txt again.
- Allow and Disallow use the RFC 9309 §2.2.2 longest match. Group selection uses the product
  token (§2.2.1). The path ``/robots.txt`` is always allowed.
- A page fetch does not follow redirects automatically. ``decide`` runs before every hop,
  including the first. A hop that is not permitted raises ``RobotsDisallowed`` or
  ``RobotsRefused`` and is not sent. The ceiling is the session's ``max_redirects`` (30 when
  the session has none), the same ceiling ``requests`` already used. It is not the five-redirect
  limit that applies only to robots.txt itself.
- A certificate failure is retried once with verification off. The page result carries
  ``tls_unverified``. When the robots.txt fetch needed that retry, ``robots_tls_unverified``
  is set as well. The path verdict does not depend on the flag.
- The configured User-Agent is sent on every request, including robots.txt. It must contain
  a contact URL or email.

A disallowed or unreachable URL is never requested. The public evidence default still does
not fall back to the Internet Archive for that refusal (see ``giye.collect.evidence``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urldefrag, urlparse

import requests

from giye.collect.robots import (
    MAX_ROBOTS_BYTES,
    REDIRECT_STATUSES,
    VERDICT_DISALLOWED,
    VERDICT_NOT_CHECKED,
    RobotsDisallowed,
    RobotsRefused,
    _location,
    decide,
)

__all__ = [
    "Fetcher",
    "Page",
    "RobotsDisallowed",
    "RobotsRefused",
    "fetcher_from_config",
    "require_contact",
]

# Used when a session has no max_redirects. 30 is requests' own default.
_PAGE_REDIRECT_LIMIT = 30


@dataclass
class Page:
    """One fetch. ``url`` is the final URL (after redirects); ``text`` is the decoded body.

    ``requested_url`` is the URL the caller asked for, before redirects. ``robots`` is the
    verdict for the hop whose bytes are in ``content`` (the last hop). A refused hop never
    becomes a ``Page``: the fetcher raises before sending it.
    """

    url: str
    status: int
    content: bytes
    content_type: str = ""
    tls_unverified: bool = False
    requested_url: str = ""
    robots: str = VERDICT_NOT_CHECKED
    robots_tls_unverified: bool = False

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
    """GET with a robots.txt check on every hop, a per-host delay, timeouts, and one TLS retry.

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
        self._last: dict[str, float] = {}
        # Replaced in tests to avoid sleeping on the wall clock.
        self._now = time.monotonic
        self._sleep = time.sleep

    def get(self, url: str) -> Page:
        """Fetch ``url``. A disallowed or unreachable hop raises before that hop is sent.

        Redirects are followed one hop at a time. Each hop is passed to ``decide`` first.
        """
        original = url
        current = urldefrag(str(url))[0]
        limit = getattr(self.session, "max_redirects", None)
        if limit is None:
            limit = _PAGE_REDIRECT_LIMIT
        limit = int(limit)
        tls_unverified = False
        robots_tls = False

        for followed in range(limit + 1):
            decision = decide(
                current,
                self.user_agent,
                timeout=self.robots_timeout_s,
                get=self._robots_get,
                use_cache=True,
            )
            robots_tls = robots_tls or decision.tls_unverified
            if not decision.permits:
                if decision.verdict == VERDICT_DISALLOWED:
                    raise RobotsDisallowed(current, decision.verdict)
                raise RobotsRefused(current, decision.verdict)
            if self._offline_root(current) is not None:
                return self._offline_page(current, original, decision.verdict, robots_tls)
            response, unverified = self._raw_get(current, self.timeout_s)
            tls_unverified = tls_unverified or unverified
            status = int(response.status_code)
            if status not in REDIRECT_STATUSES:
                return self._page_from_response(
                    response,
                    original=original,
                    current=current,
                    robots=decision.verdict,
                    tls_unverified=tls_unverified,
                    robots_tls=robots_tls,
                )
            if followed == limit:
                _close(response)
                raise requests.exceptions.TooManyRedirects(f"exceeded {limit} redirects: {current}")
            nxt = _location(response, current)
            _close(response)
            if not nxt:
                raise requests.exceptions.InvalidURL(f"redirect without a usable Location: {current}")
            current = urldefrag(nxt)[0]
        raise requests.exceptions.TooManyRedirects(f"exceeded {limit} redirects: {current}")

    def _robots_get(self, url: str, timeout: float):
        """One robots.txt hop for ``decide``. Redirects are not followed here.

        An offline prefix is read from that directory. A missing file is a 404, which
        RFC 9309 treats as unavailable (allowed). A TLS failure is retried once; the
        response or the exception then carries ``tls_unverified``.
        """
        offline = self._offline_root(url)
        if offline is not None:
            return _offline_robots(offline[1], url)
        response, unverified = self._raw_get(url, timeout)
        # decide() reads this flag. A requests response accepts the attribute.
        response.tls_unverified = unverified or bool(getattr(response, "tls_unverified", False))
        return response

    def _page_from_response(
        self,
        response,
        *,
        original: str,
        current: str,
        robots: str,
        tls_unverified: bool,
        robots_tls: bool,
    ) -> Page:
        headers = getattr(response, "headers", None) or {}
        content_type = ""
        if hasattr(headers, "get"):
            content_type = headers.get("Content-Type") or headers.get("content-type") or ""
        body = getattr(response, "content", b"") or b""
        if isinstance(body, str):
            body = body.encode("utf-8")
        final = getattr(response, "url", None) or current
        return Page(
            url=final,
            status=int(response.status_code),
            content=body if isinstance(body, bytes) else bytes(body),
            content_type=content_type,
            tls_unverified=tls_unverified,
            requested_url=original,
            robots=robots,
            robots_tls_unverified=robots_tls,
        )

    def _offline_page(self, url: str, original: str, robots: str, robots_tls: bool) -> Page:
        netloc = urlparse(url).netloc
        self._throttle(netloc)
        self._last[netloc] = self._now()
        offline = self._offline_file(url)
        if offline is None or not offline.is_file():
            return Page(
                url=url,
                status=404,
                content=b"",
                content_type="text/html",
                requested_url=original,
                robots=robots,
                robots_tls_unverified=robots_tls,
            )
        return Page(
            url=url,
            status=200,
            content=offline.read_bytes(),
            content_type=_content_type(offline),
            tls_unverified=False,
            requested_url=original,
            robots=robots,
            robots_tls_unverified=robots_tls,
        )

    def _raw_get(self, url: str, timeout: float) -> tuple[object, bool]:
        """GET one hop. Redirects stay off so the caller can check robots.txt on the next URL.

        The retry after ``SSLError`` is immediate (production did not pause between the two
        attempts). The per-host delay applies before the first attempt. ``allow_redirects``
        is always false.
        """
        netloc = urlparse(url).netloc
        self._throttle(netloc)
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
        }
        try:
            try:
                response = self.session.get(
                    url, timeout=timeout, headers=headers, allow_redirects=False
                )
                return response, False
            except requests.exceptions.SSLError:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                try:
                    response = self.session.get(
                        url, timeout=timeout, headers=headers, allow_redirects=False, verify=False
                    )
                except requests.RequestException as exc:
                    exc.tls_unverified = True  # type: ignore[attr-defined]
                    raise
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


def _offline_robots(root: Path, url: str):
    """A robots.txt response from a fixture directory. Missing file → 404 (unavailable, allowed)."""
    path = root / "robots.txt"
    if not path.is_file():
        return SimpleNamespace(
            status_code=404, headers={}, content=b"", text="", url=url, tls_unverified=False
        )
    content = path.read_bytes()[:MAX_ROBOTS_BYTES]
    return SimpleNamespace(
        status_code=200,
        headers={"Content-Type": "text/plain; charset=utf-8"},
        content=content,
        text=content.decode("utf-8", errors="replace"),
        url=url,
        tls_unverified=False,
    )


def _close(response) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()


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


def fetcher_from_config(config: object) -> Fetcher:
    """Build a ``Fetcher`` from a ``giye.config.Config``."""
    return Fetcher(
        config.user_agent,  # type: ignore[attr-defined]
        min_delay_s=config.min_delay_s,  # type: ignore[attr-defined]
        timeout_s=config.timeout_s,  # type: ignore[attr-defined]
        robots_timeout_s=config.robots_timeout_s,  # type: ignore[attr-defined]
        offline_roots=config.offline_roots,  # type: ignore[attr-defined]
    )
