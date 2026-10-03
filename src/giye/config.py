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

    # Optional. V7,V8,V9 is the default; "none" is the resolver from before those rules.
    # reference is a directory with geonames/ and countries/ (GeoNames is not shipped).
    # glossary and gazetteer replace the packaged Korean–English tables.
    [normalize]
    venue_name_rules = ""
    # reference = "reference"
    # glossary = "glossary.yaml"
    # gazetteer = "cities.tsv"

    # CV extraction. model defaults to the production id. temperature is omitted
    # unless set. cache defaults to <data>/work/cv_cache. sources are optional.
    [extract]
    model = "claude-opus-5"

    # Site snapshot. site_url is the public origin cited on each page.
    # dataset_version 0.2 and citation_author "기예 Giye" match the live archive.
    # Another field sets its own title, author, and origin.
    [publish]
    site_url = "https://example.org"
    dataset_version = "0.2"
    dataset_title = "Synthetic media-art field (demo)"
    citation_author = "Example Archive"
    # cache = "cache"
    # [[extract.sources]]
    # name_ko = "김하늘"
    # lang = "ko"
    # url = "https://cv.example.org/haneul-ko"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class ExtractSource:
    """One CV location declared in ``[[extract.sources]]``.

    ``source_id`` is optional. Empty means ``CV-<ledger_id>-<lang>``, the
    production id. The demo sets it so a hand-written replay response can name
    the source before a ledger id exists.
    """

    url: str
    lang: str
    ledger_id: str = ""
    name_ko: str = ""
    name_en: str = ""
    kind: str = ""
    note: str = ""
    fetch_url: str = ""
    source_id: str = ""


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
    # Optional GeoNames tree (geonames/ + countries/). Unset uses the packaged gazetteer.
    normalize_reference: Path | None = None
    # Optional replacements for the packaged Korean–English glossary and city table.
    normalize_glossary: Path | None = None
    normalize_gazetteer: Path | None = None
    # "" means V7, V8 and V9. "none" applies none of them. A comma-separated subset ablates.
    venue_name_rules: str = ""
    # CV extraction. The model id is the production default. Temperature is sent
    # only when the file sets it; production omitted the parameter.
    extract_model: str = "claude-opus-5"
    extract_temperature: float | None = None
    extract_cache: Path | None = None
    extract_allow_team: bool = False
    extract_sources: tuple[ExtractSource, ...] = ()
    # Public origin of the published site. Citations use ``<site_url>/artist/<id>``
    # and ``<site_url>/data``. The default is the reference deployment.
    site_url: str = "https://giye.org"
    # Production ``dataset_versions.json`` is version 0.2. The artist page in the
    # web app hard-codes 1.0; the snapshot uses this value for both.
    dataset_version: str = "0.2"
    # Empty means the archive name. Production's data page uses a longer title.
    dataset_title: str = ""
    # Production CiteDialog always writes this author string.
    citation_author: str = "기예 Giye"
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
    known = {"archive", "paths", "collect", "evidence", "resolve", "normalize", "extract", "publish"}
    publish = raw.get("publish") or {}
    if not isinstance(publish, dict):
        raise TypeError(f"{path}: [publish] must be a table")
    extract = raw.get("extract") or {}
    if not isinstance(extract, dict):
        raise TypeError(f"{path}: [extract] must be a table")
    normalize = raw.get("normalize") or {}
    if not isinstance(normalize, dict):
        raise TypeError(f"{path}: [normalize] must be a table")
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
        normalize_reference=_optional_path(root, normalize.get("reference")),
        normalize_glossary=_optional_path(root, normalize.get("glossary")),
        normalize_gazetteer=_optional_path(root, normalize.get("gazetteer")),
        venue_name_rules=_venue_name_rules(normalize.get("venue_name_rules", "")),
        extract_model=_extract_model(extract.get("model", "claude-opus-5")),
        extract_temperature=_extract_temperature(extract),
        extract_cache=_optional_path(root, extract.get("cache")),
        extract_allow_team=bool(extract.get("allow_team", False)),
        extract_sources=_extract_sources(extract),
        site_url=_site_url(publish.get("site_url", "https://giye.org")),
        dataset_version=_plain(publish.get("dataset_version", "0.2"), "0.2", "[publish] dataset_version"),
        dataset_title=_plain(publish.get("dataset_title", ""), "", "[publish] dataset_title"),
        citation_author=_plain(publish.get("citation_author", "기예 Giye"), "기예 Giye", "[publish] citation_author"),
        extra={k: v for k, v in raw.items() if k not in known},
    )


def _site_url(value: object) -> str:
    if value is None or value == "":
        return "https://giye.org"
    if not isinstance(value, str):
        raise TypeError("[publish] site_url must be a string")
    return value.rstrip("/")


def _plain(value: object, default: str, label: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    return value


def _optional_path(root: Path, value: object) -> Path | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TypeError("a normalize path must be a string")
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _venue_name_rules(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise TypeError("[normalize] venue_name_rules must be a string or a list of strings")
        return ",".join(value)
    if not isinstance(value, str):
        raise TypeError("[normalize] venue_name_rules must be a string or a list of strings")
    return value


def _extract_model(value: object) -> str:
    if value is None or value == "":
        return "claude-opus-5"
    if not isinstance(value, str):
        raise TypeError("[extract] model must be a string")
    return value


def _extract_temperature(extract: dict) -> float | None:
    """None when the key is absent, so the live call omits temperature as production did."""
    if "temperature" not in extract or extract["temperature"] is None:
        return None
    return float(extract["temperature"])


def _extract_sources(extract: dict) -> tuple[ExtractSource, ...]:
    raw = extract.get("sources") or []
    if not isinstance(raw, list):
        raise TypeError("[extract.sources] must be an array of tables")
    sources: list[ExtractSource] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("each [extract.sources] entry must be a table")
        if "url" not in item or "lang" not in item:
            raise ValueError("an extract source needs url and lang")
        lang = str(item["lang"])
        if lang not in ("ko", "en", "mixed"):
            raise ValueError(f"extract source lang must be ko, en, or mixed (got {lang})")
        sources.append(
            ExtractSource(
                url=str(item["url"]),
                lang=lang,
                ledger_id=str(item.get("ledger_id") or ""),
                name_ko=str(item.get("name_ko") or ""),
                name_en=str(item.get("name_en") or ""),
                kind=str(item.get("kind") or ""),
                note=str(item.get("note") or ""),
                fetch_url=str(item.get("fetch_url") or ""),
                source_id=str(item.get("source_id") or ""),
            )
        )
    return tuple(sources)


def _event_patterns(resolve: dict) -> tuple[tuple[str, str], ...]:
    raw = resolve.get("event_patterns") or {}
    if not isinstance(raw, dict):
        raise TypeError("[resolve.event_patterns] must be a table of frame prefix = regex")
    return tuple((str(key), str(pattern)) for key, pattern in raw.items())
