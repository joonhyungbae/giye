# SPDX-License-Identifier: AGPL-3.0-only
"""Fetch registered CVs and keep a snapshot only when the text hash changes.

Every request goes through ``giye.collect.Fetcher``, which checks robots.txt
first. The content hash is the whitespace-free SHA-256 of the extracted text
(``fingerprint``). An unchanged hash does not write a new snapshot
and does not count as a new CV. A new or changed text is queued
(``cv_new`` / ``cv_changed``). A failure, including a robots disallow, is
queued as ``cv_pull_failed`` and the registry row is kept.
"""

from __future__ import annotations

import difflib
import hashlib
import uuid
from datetime import date, datetime, timezone

from giye.collect.fetch import Fetcher, Page
from giye.config import Config
from giye.extract.paths import resolve_stored, stored_name
from giye.extract.text import extension_for, extract_text, fetch_target, fingerprint, normalize
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import REVIEW_FIELDS, empty_row

MAX_BYTES = 30 * 1024 * 1024


class NotPublic(Exception):
    """The host returned a login page instead of the file."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_of(today: date | None) -> date:
    """UTC date, the same clock as ledger backups, so a snapshot and its backup name the same day."""
    return today or datetime.now(timezone.utc).date()


def _active(row: dict[str, str]) -> bool:
    return row.get("active", "").lower() in ("true", "yes", "1")


def enqueue(queue: list[dict[str, str]], ledger_id: str, reason: str, detail: str) -> None:
    """One open item per person and reason. A repeat updates the detail."""
    for item in queue:
        if item["ledger_id"] == ledger_id and item["reason"] == reason and item["status"] == "open":
            item["detail"] = detail
            return
    queue.append(
        empty_row(
            REVIEW_FIELDS,
            queue_id=str(uuid.uuid4()),
            ledger_id=ledger_id,
            reason=reason,
            detail=detail,
            status="open",
            created_at=_now(),
        )
    )


def pull_active(
    ledger: Ledger, fetcher: Fetcher, *, today: date | None = None, offline_only: bool = False
) -> dict[str, int]:
    """Fetch every active source. Returns a count per ``last_status``.

    ``offline_only`` (``giye extract --replay-only``) pulls only sources whose
    download URL is under one of the fetcher's ``offline_roots``, which are
    local directories (the demo's fixture CVs). A replay-only run reaches no
    network: every other source keeps the snapshot it has, its fetch columns
    are not touched, and no ``cv_pull_failed`` item is queued for it. When no
    source was pulled, neither table is written.
    """
    config = ledger.config
    rows = ledger.read("cv_sources")
    queue = ledger.read("review_queue")
    counts: dict[str, int] = {}
    stamp = _as_of(today)
    pulled = False
    for row in rows:
        if not _active(row):
            continue
        if offline_only and not _served_offline(fetcher, row):
            continue
        status = pull_one(config, row, queue, fetcher, today=stamp)
        counts[status] = counts.get(status, 0) + 1
        pulled = True
    if pulled:
        ledger.write("cv_sources", rows, task="pull-cv")
        ledger.write("review_queue", queue, task="pull-cv")
    return counts


def _served_offline(fetcher: Fetcher, row: dict[str, str]) -> bool:
    """True when this source's download URL is read from a configured local directory."""
    try:
        target = fetch_target(row.get("kind") or "web", row.get("fetch_url") or row["url"])
    except ValueError:
        return False
    return any(target == prefix or target.startswith(prefix + "/") for prefix, _root in fetcher.offline_roots)


def pull_one(
    config: Config,
    row: dict[str, str],
    queue: list[dict[str, str]],
    fetcher: Fetcher,
    *,
    today: date | None = None,
) -> str:
    """Fetch one source, snapshot it when the hash changes, and return the status."""
    row["last_pulled_at"] = _now()
    target = row.get("fetch_url") or row["url"]
    kind = row.get("kind") or "web"
    try:
        body, ext = _download(fetcher, kind, target)
        text = normalize(extract_text(body, ext))
        if kind in ("web", "web_layout") and not text.strip():
            raise ValueError("page returned no text")
    except NotPublic as exc:
        row["last_status"] = "not_public"
        enqueue(queue, row["ledger_id"], "cv_pull_failed", f"{row['source_id']}: {exc}")
        return row["last_status"]
    except Exception as exc:  # noqa: BLE001 — queue the failure and keep the registry row
        row["last_status"] = "error"
        enqueue(queue, row["ledger_id"], "cv_pull_failed", f"{row['source_id']}: {type(exc).__name__}: {exc}")
        return row["last_status"]

    digest = fingerprint(text) if text else hashlib.sha256(body).hexdigest()
    prev_stored = (row.get("snapshot_path") or "") + ".txt" if row.get("snapshot_path") else ""
    prev_path = resolve_stored(config, prev_stored) if prev_stored else None
    prev_text = prev_path.read_text(encoding="utf-8") if prev_path and prev_path.is_file() else None
    if digest == row.get("content_sha256") or (text and prev_text is not None and fingerprint(prev_text) == digest):
        row["content_sha256"] = digest
        row["last_status"] = "unchanged"
        return row["last_status"]

    stamp = _as_of(today)
    stem = config.raw / "cv" / row["ledger_id"] / row["source_id"] / f"{stamp:%Y%m%d}-{digest[:8]}"
    stem.parent.mkdir(parents=True, exist_ok=True)
    if ext != "txt":
        stem.with_suffix(f".{ext}").write_bytes(body)
    text_path = stem.parent / f"{stem.name}.txt"
    text_path.write_text(text + "\n", encoding="utf-8")

    row["snapshot_path"] = stored_name(config, stem)
    row["content_sha256"] = digest
    row["last_changed_at"] = _now()

    if prev_text is None:
        row["last_status"] = "new"
        enqueue(queue, row["ledger_id"], "cv_new", f"{row['source_id']}: {row['snapshot_path']}.txt")
    else:
        diff = list(
            difflib.unified_diff(
                prev_text.splitlines(),
                text.splitlines(),
                fromfile=stored_name(config, prev_path) if prev_path else "",
                tofile=row["snapshot_path"] + ".txt",
                lineterm="",
            )
        )
        diff_dir = config.work / "cv_diffs"
        diff_dir.mkdir(parents=True, exist_ok=True)
        diff_path = diff_dir / f"{row['source_id']}-{stamp:%Y%m%d}.diff"
        diff_path.write_text("\n".join(diff) + "\n", encoding="utf-8")
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        row["last_status"] = "changed"
        enqueue(
            queue,
            row["ledger_id"],
            "cv_changed",
            f"{row['source_id']}: +{added}/-{removed} lines, {stored_name(config, diff_path)}",
        )
    return row["last_status"]


def _download(fetcher: Fetcher, kind: str, url: str) -> tuple[bytes, str]:
    """GET ``url`` (rewritten for a share link). Raises ``RobotsDisallowed`` before a refused request."""
    if kind == "gdrive_folder":
        raise ValueError("a drive folder is not a CV file")
    target = fetch_target(kind, url)
    page = fetcher.get(target)
    if not page.ok:
        raise RuntimeError(f"HTTP {page.status}")
    if len(page.content) > MAX_BYTES:
        raise ValueError(f"response larger than {MAX_BYTES // (1024 * 1024)}MB")
    _reject_login_page(kind, page)
    ext = extension_for(page.content, page.content_type, kind)
    return page.content, ext


def _reject_login_page(kind: str, page: Page) -> None:
    """A Google HTML page is not the exported file. A virus-scan form is refused rather than followed."""
    if kind not in ("gdrive_file", "gdoc", "gsheet"):
        return
    ctype = (page.content_type or "").lower()
    looks_html = "html" in ctype or b"<html" in page.content[:200].lower()
    if not looks_html and page.content[:5] != b"%PDF-":
        return
    if page.content[:5] == b"%PDF-":
        return
    text = page.content.decode("utf-8", errors="replace")
    if "accounts.google.com" in page.url or "ServiceLogin" in text:
        raise NotPublic("sharing is not 'anyone with the link'")
    if "html" in (page.content_type or "").lower() or b"<html" in page.content[:200].lower():
        raise NotPublic("drive returned an html page instead of the file")
