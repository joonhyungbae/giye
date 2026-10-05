# SPDX-License-Identifier: AGPL-3.0-only
"""Content-addressed copies of bytes a collector or the evidence pass fetched.

Each fetch appends one ``manifest.jsonl`` line, even when the bytes were
already on disk. Identical content is written once; a later fetch only adds a
manifest line (``new`` is false and ``path`` points at the existing object).

The public path is the SHA-256 itself (``<frame>/snapshots/sha256/<sha[:2]>/<sha><ext>``)
so the file name does not depend on the URL. Lookup does not use that name. A later
fetch of the same bytes is found by the full sha256 on an existing manifest line.
A short prefix is not an identity: two objects can share the first characters.
A file that only shares that prefix is not reused.

New lines always record ``status``, ``final_url``, ``content_type``, and ``robots``
(``allowed``, ``unavailable_allowed``, ``disallowed``, ``unreachable_disallowed``,
or ``not_checked``). ``not_checked`` is only for bytes the caller already holds and
did not just fetch. A refused hop is not stored: there are no bytes. A host
whose terms forbid collection is the same. ``Fetcher`` raises before
robots.txt, and this store gets no line.

Manifest version 1 does not keep the original response headers. Version 2 would be
the first to store them. Until then a WARC export reconstructs a status line and
Content-Type from these fields and the stored body (see ``giye.export``).

``recall`` reads the lines back for ``giye collect --from-snapshots``. It scans
``<root>/*/snapshots/manifest.jsonl``. A line matches when its ``url`` or
``final_url`` equals the requested URL. Withdrawn lines are not bodies
(``servable_rows``). The newest match has the greatest ``fetched_at``; an equal
timestamp keeps the later line. When ``prefer_frame`` also has a match, only
that frame is considered, so a collector re-reads its own store first.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Empty bodies and bodies above 40 MB are not a page capture. The snapshot
# store and the evidence pass both skip them.
MAX_BYTES = 40 * 1024 * 1024

# Version 1 has no original response headers. See the module docstring.
MANIFEST_VERSION = 1
ROBOTS_NOT_CHECKED = "not_checked"
HEADERS_NOT_KEPT = (
    "Original response headers were not kept before manifest v2. "
    "The WARC response record reconstructs a minimal status line, Content-Type, "
    "and Content-Length from the manifest and the stored body."
)


def utc_now() -> str:
    """UTC timestamp ``YYYY-MM-DDTHH:MM:SSZ`` for a manifest line."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def withdrawn_keys(rows: list[dict]) -> set[str]:
    """URLs whose latest manifest decision is a withdrawal.

    A line with ``withdrawn`` true records a capture link and is not a body.
    Earlier lines for that URL, including a ``final_url`` they stored, are
    withdrawn with it so an export does not serve the old bytes.
    """
    keys_of: dict[str, set[str]] = {}
    withdrawn: set[str] = set()
    for row in rows:
        url = row.get("url") if isinstance(row.get("url"), str) else ""
        final = row.get("final_url") if isinstance(row.get("final_url"), str) else ""
        keys = {key for key in (url, final) if key}
        if url:
            keys_of.setdefault(url, set()).update(keys)
        if row.get("withdrawn") and url:
            withdrawn.add(url)
    blocked: set[str] = set()
    for url in withdrawn:
        blocked.update(keys_of.get(url, {url}))
    return blocked


def servable_rows(rows: list[dict]) -> list[dict]:
    """Lines whose bytes may be read. A withdrawn URL is link-only."""
    blocked = withdrawn_keys(rows)
    served: list[dict] = []
    for row in rows:
        if row.get("withdrawn"):
            continue
        url = row.get("url") if isinstance(row.get("url"), str) else ""
        if url and url in blocked:
            continue
        served.append(row)
    return served


@dataclass(frozen=True)
class KeptBody:
    """One servable manifest line and the bytes it points at."""

    frame: str
    url: str
    final_url: str
    status: int
    content: bytes
    content_type: str
    fetched_at: str
    robots: str
    tls_unverified: bool = False
    robots_tls_unverified: bool = False


