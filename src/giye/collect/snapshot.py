# SPDX-License-Identifier: MIT
"""Content-addressed copies of bytes a collector or the evidence pass fetched.

Ported from ``scripts/collectors/snapshot.py``. Production stored
``data/raw/<frame>/snapshots/<url-stem>__<sha256 prefix><ext>`` and appended one
``manifest.jsonl`` line per fetch, even when the bytes were already on disk.
Identical content is written once; a later fetch only adds a manifest line
(``new`` is false and ``path`` points at the existing object).

The public path is the SHA-256 itself (``<frame>/snapshots/sha256/<sha[:2]>/<sha><ext>``)
so the file name does not depend on the URL. Lookup does not use that name. A later
fetch of the same bytes is found by the full sha256 on an existing manifest line
(production stopped trusting the 10-character prefix in the filename, because two
objects can share it). A file that only shares that prefix is not reused.

New lines always record ``status``, ``final_url``, ``content_type``, and ``robots``
(``allowed``, ``unavailable_allowed``, ``disallowed``, ``unreachable_disallowed``,
or ``not_checked``). ``not_checked`` is only for bytes the caller already holds and
did not just fetch. A refused hop is not stored: there are no bytes.

Manifest version 1 does not keep the original response headers. Version 2 would be
the first to store them. Until then a WARC export reconstructs a status line and
Content-Type from these fields and the stored body (see ``giye.export``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

# Production SnapshotSession and archive_evidence both skip bodies above 40 MB.
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


class SnapshotStore:
    """Keep response bodies under ``root`` (the archive's ``data/raw`` directory)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        # snapshots directory → full sha256 → path as stored on the manifest line.
        self._sha_index: dict[Path, dict[str, str]] = {}

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
        ``MAX_BYTES`` (production did not archive those responses).
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
        return path

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
