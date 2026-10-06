# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP fetcher that checks robots.txt before every request and every redirect hop.

Roster collectors, CV pulls, and the evidence keeper share this fetcher, so a
refusal is the same whoever asked (docs/RULES.md, collection policy). The decisions:

- A host whose terms forbid collection (``SOCIAL_HOSTS``) is refused before
  robots.txt is fetched and before the request is sent. Roster collectors, CV
  pulls, and the evidence keeper all use this fetcher, so the block is not
  special to one caller. The refusal is logged and is not a manifest line:
  there are no bytes, the same as a robots refusal.
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
- The configured User-Agent is sent on every request, including robots.txt. It must start
  with the crawler's product token and contain a contact URL with a host or an e-mail
  address (``require_contact``). A contact on a reserved documentation domain such as
  example.org is sent only to reserved or loopback hosts (tests, the demo); a request to
  any other host raises ``ContactError`` before it is sent.
- ``request`` also sends a POST (``post``). Some archives answer a list only to a
  form submission. The same robots.txt and terms checks run before it. The body
  is hashed (``body_sha256``) so the snapshot line and replay can tell two POSTs
  to one URL apart. A 301, 302, or 303 after a POST is followed as a GET with no
  body (what ``requests`` and browsers do); 307 and 308 repeat the POST.
- ``from_snapshots`` answers ``get`` from the snapshot store instead of the network.
  No socket is opened and robots.txt is not fetched: the manifest line already records
  the verdict from the original fetch. A URL that was never kept is a 404 page with
  reason ``not kept``.

A disallowed or unreachable URL is never requested. The public evidence default still does
not fall back to the Internet Archive for that refusal (see ``giye.collect.evidence``).
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urldefrag, urlencode, urlparse

import requests

from giye.collect.robots import (
    MAX_ROBOTS_BYTES,
    REDIRECT_STATUSES,
    VERDICT_DISALLOWED,
    VERDICT_NOT_CHECKED,
    RobotsDisallowed,
    RobotsRefused,
    _location,
    _product_token,
    decide,
)
from giye.collect.snapshot import SnapshotStore
from giye.config import ConfigError

__all__ = [
    "SOCIAL_HOSTS",
    "ContactError",
    "Fetcher",
    "Page",
    "RobotsDisallowed",
    "RobotsRefused",
    "TermsRefused",
    "encode_body",
    "fetcher_from_config",
    "is_social",
    "require_contact",
]

log = logging.getLogger(__name__)

# Used when a session has no max_redirects. 30 is requests' own default.
_PAGE_REDIRECT_LIMIT = 30

# Hosts whose terms forbid automated collection. Refused in ``Fetcher.get``
# before robots.txt, for every caller (docs/RULES.md, collection policy).
SOCIAL_HOSTS = (
    "instagram.com",
    "facebook.com",
    "linkedin.com",
    "x.com",
    "twitter.com",
    "threads.net",
    "threads.com",
    "tiktok.com",
    # Short-link and alternate hosts of the same platforms. A redirect from
    # them would be refused at the next hop, but the first request would
    # already reach the platform's own host.
    "instagr.am",
    "fb.com",
    "fb.me",
    "fb.watch",
    "t.co",
    "lnkd.in",
)

# Same verdict string the evidence status uses. It is not a robots.txt verdict.
TERMS_VERDICT = "platform_excluded"


