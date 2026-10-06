# SPDX-License-Identifier: AGPL-3.0-only
"""Decide whether this run may fetch a URL, from that origin's robots.txt.

Path matching is this module, not ``urllib.robotparser``: RFC 9309 §2.2.2
picks the longest match.

RFC 9309 §2.3.1.1: an HTTP 2xx response is a successful download. The body is
parsed and its rules are followed.

RFC 9309 §2.3.1.2: follow at least five consecutive redirects, including to
another host. The rules apply to the origin we first asked (the page's
origin), not to the host that happened to serve the file. A sixth redirect
is more than five consecutive redirects. The file is then unavailable, and
the URL may be fetched. That is the RFC's "MAY assume unavailable", and it
is the choice this archive keeps. It is not treated
as unreachable.

RFC 9309 §2.3.1.3: a 4xx response, including 404, means robots.txt is
unavailable. The crawler may access any resource on that origin
("unavailable_allowed").

RFC 9309 §2.3.1.4: a 5xx response, a network error, or a timeout means
robots.txt is unreachable. The crawler must assume complete disallow for this
run ("unreachable_disallowed"). The outcome is remembered only in this process.
It is not written to disk, and a later run fetches robots.txt again.

Path matching replaces urllib.robotparser. Group selection follows
§2.2.1: the product token is the leading run of ASCII letters, hyphens, and
underscores in the User-Agent string. A robots.txt group matches when one of
its user-agent product tokens equals that token, case-insensitively. Every
matching group is merged into one group of rules. That group is the most
specific one. A different token, including a proper prefix such as "Giye"
for "GiyeArchiveBot", does not match. If no group names the token, the groups
named "*" are merged and used. If there is no "*" group either, no rule
applies and the URL is allowed.

Allow and Disallow follow §2.2.2 and §2.2.3. The match string is the URL path
plus "?" and the query when a query is present; the fragment is dropped.
Matching is case-sensitive and starts at the first octet. "*" is any run of
characters, including the empty run. "$" is the end of the path only when it
is the last character of the rule; a "$" earlier in the rule is a literal
dollar and is percent-encoded so it matches a dollar in the URL. The longest
matching rule wins, measured in octets of the normalised pattern (including
"*" and a trailing "$"). An allow and a disallow of equal length: allow wins.
An empty Allow or Disallow value has no octets, matches nothing, and leaves
the URL allowed when no other rule matches. Rules that appear before the
first user-agent line are ignored. The path "/robots.txt" is always allowed.

Percent-encoding follows §2.2.2. Both the rule and the URL are normalised
before comparison: percent-encoded unreserved octets (RFC 3986: ALPHA, DIGIT,
"-", ".", "_", "~") are decoded; other percent-escapes stay encoded with
uppercase hex; non-ASCII octets are encoded as UTF-8 percent-escapes; reserved
and other ASCII characters are encoded, except the structural characters "/"
in the path and "?", "=", "&" in the query. In a rule, "*" and a trailing "$"
are syntax and are not encoded. A literal asterisk in the URL is encoded to
"%2A" so a rule written "%2A" matches it (§2.2.3).

A TLS verification failure is retried once without verification. The evidence
keeper already did that, so an expired or mismatched certificate is not treated
as a missing file. The retry is not an access rule: the status and the path
rules are unchanged. Verdict.tls_unverified records that the retry was used.
If the retry also fails, the outcome is unreachable and the flag is still set.

The body is parsed up to 500 KiB (RFC 9309 §2.5: the parsing limit must be at
least 500 kibibytes) and decoded as UTF-8.

A page fetch goes through `guarded_request`. Automatic redirects are off.
Each hop, including the first, is passed to `decide()` before it is sent.
A hop that is not permitted raises `RobotsRefused` and is not requested.
The exception is logged, and that log line is the record of the refusal.
The redirect ceiling is the session's `max_redirects` (requests' default is
30): the same ceiling requests already applied to a page, so a chain that
used to finish still finishes when every hop is allowed. It is not the
five-redirect rule for robots.txt above.

The public fetcher calls ``decide`` before every hop, including redirects, and
does not send a hop whose verdict does not permit it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

log = logging.getLogger(__name__)

# Five redirects followed, then one more response. A further redirect is
# "more than five" and is unavailable (RFC 9309 §2.3.1.2), which this archive
# treats as allowed. See the module docstring. This limit is only for
# robots.txt. A page fetch uses the session's max_redirects instead.
MAX_REDIRECTS = 5
# Used when a session has no max_redirects. 30 is requests' own default.
PAGE_REDIRECT_LIMIT = 30
MAX_ROBOTS_BYTES = 500 * 1024
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

VERDICT_ALLOWED = "allowed"
VERDICT_UNAVAILABLE = "unavailable_allowed"
VERDICT_DISALLOWED = "disallowed"
VERDICT_UNREACHABLE = "unreachable_disallowed"
VERDICT_NOT_CHECKED = "not_checked"

_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_HEX = frozenset("0123456789abcdefABCDEF")

# (origin, user-agent) → rules for this process. Unreachable stays a disallow
# until the process ends; the next run tries again.
_cache: dict[tuple[str, str], OriginRules] = {}


class RobotsRefused(Exception):
    """A live fetch was not sent: robots.txt disallows it, or could not be reached.

    This is not a transport error. Callers that retry requests.RequestException
    must not treat it as a blip and try the same URL again: the request was
    refused before it was sent.
    """

    def __init__(self, url: str, verdict: str, reason: str = "") -> None:
        self.url = url
        self.verdict = verdict
        # Why robots.txt was unreachable: "network" (DNS or connection failure:
        # the host is gone or down), "timeout", "status" (HTTP 5xx), or empty.
        self.reason = reason
        super().__init__(f"not fetched ({verdict}): {url}")
        log.warning("refusing fetch (%s): %s", verdict, url)


class RobotsDisallowed(RobotsRefused):
    """A parsed robots.txt disallows this URL. The request was not sent.

    Unreachable robots.txt (HTTP 5xx, timeout, network error) raises
    ``RobotsRefused`` with verdict ``unreachable_disallowed`` instead: the file
    was not parsed. Callers that only catch this subclass still see a parsed
    disallow, which is what ``giye.extract`` records as ``RobotsDisallowed``.
    """

    def __init__(self, url: str, verdict: str = "disallowed") -> None:
        super().__init__(url, verdict)


@dataclass(frozen=True)
class Verdict:
    """One URL's robots outcome for this run."""

    verdict: str
    permits: bool
    http_status: int | None = None
    robots_url: str = ""
    error: str = ""
    # True when robots.txt itself was fetched only after TLS verification was
    # turned off. The path verdict does not depend on this flag.
    tls_unverified: bool = False


