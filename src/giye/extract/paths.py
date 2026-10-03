# SPDX-License-Identifier: MIT
"""Map a stored ``data/...`` path onto the configured data directory.

Production stores ``snapshot_path`` as ``data/raw/cv/...`` (no file suffix) and
resolves it against the data root. ``giye.normalize`` reads ``<snapshot>.txt``
the same way. An absolute path is kept.
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