class TermsRefused(Exception):
    """A host whose terms forbid collection. The request was not sent.

    robots.txt is not fetched either. Callers record this the way they record
    a robots refusal: the log line is the record, and no snapshot bytes are
    written. Evidence keeps the status ``platform_excluded``.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.verdict = TERMS_VERDICT
        super().__init__(f"not fetched ({TERMS_VERDICT}): {url}")
        log.warning("refusing fetch (%s): %s", self.verdict, url)


def is_social(url: str) -> bool:
    """True when ``url``'s host is a platform whose terms forbid collection.

    The match is the hostname, so a port or userinfo cannot bypass it. A
    lookalike such as ``instagram.com.example.org`` does not match.
    """
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in SOCIAL_HOSTS)


@dataclass
class Page:
    """One fetch. ``url`` is the final URL (after redirects); ``text`` is the decoded body.

    ``requested_url`` is the URL the caller asked for, before redirects. ``robots`` is the
    verdict for the hop whose bytes are in ``content`` (the last hop). A refused hop never
    becomes a ``Page``: the fetcher raises before sending it.

    ``fetched_at`` is the manifest timestamp when this page was read back from the
    snapshot store. ``reason`` is ``not kept`` when the store has no body for the URL.

    ``method`` is the request method the caller used (``GET`` or ``POST``) and
    ``body_sha256`` the SHA-256 of the request body it sent; empty for a GET.
    Together with ``requested_url`` they identify the request on a snapshot line.
    """

    url: str
    status: int
    content: bytes
    content_type: str = ""
    tls_unverified: bool = False
    requested_url: str = ""
    robots: str = VERDICT_NOT_CHECKED
    robots_tls_unverified: bool = False
    fetched_at: str = ""
    reason: str = ""
    method: str = "GET"
    body_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.requested_url:
            self.requested_url = self.url

    @property
    def ok(self) -> bool:
        """True for HTTP status below 400, matching ``requests.Response.ok``."""
        return 200 <= self.status < 400

    @property
    def text(self) -> str:
        """Body decoded with the charset in ``Content-Type``, or UTF-8. Bad bytes are replaced."""
        charset = "utf-8"
        for part in self.content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip(" \"'")
                break
        return self.content.decode(charset, errors="replace")


def encode_body(data: object) -> tuple[bytes, str]:
    """Request body bytes and the Content-Type to send with them.

    A mapping (or a sequence of pairs) is form-encoded in the order given, the
    way ``requests`` encodes ``data=``; the hash is taken over these bytes, so
    the same form gives the same hash. ``str`` is UTF-8. ``bytes`` is sent as
    is with no Content-Type, because the caller knows what they hold.
    """
    if data is None:
        return b"", ""
    if isinstance(data, bytes):
        return data, ""
    if isinstance(data, str):
        return data.encode("utf-8"), ""
    if isinstance(data, Mapping):
        pairs = list(data.items())
    else:
        pairs = list(data)  # type: ignore[call-overload]
    return urlencode(pairs, doseq=True).encode("ascii"), "application/x-www-form-urlencoded"


def body_hash(body: bytes, method: str) -> str:
    """SHA-256 of a request body. A GET with no body has no hash, so its lines stay unchanged."""
    if method == "GET" and not body:
        return ""
    return hashlib.sha256(body).hexdigest()


class ContactError(ConfigError, ValueError):
    """The configured User-Agent cannot identify the archive to a site operator.

    A ``ConfigError`` so the command prints one line, and a ``ValueError`` for
    callers that caught the earlier exception type.
    """


# A contact URL with a host, or an e-mail address (``mailto:`` included).
_CONTACT_URL = re.compile(r"https?://([^/\s;()<>,]+)", re.IGNORECASE)
_CONTACT_MAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")

# Hosts reserved for documentation and testing (RFC 2606, RFC 6761). A contact
# there reaches nobody, so it identifies no one to a real site's operator.
_RESERVED_SUFFIXES = ("example", "test", "invalid", "localhost")
_RESERVED_DOMAINS = ("example.org", "example.com", "example.net")


def _is_reserved_host(host: str) -> bool:
    """True for RFC 2606/6761 names and loopback addresses."""
    host = (host or "").lower().rstrip(".").split("@")[-1]
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    else:
        host = host.split(":", 1)[0]
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if any(host == name or host.endswith("." + name) for name in _RESERVED_DOMAINS):
        return True
    return host.rsplit(".", 1)[-1] in _RESERVED_SUFFIXES


def _contact_hosts(ua: str) -> list[str]:
    """Hosts of every contact in ``ua``: URL hosts and e-mail domains."""
    hosts = [match.group(1) for match in _CONTACT_URL.finditer(ua)]
    hosts.extend(match.group(1) for match in _CONTACT_MAIL.finditer(ua))
    found = []
    for host in hosts:
        name = host.split("@")[-1].split(":", 1)[0].lower().rstrip(".")
        # A host needs a dot (a domain) or must be localhost.
        if "." in name or name == "localhost":
            found.append(name)
    return found


def require_contact(user_agent: str) -> str:
    """The archive's User-Agent has to name the crawler and carry a working contact.

    Rules (docs/CONFIG.md, ``[collect] user_agent``):

    - a contact is an http(s) URL whose host is a domain, or an e-mail address
      ``local@domain.tld``. A bare ``@`` or ``http://`` is not a contact;
    - the product token (the leading name, RFC 9309 §2.2.1) is what robots.txt
      groups are matched on, so a browser-style ``Mozilla/5.0 (...)`` string is
      refused: a site's rules for the crawler's own name would not apply.

    A contact on a reserved documentation domain (example.org and the like) is
    accepted here, so tests and the demo can build a fetcher, but
    ``Fetcher`` refuses to send it to any real host (see ``check_contact_for``).
    """
    ua = (user_agent or "").strip()
    if not ua:
        raise ContactError(
            "[collect] user_agent is required before anything is fetched: name the crawler and give "
            "a contact URL or e-mail address (for example 'MyArchiveBot/0.1 (+https://your.site/contact)')"
        )
    if not _contact_hosts(ua):
        raise ContactError(
            "user_agent must include a contact URL with a host or an e-mail address "
            "(for example 'MyArchiveBot/0.1 (+https://your.site/contact)')"
        )
    if _product_token(ua).lower() == "mozilla":
        raise ContactError(
            "user_agent must start with the crawler's own product token, not 'Mozilla': "
            "robots.txt groups are chosen by that first token (RFC 9309 §2.2.1)"
        )
    return ua


def placeholder_contact(user_agent: str) -> bool:
    """True when every contact in ``user_agent`` is on a reserved documentation or test host."""
    hosts = _contact_hosts(user_agent or "")
    return bool(hosts) and all(_is_reserved_host(host) for host in hosts)


def check_contact_for(user_agent: str, url: str) -> None:
    """Refuse to send a placeholder contact to a real host.

    A contact on example.org (or another reserved name) reaches nobody, so a
    request carrying it does not identify the archive. It may still go to a
    reserved or loopback host (the tests' local servers and fakes), which
    no third party operates.
    """
    if placeholder_contact(user_agent) and not _is_reserved_host(urlparse(url).netloc):
        raise ContactError(
            f"user_agent {user_agent!r} gives only a placeholder contact (a reserved documentation domain); "
            f"set [collect] user_agent to a contact you read before fetching {url}"
        )


class Fetcher:
    """GET with a robots.txt check on every hop, a per-host delay, timeouts, and one TLS retry.

    ``offline_roots`` maps a URL prefix to a local directory (the demo). Those reads never
    open a socket, but they still pass through the robots.txt file in that directory.

    ``from_snapshots`` reads ``snapshot_root/*/snapshots/manifest.jsonl`` instead.
    ``prefer_frame`` is the collector's frame: when that frame also kept the URL,
    its copy is used. robots.txt is not fetched in this mode.
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
        from_snapshots: bool = False,
        snapshot_root: Path | None = None,
        prefer_frame: str = "",
    ) -> None:
        self.user_agent = require_contact(user_agent)
        self.min_delay_s = float(min_delay_s)
        self.timeout_s = float(timeout_s)
        self.robots_timeout_s = float(robots_timeout_s)
        self.from_snapshots = bool(from_snapshots)
        self.snapshot_root = None if snapshot_root is None else Path(snapshot_root)
        self.prefer_frame = prefer_frame or ""
        self._reads: SnapshotStore | None = None
        # Replay must not be able to fall through into a live session.
        if self.from_snapshots:
            self.session = session
        else:
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

        In ``from_snapshots`` mode the answer is the newest kept body for ``url``
        (see ``SnapshotStore.recall``). Nothing is sent, and robots.txt is not read.
        """
        return self.request(url)

    def post(self, url: str, data: object = None) -> Page:
        """POST ``data`` to ``url`` under the same checks as ``get``. See ``request``."""
        return self.request(url, method="POST", data=data)

    def request(self, url: str, *, method: str = "GET", data: object = None) -> Page:
        """``GET`` or ``POST`` one URL. Robots.txt and the terms block apply to every hop.

        ``data`` is the request body (see ``encode_body``). A GET with a body is
        refused: a query belongs in the URL, and a GET line in the manifest has
        no body hash. In ``from_snapshots`` mode the kept line must match the
        method and the body hash as well as the URL.
        """
        method = (method or "GET").upper()
        if method not in {"GET", "POST"}:
            raise ValueError(f"unsupported request method: {method}")
        body, body_type = encode_body(data)
        if method == "GET" and data is not None:
            raise ValueError("a GET request takes no body; put the query in the URL")
        digest = body_hash(body, method)
        if self.from_snapshots:
            return self._from_store(url, method=method, body_sha256=digest)
        page = self._live(url, method=method, body=body, body_type=body_type)
        page.method = method
        page.body_sha256 = digest
        return page

    def _live(self, url: str, *, method: str, body: bytes, body_type: str) -> Page:
        """Send the request and follow redirects one hop at a time, checking each hop first."""
        original = url
        current = urldefrag(str(url))[0]
        limit = getattr(self.session, "max_redirects", None)
        if limit is None:
            limit = _PAGE_REDIRECT_LIMIT
        limit = int(limit)
        tls_unverified = False
        robots_tls = False

        for followed in range(limit + 1):
            # Terms of service before robots.txt, on every hop including a redirect.
            if is_social(current):
                raise TermsRefused(current)
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
            response, unverified = self._raw_request(current, self.timeout_s, method, body, body_type)
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
            # 301/302/303 after a POST continue as a GET without the body.
            if method != "GET" and status in (301, 302, 303):
                method, body, body_type = "GET", b"", ""
        raise requests.exceptions.TooManyRedirects(f"exceeded {limit} redirects: {current}")

    def _from_store(self, url: str, *, method: str = "GET", body_sha256: str = "") -> Page:
        """One kept body, or a 404 page whose ``reason`` is ``not kept``.

        The session, ``offline_roots``, and ``decide`` are not used. A mistaken
        call into the live path cannot open a socket: this mode stores no session
        unless the caller passed one, and this method does not touch it.
        """
        store = self._read_store()
        if store is None:
            return _not_kept(url)
        kept = store.recall(str(url), prefer_frame=self.prefer_frame, method=method, body_sha256=body_sha256)
        if kept is None:
            page = _not_kept(url)
            page.method, page.body_sha256 = method, body_sha256
            return page
        final = kept.final_url or kept.url or str(url)
        return Page(
            url=final,
            status=kept.status,
            content=kept.content,
            content_type=kept.content_type,
            tls_unverified=kept.tls_unverified,
            requested_url=str(url),
            robots=kept.robots or VERDICT_NOT_CHECKED,
            robots_tls_unverified=kept.robots_tls_unverified,
            fetched_at=kept.fetched_at,
            method=method,
            body_sha256=body_sha256,
        )

    def _read_store(self) -> SnapshotStore | None:
        if self.snapshot_root is None:
            return None
        if self._reads is None:
            self._reads = SnapshotStore(self.snapshot_root)
        return self._reads

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
        """GET one hop. Redirects stay off so the caller can check robots.txt on the next URL."""
        return self._raw_request(url, timeout, "GET", b"", "")

    def _raw_request(
        self, url: str, timeout: float, method: str, body: bytes, body_type: str
    ) -> tuple[object, bool]:
        """Send one hop. Redirects stay off so the caller can check robots.txt on the next URL.

        A GET goes through ``session.get`` and a POST through ``session.post``.

        The retry after ``SSLError`` is immediate. The per-host delay already ran
        before the first attempt; the retry is the same request after a certificate
        failure, not a new visit. ``allow_redirects`` is always false.
        """
        check_contact_for(self.user_agent, url)
        netloc = urlparse(url).netloc
        self._throttle(netloc)
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
        }
        if body_type:
            headers["Content-Type"] = body_type
        send = self.session.get
        extra: dict[str, object] = {}
        if method == "POST":
            send = self.session.post
            extra["data"] = body
        try:
            try:
                response = send(url, timeout=timeout, headers=headers, allow_redirects=False, **extra)
                return response, False
            except requests.exceptions.SSLError:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                try:
                    response = send(
                        url, timeout=timeout, headers=headers, allow_redirects=False, verify=False, **extra
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

    def serves_offline(self, url: str) -> bool:
        """True when ``url`` is under one of ``offline_roots`` (read from disk, no socket)."""
        return self._offline_root(url) is not None

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


def _not_kept(url: str) -> Page:
    """A URL the snapshot store has no servable body for. No request was made."""
    return Page(url=url, status=404, content=b"", requested_url=url, reason="not kept")


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


def _close(response: object) -> None:
    """Close a response when it has ``close``. A fixture page does not."""
    close = getattr(response, "close", None)
    if callable(close):
        close()


def _under(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``. A path that climbs out is refused."""
    parts = Path(rel).parts
    if any(part == ".." for part in parts):
        raise ValueError(f"offline URL escapes the fixture root: {rel}")
    path = (root / rel).resolve()
    root_resolved = root.resolve()
    if path != root_resolved and root_resolved not in path.parents:
        raise ValueError(f"offline URL escapes the fixture root: {rel}")
    return path


def _content_type(path: Path) -> str:
    """Content-Type for an offline fixture, from the file suffix."""
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


def fetcher_from_config(config: object, *, from_snapshots: bool = False) -> Fetcher:
    """Build a ``Fetcher`` from a ``giye.config.Config``.

    ``from_snapshots`` reads ``config.raw`` and does not use the network.
    """
    raw = getattr(config, "raw", None)
    return Fetcher(
        config.user_agent,  # type: ignore[attr-defined]
        min_delay_s=config.min_delay_s,  # type: ignore[attr-defined]
        timeout_s=config.timeout_s,  # type: ignore[attr-defined]
        robots_timeout_s=config.robots_timeout_s,  # type: ignore[attr-defined]
        offline_roots=config.offline_roots,  # type: ignore[attr-defined]
        from_snapshots=from_snapshots,
        snapshot_root=Path(raw) if from_snapshots and raw is not None else None,
    )