@dataclass(frozen=True)
class _Rule:
    allow: bool
    pattern: str


@dataclass(frozen=True)
class _Group:
    tokens: tuple[str, ...]
    rules: tuple[_Rule, ...]


@dataclass
class OriginRules:
    """robots.txt outcome cached for one origin in this process."""

    kind: str  # parsed | unavailable | unreachable
    rules: tuple[_Rule, ...] | None
    http_status: int | None
    robots_url: str
    error: str
    tls_unverified: bool = False


def clear_cache() -> None:
    """Drop this process's robots outcomes. Tests use this; a normal run does not."""
    _cache.clear()


def origin_of(url: str) -> str | None:
    """scheme://host[:port] for an http(s) URL, or None when there is nothing to ask."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def decide(
    url: str,
    user_agent: str,
    *,
    timeout: float = 20,
    get=None,
    session=None,
    use_cache: bool = True,
) -> Verdict:
    """Return the robots verdict for `url` under `user_agent`.

    `get(url, timeout)` may replace the HTTP fetch (tests pass a fake). When it
    is omitted, robots.txt is fetched with redirects disabled so this function
    can stop at five hops. The fetch of robots.txt itself does not go through
    a caller's session wrapper: pass a plain session, not one that calls
    decide() again.
    """
    origin = origin_of(url)
    if origin is None:
        return Verdict(VERDICT_UNREACHABLE, False, error="bad_url")
    key = (origin, user_agent)
    rules = _cache.get(key) if use_cache else None
    if rules is None:
        rules = _fetch_rules(origin, user_agent, timeout=timeout, get=get, session=session)
        if use_cache:
            _cache[key] = rules
    if rules.kind == "unavailable":
        return Verdict(
            VERDICT_UNAVAILABLE, True, rules.http_status, rules.robots_url, rules.error, rules.tls_unverified
        )
    if rules.kind == "unreachable":
        return Verdict(
            VERDICT_UNREACHABLE, False, rules.http_status, rules.robots_url, rules.error, rules.tls_unverified
        )
    permitted = _allowed_by_rules(rules.rules or (), url)
    verdict = VERDICT_ALLOWED if permitted else VERDICT_DISALLOWED
    return Verdict(verdict, permitted, rules.http_status, rules.robots_url, "", rules.tls_unverified)


@dataclass(frozen=True)
class CheckedFetch:
    """The response that was kept, and the robots verdict of that hop.

    `verdict` is the outcome for the URL that returned this response (the
    last hop). `tls_unverified` is true when any hop's robots.txt needed the
    certificate retry. A refused hop is not represented here: it raises
    `RobotsRefused` before the request is sent.
    """

    response: object
    verdict: str
    tls_unverified: bool = False


def _caller_user_agent(session, headers) -> str:
    """User-Agent the page request will send, so decide() uses the same token."""
    if headers:
        override = headers.get("User-Agent") or headers.get("user-agent")
        if override:
            return str(override)
    return str(getattr(session, "headers", {}).get("User-Agent") or "")


def _method_after_redirect(method: str, status: int) -> str:
    """Match requests: 302 and 303 become GET (except HEAD); 301 turns POST into GET.

    307 and 308 keep the method and the body. requests does this so a browser
    and this archive follow the same hop.
    """
    method = method.upper()
    if status in (302, 303) and method != "HEAD":
        return "GET"
    if status == 301 and method == "POST":
        return "GET"
    return method


def _without_body(kwargs: dict, method: str) -> dict:
    """Drop the body once the hop is a GET. params belong to the first URL only."""
    hop = dict(kwargs)
    hop.pop("params", None)
    if method != "GET":
        return hop
    hop.pop("data", None)
    hop.pop("json", None)
    hop.pop("files", None)
    headers = hop.get("headers")
    if headers:
        headers = dict(headers)
        for name in ("Content-Length", "content-length", "Content-Type", "content-type"):
            headers.pop(name, None)
        hop["headers"] = headers
    return hop


def _close_response(response: object) -> None:
    """Close a robots.txt response when it has ``close``."""
    close = getattr(response, "close", None)
    if callable(close):
        close()


def guarded_request(session: Any, method: str, url: str, *args: Any, **kwargs: Any) -> CheckedFetch:
    """Send one request, following redirects by hand.

    `decide()` runs before every hop, including the first. A hop that is not
    permitted raises `RobotsRefused` (logged) and is not sent. `allow_redirects`
    from the caller is ignored: this function always follows manually, up to
    the session's `max_redirects`.

    The call uses `requests.Session.request` on `session`, not `session.request`,
    so a subclass that overrides `request` and calls this function does not recurse.
    robots.txt is fetched by `decide()` on its own session, not on `session`.
    """
    import requests

    kwargs.pop("allow_redirects", None)
    ua = _caller_user_agent(session, kwargs.get("headers"))
    limit = getattr(session, "max_redirects", None)
    if limit is None:
        limit = PAGE_REDIRECT_LIMIT
    limit = int(limit)
    current = urldefrag(str(url))[0]
    method_now = method
    kwargs_now = dict(kwargs)
    args_now = args
    tls_unverified = False

    for followed in range(limit + 1):
        decision = decide(current, ua)
        tls_unverified = tls_unverified or decision.tls_unverified
        if not decision.permits:
            raise RobotsRefused(current, decision.verdict, decision.error)
        response = requests.Session.request(
            session, method_now, current, *args_now, allow_redirects=False, **kwargs_now
        )
        status = int(response.status_code)
        if status not in REDIRECT_STATUSES:
            return CheckedFetch(response, decision.verdict, tls_unverified)
        if followed == limit:
            _close_response(response)
            raise requests.exceptions.TooManyRedirects(
                f"exceeded {limit} redirects: {current}"
            )
        nxt = _location(response, current)
        _close_response(response)
        if not nxt:
            raise requests.exceptions.InvalidURL(f"redirect without a usable Location: {current}")
        current = urldefrag(nxt)[0]
        method_now = _method_after_redirect(method_now, status)
        kwargs_now = _without_body(kwargs_now, method_now)
        args_now = ()


class _CdpDocumentRoute:
    """One paused Chrome document request, with abort/continue_ like a Playwright route."""

    def __init__(self, session, params: dict) -> None:
        request = params.get("request") or {}
        self.request = SimpleNamespace(
            url=str(request.get("url") or ""),
            resource_type=str(params.get("resourceType") or ""),
            headers=request.get("headers") or {},
        )
        self._session = session
        self._id = params.get("requestId")
        self.settled = False

    def abort(self) -> None:
        """Fail the paused request so Chromium does not send it. A second call does nothing."""
        if self.settled:
            return
        self.settled = True
        self._session.send(
            "Fetch.failRequest",
            {"requestId": self._id, "errorReason": "Aborted"},
        )

    def continue_(self) -> None:
        """Let Chromium send the paused request. A second call does nothing."""
        if self.settled:
            return
        self.settled = True
        self._session.send("Fetch.continueRequest", {"requestId": self._id})


def handle_document_route(route: Any) -> None:
    """Refuse a document navigation that robots.txt disallows or could not judge.

    ``route`` has ``request`` (``url``, ``resource_type``, ``headers``) and ``abort()`` /
    ``continue_()``. Only resource type ``document`` is judged. CDP spells that
    ``Document``; a frame and a redirect hop both arrive as documents. An image, a
    stylesheet, or a script is continued and is not passed to ``decide()``.

    Playwright's ``page.route`` handler is not called for a redirect hop, so a
    browser fetcher has to pause Document requests itself. This archive's HTTP
    fetcher uses ``decide`` on each hop and does not open a browser. The guard is
    here so a later browser collector can call it without a second matcher.
    """
    request = route.request
    if str(getattr(request, "resource_type", "") or "").lower() != "document":
        route.continue_()
        return
    headers = getattr(request, "headers", None) or {}
    user_agent = ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        user_agent = str(getter("User-Agent") or getter("user-agent") or "")
    decision = decide(str(getattr(request, "url", "") or ""), user_agent)
    if decision.verdict in (VERDICT_DISALLOWED, VERDICT_UNREACHABLE):
        # RobotsRefused logs the refusal. Abort instead of raising: the handler
        # has to finish, and Chromium must not send the request.
        RobotsRefused(str(request.url), decision.verdict)
        route.abort()
        return
    route.continue_()


def install_document_guard(page: Any) -> None:
    """Pause every Document request on ``page`` and refuse the ones robots.txt forbids.

    Playwright's page.route handler is not called for a redirect hop: Playwright
    continues that hop itself. A Chrome DevTools Fetch session pauses each hop,
    and each frame, before Chromium sends it. ``decide()`` uses the User-Agent on
    the paused request. Stylesheets, scripts, and images are outside the Fetch
    pattern, so they load. Installing twice does nothing.
    """
    if getattr(page, "_giye_document_guard", False):
        return
    session = page.context.new_cdp_session(page)
    session.send(
        "Fetch.enable",
        {
            "patterns": [
                {"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}
            ]
        },
    )

    def paused(params: dict) -> None:
        """Judge one paused Document request and abort it when robots.txt refuses it."""
        route = _CdpDocumentRoute(session, params)
        try:
            handle_document_route(route)
        except Exception as exc:  # noqa: BLE001 — the handler must finish; Chromium must not send the request
            log.warning("document robots check failed: %s (%s)", route.request.url, type(exc).__name__)
            if not route.settled:
                try:
                    route.abort()
                except Exception as abort_exc:  # noqa: BLE001 — abort is best-effort after the check already failed
                    log.warning(
                        "document robots check could not abort: %s (%s)",
                        route.request.url,
                        type(abort_exc).__name__,
                    )

    session.on("Fetch.requestPaused", paused)
    # The CDP session stays with the page. This flag only blocks a second install.
    page._giye_document_guard = True


def _fetch_rules(origin: str, user_agent: str, *, timeout: float, get, session) -> OriginRules:
    current = f"{origin}/robots.txt"
    seen: set[str] = set()
    # Any hop may be the one that needed the certificate retry. Remember it
    # even when a later hop verifies cleanly.
    tls_unverified = False
    fetcher = get or (lambda fetch_url, timeout: _default_get(fetch_url, timeout, session=session, user_agent=user_agent))

    def noted(response_or_exc: object) -> None:
        """Remember a hop that was fetched only after TLS verification was turned off."""
        nonlocal tls_unverified
        if getattr(response_or_exc, "tls_unverified", False):
            tls_unverified = True
            log.warning("robots.txt fetched with TLS verification disabled: %s", current)

    for followed in range(MAX_REDIRECTS + 1):
        if current in seen:
            # A loop cannot produce a file inside the redirect budget.
            return OriginRules("unavailable", None, None, current, "redirect_loop", tls_unverified)
        seen.add(current)
        try:
            response = fetcher(current, timeout)
        except Exception as exc:  # classified below; other bugs propagate
            noted(exc)
            if not _is_fetch_failure(exc):
                raise
            kind = "timeout" if _is_timeout(exc) else "network"
            return OriginRules("unreachable", None, None, current, kind, tls_unverified)
        noted(response)
        status = int(response.status_code)
        if status in REDIRECT_STATUSES:
            if followed == MAX_REDIRECTS:
                # RFC 9309 §2.3.1.2: more than five consecutive redirects, the
                # file may be treated as unavailable (allowed). Kept on purpose.
                return OriginRules("unavailable", None, status, current, "too_many_redirects", tls_unverified)
            nxt = _location(response, current)
            if not nxt:
                return OriginRules("unreachable", None, status, current, "bad_redirect", tls_unverified)
            current = nxt
            continue
        if 200 <= status < 300:
            selected = tuple(_select_rules(_parse(_body(response)), user_agent))
            final = getattr(response, "url", None) or current
            return OriginRules("parsed", selected, status, final, "", tls_unverified)
        if 400 <= status < 500:
            return OriginRules("unavailable", None, status, current, "", tls_unverified)
        # 5xx, and anything else we do not know how to follow (1xx, 300, 304).
        return OriginRules("unreachable", None, status, current, "status", tls_unverified)


def _product_token(value: str) -> str:
    """Product token: leading [A-Za-z_-], or '*' for the catch-all group.

    The token stops at the first other character, so "GiyeArchiveBot/0.2"
    yields "GiyeArchiveBot". A line that is "*" or "*" plus whitespace is the
    catch-all. Anything else that does not start with a token is ignored.
    """
    value = value.strip()
    if not value:
        return ""
    if value[0] == "*" and (len(value) == 1 or value[1].isspace()):
        return "*"
    token: list[str] = []
    for ch in value:
        if ch.isascii() and (ch.isalpha() or ch in "-_"):
            token.append(ch)
        else:
            break
    return "".join(token)


def _strip_comment(line: str) -> str:
    # "#" starts a comment even mid-line (the path grammar excludes it).
    hash_at = line.find("#")
    if hash_at >= 0:
        line = line[:hash_at]
    return line.strip()


def _parse(text: str) -> list[_Group]:
    """Groups of product tokens and the allow/disallow rules that follow them.

    Several user-agent lines in a row, before any rule, are one group (they
    share the rules). A user-agent line after a rule starts the next group.
    A Sitemap line, or any other record, does not end a group. Rules that
    precede the first user-agent line are ignored.
    """
    text = text.removeprefix("\ufeff")
    groups: list[_Group] = []
    tokens: list[str] = []
    rules: list[_Rule] = []

    def flush() -> None:
        """Store the current user-agent group and start the next one."""
        nonlocal tokens, rules
        if tokens:
            groups.append(_Group(tuple(tokens), tuple(rules)))
        tokens = []
        rules = []

    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if rules:
                flush()
            token = _product_token(value)
            if token:
                tokens.append(token)
            continue
        if key in ("allow", "disallow"):
            if not tokens:
                continue
            rules.append(_Rule(key == "allow", _normalize_pattern(value)))
            continue
    flush()
    return groups


def _select_rules(groups: list[_Group], user_agent: str) -> list[_Rule]:
    """Rules of the most specific group, or of '*' when nothing names us.

    Equality is case-insensitive and exact on the product token. Groups that
    name the same token are merged (RFC 9309 §2.2.1). Their rules are not
    merged with '*': the named group is the more specific one. A token that
    is only a prefix of the crawler's token is a different product and is
    not a match.
    """
    crawler = _product_token(user_agent).lower()
    specific: list[_Rule] = []
    wildcard: list[_Rule] = []
    matched = False
    for group in groups:
        named = crawler and any(token != "*" and token.lower() == crawler for token in group.tokens)
        if named:
            matched = True
            specific.extend(group.rules)
        elif "*" in group.tokens:
            wildcard.extend(group.rules)
    if matched:
        return specific
    return wildcard


def _percent(octet: int) -> str:
    """One octet as an uppercase percent-escape (RFC 9309 §2.2.2)."""
    return f"%{octet:02X}"


def _normalize(text: str, *, keep: str, syntax: str = "") -> str:
    """Normalise one path or query piece for §2.2.2 comparison.

    `keep` characters stay literal because they structure the path or the
    query. `syntax` characters stay literal because they are rule syntax
    ("*" and, before this function sees it, a trailing "$" already removed).
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "%" and i + 2 < len(text) and text[i + 1] in _HEX and text[i + 2] in _HEX:
            octet = int(text[i + 1 : i + 3], 16)
            decoded = chr(octet)
            if decoded in _UNRESERVED:
                out.append(decoded)
            else:
                out.append("%" + text[i + 1 : i + 3].upper())
            i += 3
            continue
        if ord(ch) < 128:
            if ch in _UNRESERVED or ch in keep or ch in syntax:
                out.append(ch)
            else:
                out.append(_percent(ord(ch)))
        else:
            for byte in ch.encode("utf-8"):
                out.append(_percent(byte))
        i += 1
    return "".join(out)


