# SPDX-License-Identifier: MIT
"""Content-addressed copies of bytes a collector or the evidence pass fetched.

Ported from ``scripts/collectors/snapshot.py``. Production stored
``data/raw/<frame>/snapshots/<url-stem>__<sha256 prefix><ext>`` and appended one
``manifest.jsonl`` line per fetch, even when the bytes were already on disk.
Identical content is written once; a later fetch only adds a manifest line
(``new`` is false and ``path`` points at the existing object).

The public path is the SHA-256 itself (``<frame>/snapshots/sha256/<sha[:2]>/<sha><ext>``)
so the file name does not depend on the URL. The manifest carries the fields the
pipeline needs to cite the fetch: url, final_url, status, fetched_at (UTC), sha256,
bytes, content_type, collector, run_id, tls_unverified.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

# Production SnapshotSession and archive_evidence both skip bodies above 40 MB.
MAX_BYTES = 40 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SnapshotStore:
    """Keep response bodies under ``root`` (the archive's ``data/raw`` directory)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

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
        folder = self.root / frame_dir / "snapshots" / "sha256" / sha[:2]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{sha}{suffix}"
        existed = path.is_file()
        if not existed:
            path.write_bytes(content)
        line = {
            "url": url,
            "final_url": final_url or url,
            "status": status,
            "fetched_at": utc_now(),
            "sha256": sha,
            "bytes": len(content),
            "content_type": content_type,
            "collector": collector,
            "run_id": run_id,
            "tls_unverified": bool(tls_unverified),
            "path": path.relative_to(self.root).as_posix(),
            "new": not existed,
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
