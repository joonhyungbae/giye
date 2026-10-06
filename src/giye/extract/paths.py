# SPDX-License-Identifier: AGPL-3.0-only
"""Map a stored ``data/...`` path onto the configured data directory.

A stored ``snapshot_path`` looks like ``data/raw/cv/...`` (no file suffix) and
is resolved against the data root, so the ledger can move with ``[paths] data``.
``giye.normalize`` reads ``<snapshot>.txt`` the same way. An absolute path is kept.
"""

from __future__ import annotations

from pathlib import Path

from giye.config import Config


def resolve_stored(config: Config, stored: str) -> Path:
    """``data/raw/cv/a.txt`` → ``<config.data>/raw/cv/a.txt``."""
    path = Path(stored)
    if path.is_absolute():
        return path
    parts = path.parts
    if parts and parts[0] == "data":
        return config.data.joinpath(*parts[1:])
    return config.data / path


def stored_name(config: Config, path: Path) -> str:
    """Inverse of ``resolve_stored``: ``data/<path under the data directory>``."""
    inside = path.resolve().relative_to(config.data.resolve())
    return Path("data", *inside.parts).as_posix()


def cv_text_path(config: Config, source: dict[str, str]) -> Path | None:
    """``<snapshot_path>.txt`` of a ``cv_sources`` row, or None when the row has no snapshot."""
    stored = (source.get("snapshot_path") or "").strip()
    return resolve_stored(config, stored + ".txt") if stored else None


def verified_cv_text(config: Config, source: dict[str, str], *, errors: str = "strict") -> str | None:
    """The kept text of a CV source, checked against its ``content_sha256``; None when not on disk.

    ``content_sha256`` is the whitespace-insensitive fingerprint of the text
    (``giye.extract.text.fingerprint``) written when the page was pulled. A
    text that no longer has that fingerprint raises
    :class:`~giye.collect.snapshot.SnapshotIntegrityError` naming the file.
    Why: the grounding check and the extraction read this text as the
    evidence for a published row, so an edited text could publish an
    invented row without any error. A row whose value is not a 64-character
    hex digest (a legacy row) is not compared, and neither is a text that is
    empty after whitespace (its fingerprint was taken from the raw body).
    """
    from giye.collect.snapshot import SnapshotIntegrityError, _digest
    from giye.extract.text import fingerprint

    path = cv_text_path(config, source)
    if path is None or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors=errors)
    expected = _digest(source.get("content_sha256"))
    if expected and text.strip():
        actual = fingerprint(text)
        if actual != expected:
            raise SnapshotIntegrityError(
                f"kept CV text {path} does not match cv_sources {source.get('source_id') or '?'}: "
                f"fingerprint {actual}, content_sha256 says {expected}"
            )
    return text