def _normalize_pattern(pattern: str) -> str:
    """Normalise a rule. A final '$' is kept as the end anchor; a '$' inside is a literal."""
    if pattern == "":
        return ""
    anchor = pattern.endswith("$")
    body = pattern[:-1] if anchor else pattern
    if "?" in body:
        path, query = body.split("?", 1)
        norm = _normalize(path, keep="/", syntax="*") + "?" + _normalize(query, keep="=&", syntax="*")
    else:
        norm = _normalize(body, keep="/", syntax="*")
    if anchor:
        norm += "$"
    return norm


def _match_target(url: str) -> str:
    """Path plus query, normalised. The fragment is not part of the match."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    path = _normalize(path, keep="/")
    if parsed.query:
        path += "?" + _normalize(parsed.query, keep="=&")
    return path


def _is_robots_file(url: str) -> bool:
    # The file name is lowercase. "/Robots.txt" is a different path and can be disallowed.
    return (urlparse(url).path or "/") == "/robots.txt"


def _matches(path: str, pattern: str) -> bool:
    """True when `pattern` matches `path` from the first octet.

    Without a trailing '$' the rule is a prefix: the pattern may end while
    the path continues. '*' consumes any run, including an empty one. The
    search keeps every path index the pattern could be at, so a later literal
    can still match after a greedy '*'.
    """
    pathlen = len(path)
    pos = [0]
    last = len(pattern) - 1
    for index, pat in enumerate(pattern):
        if pat == "$" and index == last:
            return pos[-1] == pathlen
        if pat == "*":
            start = pos[0]
            pos = list(range(start, pathlen + 1))
            continue
        nxt: list[int] = []
        for at in pos:
            if at < pathlen and path[at] == pat:
                nxt.append(at + 1)
        if not nxt:
            return False
        pos = nxt
    return True


def _allowed_by_rules(rules: tuple[_Rule, ...] | list[_Rule], url: str) -> bool:
    """Longest match wins. Equal lengths: allow wins. No match: allowed.

    An empty pattern has length 0 and is not a match, so "Disallow:" alone
    allows every URL. "/robots.txt" is allowed before any rule is considered.
    """
    if _is_robots_file(url):
        return True
    path = _match_target(url)
    allow = 0
    disallow = 0
    for rule in rules:
        pattern = rule.pattern
        if not pattern or not _matches(path, pattern):
            continue
        length = len(pattern)
        if rule.allow:
            allow = max(allow, length)
        else:
            disallow = max(disallow, length)
    if allow == 0 and disallow == 0:
        return True
    return allow >= disallow


def _body(response: object) -> str:
    """robots.txt body as UTF-8 text, capped at the parse limit."""
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        # RFC 9309 §2.2 is UTF-8. requests would otherwise guess ISO-8859-1 when
        # the header omits charset, and a non-ASCII rule would be misread.
        return content[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace")
    text = getattr(response, "text", "") or ""
    return text[:MAX_ROBOTS_BYTES]


def _location(response: object, current: str) -> str | None:
    """Absolute URL from a redirect's Location header, or None when it is missing."""
    headers = getattr(response, "headers", None) or {}
    loc = headers.get("Location")
    if not loc:
        loc = headers.get("location")
    if not loc:
        return None
    nxt = urljoin(current, str(loc).strip())
    parsed = urlparse(nxt)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return nxt


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    try:
        import requests
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, requests.Timeout)


