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
    timeout_s = 45
    robots_timeout_s = 20
    collector_modules = ["collectors.py"]

    # Optional. URL prefix = directory of local pages. Reads stay offline but still
    # check that directory's robots.txt. Used by the demo.
    [collect.offline_roots]
    "https://example.org" = "fixtures"

    [evidence]
    # Production used the Internet Archive when robots.txt disallowed a host.
    # The public release does not, unless this is set true. Default false.
    archive_fallback_for_disallowed = false

    # Optional. Frame-code prefix = regex for rule E2. Added to the production
    # programme patterns; a prefix already in that list replaces its pattern.
    [resolve.event_patterns]
    EXAMPLE-RESIDENCY = "example residency"

    [resolve]
    cv_dir = "fixtures/cv"      # local HTML CVs, matched to people by name
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
    user_agent: str = "GiyeArchive/0.1 (+https://example.org/contact)"
    min_delay_s: float = 2.0
    timeout_s: float = 45.0
    robots_timeout_s: float = 20.0
    # When false (the public default), a robots.txt disallow is recorded and not
    # replaced with an Internet Archive capture. See giye.collect.evidence.
    archive_fallback_for_disallowed: bool = False
    offline_roots: tuple[tuple[str, Path], ...] = ()
    collector_modules: tuple[str, ...] = ()
    # Extra E2 event patterns (frame-code prefix → regex). Production patterns stay
    # in giye.resolve.evidence; a key here replaces that prefix's pattern.
    event_patterns: tuple[tuple[str, str], ...] = ()
    # Local HTML CVs, read by name. Production reads data/work/cv_extract/<id>.json.
    cv_dir: Path | None = None
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

    @property
    def work(self) -> Path:
        """Collector CSVs and evidence status. Follows the configured data directory."""
        return self.data / "work"


def _offline_roots(root: Path, collect: dict) -> tuple[tuple[str, Path], ...]:
    raw_roots = collect.get("offline_roots") or {}
    if not isinstance(raw_roots, dict):
        raise TypeError("[collect.offline_roots] must be a table of URL prefix = directory")
    pairs: list[tuple[str, Path]] = []
    for prefix, dest in raw_roots.items():
        directory = Path(str(dest))
        if not directory.is_absolute():
            directory = root / directory
        pairs.append((str(prefix).rstrip("/"), directory.resolve()))
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return tuple(pairs)


def _collector_modules(collect: dict) -> tuple[str, ...]:
    modules = collect.get("collector_modules") or []
    if isinstance(modules, str):
        modules = [modules]
    if not isinstance(modules, list) or not all(isinstance(item, str) for item in modules):
        raise TypeError("[collect] collector_modules must be a list of paths")
    return tuple(modules)


def load(path: str | Path) -> Config:
    """Read a ``giye.toml``; relative paths are taken from the file's directory."""
    path = Path(path).resolve()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    archive, paths, collect = raw.get("archive", {}), raw.get("paths", {}), raw.get("collect", {})
    evidence = raw.get("evidence") or {}
    if "name" not in archive:
        raise ValueError(f"{path}: [archive] name is required")
    if not isinstance(evidence, dict):
        raise TypeError(f"{path}: [evidence] must be a table")
    resolve = raw.get("resolve") or {}
    if not isinstance(resolve, dict):
        raise TypeError(f"{path}: [resolve] must be a table")
    known = {"archive", "paths", "collect", "evidence", "resolve"}
    cv_dir = resolve.get("cv_dir")
    cv_path = None
    if cv_dir:
        cv_path = Path(str(cv_dir))
        if not cv_path.is_absolute():
            cv_path = (root / cv_path).resolve()
    return Config(
        root=root,
        name=archive["name"],
        id_prefix=archive.get("id_prefix", "GY"),
        territory=archive.get("territory", ""),
        languages=tuple(archive.get("languages", ["en"])),
        data=(root / paths.get("data", "data")).resolve(),
        frames=(root / paths.get("frames", "frames.yml")).resolve(),
        user_agent=collect.get("user_agent", "GiyeArchive/0.1 (+https://example.org/contact)"),
        min_delay_s=float(collect.get("min_delay_s", 2.0)),
        timeout_s=float(collect.get("timeout_s", 45.0)),
        robots_timeout_s=float(collect.get("robots_timeout_s", 20.0)),
        archive_fallback_for_disallowed=bool(evidence.get("archive_fallback_for_disallowed", False)),
        offline_roots=_offline_roots(root, collect),
        collector_modules=_collector_modules(collect),
        event_patterns=_event_patterns(resolve),
        cv_dir=cv_path,
        extra={k: v for k, v in raw.items() if k not in known},
    )


def _event_patterns(resolve: dict) -> tuple[tuple[str, str], ...]:
    raw = resolve.get("event_patterns") or {}
    if not isinstance(raw, dict):
        raise TypeError("[resolve.event_patterns] must be a table of frame prefix = regex")
    return tuple((str(key), str(pattern)) for key, pattern in raw.items())
