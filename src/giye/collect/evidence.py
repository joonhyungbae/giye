# SPDX-License-Identifier: AGPL-3.0-only
"""Keep an original copy of every URL the archive cites.

Ported from ``scripts/archive_evidence.py``. For each cited URL:

1. Social platforms whose terms forbid collection (Instagram, Facebook, LinkedIn,
   X, Threads, TikTok) are not requested. Status ``platform_excluded``.
2. Otherwise the URL is fetched through ``Fetcher`` (robots.txt, delay, TLS retry).
   A successful body is stored with ``via=direct``.
3. When the page is gone — HTTP 404 or 410, or the connection fails — an existing
   Internet Archive capture is stored with ``via=archive.org`` and the capture
   timestamp. The lookup is the availability API plus the ``id_`` raw URL.
   Giye never calls Save Page Now and never asks the Archive to create a capture.
4. Other direct failures (HTTP 5xx, an empty or oversized body) also fall through
   to that existing-capture lookup, as production did.
5. When robots.txt disallows the URL, record the existing capture's URL and
   timestamp (``archive_link_only``, ``direct_failure=robots``) and do not
   download, keep, or serve the bytes. The availability API is the only Archive
   request. There is no switch that stores those bytes. An unreachable
   robots.txt (HTTP 5xx, timeout, network error) is ``robots_disallowed`` with
   reason ``robots_unreachable`` and is not sent to the Archive.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
import yaml

from giye.collect.fetch import Fetcher, Page, fetcher_from_config
from giye.collect.robots import VERDICT_UNREACHABLE, RobotsRefused
from giye.collect.snapshot import MAX_BYTES, SnapshotStore, servable_rows, utc_now

# Production list. Their terms forbid automated collection.
SOCIAL_HOSTS = (
    "instagram.com",
    "facebook.com",
    "linkedin.com",
    "x.com",
    "twitter.com",
    "threads.net",
    "tiktok.com",
)

# Availability API, then the raw capture. There is no Save Page Now URL in this module.
WAYBACK_AVAILABLE = "https://archive.org/wayback/available"

URL_COLUMNS = {"source_url", "url", "fetch_url", "archive_url", "members_source"}
URL_IN_TEXT = re.compile(r"https?://[^\s,'\"<>）)]+")
SETTLED = frozenset(
    {
        "fetched",
        "archive_org",
        "archive_link_only",
        "platform_excluded",
        "robots_disallowed",
        "collector_or_cv_snapshot",
    }
)
_TIMESTAMP = re.compile(r"\d{1,20}")


def is_social(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in SOCIAL_HOSTS)


def settle_url(
    url: str,
    *,
    fetcher: Fetcher,
    store: SnapshotStore,
    frame: str = "_evidence",
    collector: str = "evidence",
    run_id: str = "",
) -> dict:
    """Fetch ``url`` or an existing Archive capture. Never requests a new capture.

    A host whose robots.txt disallows the URL is link-only: the availability
    API may run, and the capture URL and timestamp are recorded. The ``id_``
    raw URL is not requested and no body is stored.
    """
    now = utc_now()
    if is_social(url):
        return {"status": "platform_excluded", "at": now}
    reason = ""
    page: Page | None
    try:
        page = fetcher.get(url)
    except RobotsRefused as exc:
        # A parsed disallow records the capture link and does not download it.
        # An unreachable robots.txt is not sent to the Archive.
        reason = "robots_unreachable" if exc.verdict == VERDICT_UNREACHABLE else "robots"
        if reason != "robots":
            return {"status": "robots_disallowed", "at": now, "reason": reason, "robots": exc.verdict}
        linked = _archive_link(url, fetcher)
        if linked is None:
            return {"status": "unavailable", "at": now, "reason": reason, "robots": exc.verdict}
        capture, timestamp = linked
        return {
            "status": "archive_link_only",
            "at": now,
            "capture_url": capture,
            "archived_at": timestamp,
            "direct_failure": "robots",
            "robots": exc.verdict,
        }
    except requests.RequestException as exc:
        reason = type(exc).__name__
        page = None
    else:
        body = page.content
        if page.ok and body and len(body) <= MAX_BYTES:
            path = store.keep(
                frame,
                url,
                body,
                final_url=page.url if page.url != url else "",
                status=page.status,
                content_type=page.content_type,
                collector=collector,
                run_id=run_id,
                tls_unverified=page.tls_unverified,
                via="direct",
                robots=page.robots,
                **({"robots_tls_unverified": True} if page.robots_tls_unverified else {}),
            )
            if path is not None:
                result = {
                    "status": "fetched",
                    "at": now,
                    "path": path.relative_to(store.root).as_posix(),
                    "http": page.status,
                }
                if page.tls_unverified:
                    result["tls_unverified"] = True
                return result
        reason = f"http {page.status}" if not page.ok else "too large or empty"

    captured = _existing_capture(url, fetcher)
    if captured is not None:
        content, timestamp, captured_page = captured
        path = store.keep(
            frame,
            url,
            content,
            final_url=captured_page.url,
            status=captured_page.status,
            content_type=captured_page.content_type,
            collector=collector,
            run_id=run_id,
            tls_unverified=captured_page.tls_unverified,
            via="archive.org",
            archived_at=timestamp,
            direct_failure=reason,
            robots=captured_page.robots,
            **({"robots_tls_unverified": True} if captured_page.robots_tls_unverified else {}),
        )
        if path is not None:
            return {
                "status": "archive_org",
                "at": now,
                "path": path.relative_to(store.root).as_posix(),
                "archived_at": timestamp,
                "direct_failure": reason,
            }
    return {"status": "unavailable", "at": now, "reason": reason}


def _archive_link(url: str, fetcher: Fetcher) -> tuple[str, str] | None:
    """Capture URL and timestamp from the availability API. Does not fetch ``id_`` bytes."""
    query = urlencode({"url": url})
    try:
        listed = fetcher.get(f"{WAYBACK_AVAILABLE}?{query}")
    except (RobotsRefused, requests.RequestException):
        return None
    if not listed.ok:
        return None
    try:
        payload = json.loads(listed.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    closest = ((payload or {}).get("archived_snapshots") or {}).get("closest") or {}
    if not closest.get("available"):
        return None
    timestamp = str(closest.get("timestamp") or "")
    if not _TIMESTAMP.fullmatch(timestamp):
        return None
    return _capture_url(closest, url, timestamp), timestamp


def _capture_url(closest: dict, original: str, timestamp: str) -> str:
    """Public replay URL. A raw ``id_`` URL is not provenance we store."""
    found = str(closest.get("url") or "").strip().replace("id_/", "", 1)
    if found.startswith("http://web.archive.org/"):
        found = "https://" + found[len("http://"):]
    if found.startswith("https://web.archive.org/web/"):
        return found
    return f"https://web.archive.org/web/{timestamp}/{original}"


def _existing_capture(url: str, fetcher: Fetcher) -> tuple[bytes, str, Page] | None:
    """Return bytes, capture timestamp, and the Archive response for an existing snapshot.

    Uses the public availability API and the ``id_`` raw-bytes URL only.
    """
    query = urlencode({"url": url})
    try:
        listed = fetcher.get(f"{WAYBACK_AVAILABLE}?{query}")
    except (RobotsRefused, requests.RequestException):
        return None
    if not listed.ok:
        return None
    try:
        payload = json.loads(listed.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    closest = ((payload or {}).get("archived_snapshots") or {}).get("closest") or {}
    if not closest.get("available"):
        return None
    timestamp = str(closest.get("timestamp") or "")
    if not _TIMESTAMP.fullmatch(timestamp):
        return None
    # Raw capture of an existing snapshot. Not /save/ (Save Page Now).
    raw_url = f"https://web.archive.org/web/{timestamp}id_/{url}"
    try:
        raw = fetcher.get(raw_url)
    except (RobotsRefused, requests.RequestException):
        return None
    if raw.ok and raw.content and len(raw.content) <= MAX_BYTES:
        return raw.content, timestamp, raw
    return None


def cited_urls(config: object) -> dict[str, set[str]]:
    """Map each cited URL to the ledger tables or files that name it.

    Walks the same kinds of places as production ``cited()``: ledger CSV URL columns,
    URLs written into reviewer notes, and ``source_url`` / ``url`` fields in YAML and
    JSON under the archive data directory (snapshot bodies and status files excluded).
    """
    where: dict[str, set[str]] = {}

    def add(url: str, source: str) -> None:
        url = (url or "").strip().split("#")[0]
        if url.startswith("http"):
            where.setdefault(url, set()).add(source)

    ledger = Path(config.ledger)  # type: ignore[attr-defined]
    if ledger.is_dir():
        for path in sorted(ledger.glob("*.csv")):
            for row in _read_csv(path):
                for column, value in row.items():
                    if column in URL_COLUMNS and isinstance(value, str):
                        add(value, path.stem)
                if path.stem in {"artists", "activities"}:
                    for found in URL_IN_TEXT.findall(row.get("reviewer_note") or ""):
                        add(found.rstrip(").,;」』"), f"{path.stem}.reviewer_note")

    data = Path(config.data)  # type: ignore[attr-defined]
    frames = Path(config.frames)  # type: ignore[attr-defined]
    files: list[Path] = []
    if frames.is_file():
        files.append(frames)
    if data.is_dir():
        for pattern in ("*.yml", "*.yaml", "*.json"):
            files.extend(data.glob(pattern))
            raw = data / "raw"
            if raw.is_dir():
                files.extend(path for path in raw.rglob(pattern) if "snapshots" not in path.parts)
    seen_files: set[Path] = set()
    for path in files:
        if path.name == "status.json" or path in seen_files or "snapshots" in path.parts:
            continue
        seen_files.add(path)
        try:
            text = path.read_text(encoding="utf-8")
            obj = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
        except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError):
            continue
        try:
            label = str(path.relative_to(data))
        except ValueError:
            label = path.name
        for found in _urls_in(obj):
            add(found, f"raw:{label}")
    return where


def archive_cited(
    config: object,
    urls: Mapping[str, Iterable[str]] | Iterable[str] | None = None,
    *,
    fetcher: Fetcher | None = None,
    store: SnapshotStore | None = None,
    retry_unavailable: bool = False,
    run_id: str = "",
) -> dict[str, dict]:
    """Settle cited URLs and write ``data/work/evidence/status.json``.

    URLs already settled in that file are left as they are (production's resumable
    run).     ``unavailable`` is retried only when ``retry_unavailable`` is set.
    ``archive_link_only`` and ``robots_disallowed`` are settled: a later run does
    not fetch the disallowed host again.
    """
    fetcher = fetcher or fetcher_from_config(config)
    store = store or SnapshotStore(Path(config.raw))  # type: ignore[attr-defined]
    if urls is None:
        where = cited_urls(config)
    elif isinstance(urls, Mapping):
        where = {url: set(sources) for url, sources in urls.items()}
    else:
        where = {url: {"caller"} for url in urls}

    out_dir = Path(config.work) / "evidence"  # type: ignore[attr-defined]
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / "status.json"
    status: dict[str, dict] = {}
    if status_path.is_file():
        loaded = json.loads(status_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            status = loaded

    have = _already_snapshotted(config)
    for url in sorted(where):
        if url in have:
            status[url] = {**status.get(url, {}), "status": "collector_or_cv_snapshot"}
            continue
        previous = status.get(url, {}).get("status")
        if previous in SETTLED:
            continue
        if previous == "unavailable" and not retry_unavailable:
            continue
        status[url] = {
            **settle_url(
                url,
                fetcher=fetcher,
                store=store,
                run_id=run_id,
            ),
            "cited_in": sorted(where[url]),
        }
    for url, sources in where.items():
        status.setdefault(url, {})["cited_in"] = sorted(sources)
    status = {url: value for url, value in status.items() if url in where}
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return status


def _already_snapshotted(config: object) -> set[str]:
    have: set[str] = set()
    raw = Path(config.raw)  # type: ignore[attr-defined]
    if not raw.is_dir():
        return have
    for manifest in raw.glob("*/snapshots/manifest.jsonl"):
        parsed: list[dict] = []
        for line in manifest.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                parsed.append(row)
        for row in servable_rows(parsed):
            if row.get("url"):
                have.add(row["url"])
            if row.get("final_url"):
                have.add(row["final_url"])
    return have


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]


def _urls_in(obj: object) -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in URL_COLUMNS and isinstance(value, str) and value.startswith("http"):
                found.append(value)
            elif key == "sources" and isinstance(value, list):
                found.extend(item for item in value if isinstance(item, str) and item.startswith("http"))
            else:
                found.extend(_urls_in(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_urls_in(value))
    return found