class SnapshotStore:
    """Keep response bodies under ``root`` (the archive's ``data/raw`` directory)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        # snapshots directory → full sha256 → path as stored on the manifest line.
        self._sha_index: dict[Path, dict[str, str]] = {}
        # Servable lines for ``recall``. Cleared when ``keep`` appends a line.
        self._kept_index: list[tuple[str, dict]] | None = None

    def keep(
        self,
        frame: str,
        url: str,
        content: bytes | str,
        *,
        final_url: str = "",
        status: int | None = None,
        content_type: str = "",
        collector: str = "",
        run_id: str = "",
        tls_unverified: bool = False,
        ext: str | None = None,
        via: str = "",
        archived_at: str = "",
        robots: str = ROBOTS_NOT_CHECKED,
        **extra: object,
    ) -> Path | None:
        """Store ``content`` if it is new, and always append a manifest line.

        Returns ``None`` without writing when the body is empty or larger than
        ``MAX_BYTES``. An empty body is not a capture, and a body over the limit
        is not stored.
        """
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not content or len(content) > MAX_BYTES:
            return None
        frame_dir = _frame_dir(frame)
        suffix = _extension(content, content_type, ext)
        sha = hashlib.sha256(content).hexdigest()
        snapshots = self.root / frame_dir / "snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        path, is_new = self._locate(snapshots, frame_dir, sha, suffix, content)
        rel = path.relative_to(self.root).as_posix()
        self._sha_index_for(snapshots)[sha] = rel
        line = {
            "url": url,
            "final_url": final_url or url,
            "status": status,
            "fetched_at": utc_now(),
            "sha256": sha,
            "bytes": len(content),
            "content_type": content_type,
            "robots": robots or ROBOTS_NOT_CHECKED,
            "manifest_version": MANIFEST_VERSION,
            "collector": collector,
            "run_id": run_id,
            "tls_unverified": bool(tls_unverified),
            "path": rel,
            "new": is_new,
        }
        if via:
            line["via"] = via
        if archived_at:
            line["archived_at"] = archived_at
        for key, value in extra.items():
            if value:
                line[key] = value
        manifest = self.root / frame_dir / "snapshots" / "manifest.jsonl"
        with manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        self._kept_index = None
        return path

    def recall(self, url: str, *, prefer_frame: str = "") -> KeptBody | None:
        """Newest servable body whose ``url`` or ``final_url`` equals ``url``.

        Every frame directory under this store is scanned
        (``<root>/*/snapshots/manifest.jsonl``). A withdrawn URL is skipped with
        its earlier bodies. When ``prefer_frame`` has a match, a newer copy in
        another frame does not win: re-collection should read the frame that
        is running. Newest is the greatest ``fetched_at``; the same timestamp
        keeps the later line. ``None`` means this store never kept the URL.
        """
        matched = [
            (index, frame, row)
            for index, (frame, row) in enumerate(self._servable_lines())
            if _line_matches(row, url)
        ]
        if not matched:
            return None
        if prefer_frame:
            own = [item for item in matched if item[1] == prefer_frame]
            if own:
                matched = own
        matched.sort(key=lambda item: (_fetched_at(item[2]), item[0]), reverse=True)
        for _index, frame, row in matched:
            content = self._body_of(frame, row)
            if content is None:
                continue
            return _kept_body(frame, row, content)
        return None

    def _servable_lines(self) -> list[tuple[str, dict]]:
        """``(frame, line)`` for every servable manifest line, in file order."""
        if self._kept_index is not None:
            return self._kept_index
        lines: list[tuple[str, dict]] = []
        if self.root.is_dir():
            for manifest in sorted(self.root.glob("*/snapshots/manifest.jsonl")):
                if not manifest.is_file():
                    continue
                frame = manifest.parent.parent.name
                for row in servable_rows(_read_manifest(manifest)):
                    lines.append((frame, row))
        self._kept_index = lines
        return lines

    def _body_of(self, frame: str, row: dict) -> bytes | None:
        """Bytes for one manifest line, or ``None`` when the file is not in this frame."""
        rel = row.get("path")
        if not isinstance(rel, str) or not rel:
            return None
        path = self._resolve_stored(self.root / frame / "snapshots", frame, rel)
        if path is None:
            return None
        return path.read_bytes()

    def _sha_index_for(self, snapshots: Path) -> dict[str, str]:
        """Full sha256 → path from every manifest line. The file is not rewritten."""
        key = snapshots.resolve()
        cached = self._sha_index.get(key)
        if cached is not None:
            return cached
        idx: dict[str, str] = {}
        manifest = snapshots / "manifest.jsonl"
        if manifest.is_file():
            for raw in manifest.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                sha = row.get("sha256")
                rel = row.get("path")
                if (
                    isinstance(sha, str)
                    and len(sha) == 64
                    and all(c in "0123456789abcdefABCDEF" for c in sha)
                    and isinstance(rel, str)
                    and rel
                ):
                    idx[sha.lower()] = rel
        self._sha_index[key] = idx
        return idx

    def _locate(
        self, snapshots: Path, frame_dir: str, sha: str, suffix: str, content: bytes
    ) -> tuple[Path, bool]:
        """Find bytes by the full sha256 on a manifest line, or write them under the hash.

        A filename that merely starts with the same 10 characters is not a match.
        Returns ``(path, is_new)``.
        """
        idx = self._sha_index_for(snapshots)
        rel = idx.get(sha)
        if rel:
            candidate = self._resolve_stored(snapshots, frame_dir, rel)
            if candidate is not None:
                return candidate, False
        folder = snapshots / "sha256" / sha[:2]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{sha}{suffix}"
        if path.is_file():
            return path, False
        path.write_bytes(content)
        return path, True

    def _resolve_stored(self, snapshots: Path, frame_dir: str, rel: str) -> Path | None:
        """Resolve a manifest path and refuse anything outside this frame's snapshots directory.

        New lines store a path relative to the archive root (``<frame>/snapshots/...``).
        Older lines store one relative to the frame directory (``snapshots/...``).
        """
        if not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
            return None
        for candidate in (self.root / rel, self.root / frame_dir / rel):
            if candidate.is_file() and _inside(snapshots, candidate):
                return candidate
        return None


def _read_manifest(path: Path) -> list[dict]:
    """Manifest lines that parse as objects. A broken line is skipped."""
    rows: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _line_matches(row: dict, url: str) -> bool:
    """True when the line's requested URL or final URL is exactly ``url``."""
    if not url:
        return False
    return url == row.get("url") or url == row.get("final_url")


def _fetched_at(row: dict) -> str:
    value = row.get("fetched_at")
    return value if isinstance(value, str) else ""


def _stored_status(row: dict) -> int:
    """Status stored on the line. A missing status is 0, not a guessed 200."""
    status = row.get("status")
    if isinstance(status, bool):
        return 0
    if isinstance(status, int):
        return status
    if isinstance(status, str):
        text = status.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return 0


def _stored_text(row: dict, key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def _kept_body(frame: str, row: dict, content: bytes) -> KeptBody:
    return KeptBody(
        frame=frame,
        url=_stored_text(row, "url"),
        final_url=_stored_text(row, "final_url"),
        status=_stored_status(row),
        content=content,
        content_type=_stored_text(row, "content_type"),
        fetched_at=_fetched_at(row),
        robots=_stored_text(row, "robots") or ROBOTS_NOT_CHECKED,
        tls_unverified=bool(row.get("tls_unverified")),
        robots_tls_unverified=bool(row.get("robots_tls_unverified")),
    )


def _inside(folder: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def _frame_dir(frame: str) -> str:
    if not frame or frame in {".", ".."} or "/" in frame or "\\" in frame or "\x00" in frame:
        raise ValueError(f"invalid frame directory: {frame!r}")
    return frame


def _extension(content: bytes, content_type: str, ext: str | None) -> str:
    if ext:
        return ext if ext.startswith(".") else f".{ext}"
    if content.startswith(b"%PDF-"):
        return ".pdf"
    if content[:1] in (b"{", b"["):
        return ".json"
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind == "application/pdf":
        return ".pdf"
    if kind == "application/json":
        return ".json"
    if kind.startswith("text/plain"):
        return ".txt"
    return ".html"
