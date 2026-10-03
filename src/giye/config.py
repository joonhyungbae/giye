# SPDX-License-Identifier: MIT
"""Archive configuration (``giye.toml``).

Why: the production archive fixed its paths and field-specific choices in code. Here every
archive declares them in one file, so the same software builds an archive of another field or
country. Paths are resolved relative to the configuration file.

Example (see examples/demo/giye.toml)::

    [archive]
    name = "Demo field archive"
    id_prefix = "GY"
    territory = "KR"            # ISO 3166-1 alpha-2, used by frame rule F3
    languages = ["ko", "en"]

    [paths]
    data = "data"               # ledger/, raw/, processed/, site/ live under here
    frames = "frames.yml"

    [collect]
    user_agent = "GiyeArchive/0.1 (+https://example.org/about)"
    min_delay_s = 2.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class Config:
    root: Path
    name: str
    id_prefix: str = "GY"
    territory: str = ""
    languages: tuple[str, ...] = ("en",)
    data: Path = Path("data")
    frames: Path = Path("frames.yml")
    user_agent: str = "GiyeArchive/0.1"
    min_delay_s: float = 2.0
    extra: dict = field(default_factory=dict)

    @property
    def ledger(self) -> Path:
        return self.data / "ledger"

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def processed(self) -> Path:
        return self.data / "processed"

    @property
    def site(self) -> Path:
        return self.data / "site"


def load(path: str | Path) -> Config:
    """Read a ``giye.toml``; relative paths are taken from the file's directory."""
    path = Path(path).resolve()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    archive, paths, collect = raw.get("archive", {}), raw.get("paths", {}), raw.get("collect", {})
    if "name" not in archive:
        raise ValueError(f"{path}: [archive] name is required")
    return Config(
        root=root,
        name=archive["name"],
        id_prefix=archive.get("id_prefix", "GY"),
        territory=archive.get("territory", ""),
        languages=tuple(archive.get("languages", ["en"])),
        data=(root / paths.get("data", "data")).resolve(),
        frames=(root / paths.get("frames", "frames.yml")).resolve(),
        user_agent=collect.get("user_agent", "GiyeArchive/0.1"),
        min_delay_s=float(collect.get("min_delay_s", 2.0)),
        extra={k: v for k, v in raw.items() if k not in {"archive", "paths", "collect"}},
    )