def _is_fetch_failure(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, OSError)):
        return True
    try:
        import requests
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, requests.RequestException)


def _default_get(url: str, timeout: float, *, session, user_agent: str):
    """GET one robots.txt hop. Redirects are not followed here.

    A TLS verification failure is retried once with verification off. The
    returned object has tls_unverified set when that retry was used. If the
    retry also fails, the exception carries the same flag and the caller
    records an unreachable file.
    """
    import requests
    import urllib3

    own_session = session is None
    http = session or requests.Session()
    if own_session:
        http.headers["User-Agent"] = user_agent

    def attempt(verify: bool) -> SimpleNamespace:
        """GET robots.txt once. ``verify`` is TLS certificate checking. Returns the capped response."""
        response = http.get(
            url,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
            verify=verify,
            headers={"User-Agent": user_agent},
        )
        try:
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(8192):
                if not chunk:
                    continue
                room = MAX_ROBOTS_BYTES - total
                if room <= 0:
                    break
                piece = chunk[:room]
                chunks.append(piece)
                total += len(piece)
        finally:
            response.close()
        content = b"".join(chunks)
        return SimpleNamespace(
            status_code=response.status_code,
            headers=response.headers,
            content=content,
            text=content.decode("utf-8", errors="replace"),
            url=response.url,
            encoding="utf-8",
            tls_unverified=not verify,
        )

    try:
        return attempt(True)
    except requests.exceptions.SSLError:
        # Same certificate retry as the evidence keeper: judge the file, not the cert.
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        try:
            return attempt(False)
        except Exception as exc:
            exc.tls_unverified = True  # type: ignore[attr-defined]
            raise
