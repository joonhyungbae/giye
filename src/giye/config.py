# SPDX-License-Identifier: AGPL-3.0-only
"""Archive configuration (``giye.toml``).

Why: paths and field-specific choices belong in one file, so the same software
builds an archive of another field or country. Paths are resolved relative to
the configuration file.

Example (see examples/demo/giye.toml)::

    [archive]
    name = "Demo field archive"
    id_prefix = "GY"
    territory = "KR"            # ISO 3166-1 alpha-2, used by frame rule F3
    languages = ["ko", "en"]

    [paths]
    data = "data"               # ledger/, raw/, processed/, site/ live under here
    frames = "frames.yml"
    field = "field.toml"        # programme phrases, team words, tags, ring aliases

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

    # Optional. Frame-code prefix = regex for rule E2. Merged with the field file;
    # a prefix already in that file replaces its pattern.
    [resolve.event_patterns]
    EXAMPLE-RESIDENCY = "example residency"

    [resolve]
    cv_dir = "fixtures/cv"      # local HTML CVs, matched to people by name
    # generic_title_records = 4 # E3: a title this many records use is not evidence; 0 = off

    # Optional. V7,V8,V9 is the default; "none" is the resolver from before those rules.
    # reference is a directory with geonames/ and countries/ (GeoNames is not shipped).
    # glossary and gazetteer replace the packaged Korean–English tables.
    # language_module defaults to giye.normalize.lang.ko_en:KoEn.
    [normalize]
    venue_name_rules = ""
    # reference = "reference"
    # glossary = "glossary.yaml"
    # gazetteer = "cities.tsv"

    # CV extraction. provider defaults to anthropic, so a file that omits it
    # keeps the hosted call. openai_compatible posts to base_url (Ollama's
    # OpenAI endpoint unless set otherwise). model defaults to claude-opus-5-5.
    # temperature is omitted unless set. cache defaults to <data>/work/cv_cache.
    # api_key_env names the variable whose value is sent as a Bearer token
    # when it is set. A local server does not need one. The default name is
    # GIYE_LLM_API_KEY. chunk_chars splits a long CV before the call. 0 is
    # off. The default is 0 for anthropic and 8000 for openai_compatible;
    # a value in the file wins. A local 32k context drops rows as the CV grows.
    [extract]
    # provider = "anthropic"
    # base_url = "http://localhost:11434/v1"
    # api_key_env = "GIYE_LLM_API_KEY"
    # chunk_chars = 8000
    # reasoning_effort = "none"
    model = "claude-opus-5-5"

    # Optional. Ledger backups older than this many days are pruned (the newest
    # backup of each file is always kept). Unset keeps every backup.
    # [ledger]
    # keep_backups_days = 90

    # Site snapshot. site_url is the public origin cited on each page.
    # Publish fails when it is unset: there is no default origin.
    # dataset_version 0.2 and citation_author "기예 Giye" match the live archive.
    # Another field sets its own title, author, and origin.
    [publish]
    site_url = "https://example.org"
    dataset_version = "0.2"
    dataset_title = "Synthetic media-art field (demo)"
    citation_author = "Example Archive"
    # Optional. Maintenance schedule stated in coverage.json (the site shows a
    # cadence line only when it is set). Declare only what the archive runs.
    # [publish.cadence]
    # weekly = "link check, site rebuild"
    # cache = "cache"
    # [[extract.sources]]
    # name_ko = "김하늘"
    # lang = "ko"
    # url = "https://cv.example.org/haneul-ko"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from giye.collect.frames import FrameRegistry


class GiyeError(Exception):
    """A problem the command prints as one line. Exit status 2. Not a bug.

    Unexpected exceptions are not this class, so they still show a traceback.
    """


class ConfigError(GiyeError):
    """``giye.toml`` or ``frames.yml`` cannot be used. The message is the line."""

from giye.field import Field, load_field

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class ExtractSource:
    """One CV location declared in ``[[extract.sources]]``.

    ``source_id`` is optional. Empty means ``CV-<ledger_id>-<lang>``. The demo
    sets it so a hand-written replay response can name the source before a
    ledger id exists.
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
    """One archive's ``giye.toml``: paths, field file, collectors, extraction, and publish.

    Relative paths are resolved from the file's directory. ``load`` builds this.
    """

    root: Path
    name: str
    id_prefix: str = "GY"
    territory: str = ""
    languages: tuple[str, ...] = ("en",)
    data: Path = Path("data")
    frames: Path = Path("frames.yml")
    # Field file (programme phrases, team words, tags, ring aliases). None uses
    # an empty field that inherits tag lists from the shipped Korean file.
    field_file: Path | None = None
    field_config: Field = field(default_factory=Field)
    # ``module:Class`` with a ``load`` classmethod. Default is Korean–English.
    language_module: str = "giye.normalize.lang.ko_en:KoEn"
    user_agent: str = "GiyeArchive/0.1 (+https://example.org/contact)"
    min_delay_s: float = 2.0
    timeout_s: float = 45.0
    robots_timeout_s: float = 20.0
    offline_roots: tuple[tuple[str, Path], ...] = ()
    collector_modules: tuple[str, ...] = ()
    # Extra E2 event patterns (frame-code prefix → regex). Merged after the field
    # file; a key here replaces that prefix's pattern.
    event_patterns: tuple[tuple[str, str], ...] = ()
    # Local HTML CVs, read by name. Extracted JSON at data/work/cv_extract/<id>.json
    # is preferred when that file exists (see giye.resolve.cv).
    cv_dir: Path | None = None
    # E3: a work title base credited to or listed by this many distinct records is
    # generic and not evidence. 0 turns the rule off. Default and its reason: giye.resolve.evidence.
    generic_title_records: int = 4
    # Optional GeoNames tree (geonames/ + countries/). Unset uses the packaged gazetteer.
    normalize_reference: Path | None = None
    # Optional replacements for the packaged Korean–English glossary and city table.
    normalize_glossary: Path | None = None
    normalize_gazetteer: Path | None = None
    # "" means V7, V8 and V9. "none" applies none of them. A comma-separated subset ablates.
    venue_name_rules: str = ""
    # CV extraction. The provider defaults to Anthropic so existing configs and
    # the demo keep the hosted call. openai_compatible calls a local server
    # without a key unless the named environment variable is set, in which
    # case that value is sent as a Bearer token. The model id defaults to
    # claude-opus-5-5. Temperature is sent only when the file sets it, so an
    # omitted key does not invent a sampling temperature.
    extract_provider: str = "anthropic"
    extract_base_url: str = "http://localhost:11434/v1"
    extract_model: str = "claude-opus-5-5"
    extract_temperature: float | None = None
    # Sent as reasoning_effort to an OpenAI-compatible server when set. "none"
    # turns off thinking on models that reason by default (qwen3.5, gemma4).
    extract_reasoning_effort: str | None = None
    # 0 sends each CV whole. ``load`` uses 8000 when the provider is
    # openai_compatible and the file omits the key. An explicit value wins.
    extract_chunk_chars: int = 0
    # False when the file omitted chunk_chars, so a CLI --provider override
    # recomputes the provider default instead of keeping the file's.
    extract_chunk_chars_explicit: bool = False
    extract_api_key_env: str = "GIYE_LLM_API_KEY"
    extract_cache: Path | None = None
    extract_allow_team: bool = False
    # Grounding check on CV rows (giye.extract.grounding); see docs/RULES.md.
    # On by default since the venue test reads the institution part (2026-10-06).
    extract_grounding: bool = True
    extract_sources: tuple[ExtractSource, ...] = ()
    # Public origin of the published site. Citations use ``<site_url>/artist/<id>``
    # and ``<site_url>/data``. Empty until the file sets it. Publish refuses to run
    # without one, so a config cannot silently cite someone else's site.
    site_url: str = ""
    # 0.2 is the published dataset version written into the site snapshot. The
    # artist page in the web app hard-codes 1.0; the snapshot uses this value
    # for both.
    dataset_version: str = "0.2"
    # Empty means the archive name. The data page can use a longer title.
    dataset_title: str = ""
    # The citation dialog writes this author string.
    citation_author: str = "기예 Giye"
    # Maintenance schedule written to coverage.json, label → what runs. Empty
    # means no cadence is published: the package itself schedules nothing.
    cadence: dict[str, str] = field(default_factory=dict)
    # Days of ledger backups to keep ([ledger] keep_backups_days). None keeps
    # every backup. The newest backup of each file is never pruned.
    keep_backups_days: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def ledger(self) -> Path:
        """``<data>/ledger``, the CSV source of truth."""
        return self.data / "ledger"

    @property
    def raw(self) -> Path:
        """``<data>/raw``, original bytes of cited URLs."""
        return self.data / "raw"

    @property
    def processed(self) -> Path:
        """``<data>/processed``, derived tables that do not edit the ledger."""
        return self.data / "processed"

    @property
    def site(self) -> Path:
        """``<data>/site``, the file snapshot the website reads."""
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


def checked_frames(config: Config) -> FrameRegistry:
    """Load ``frames.yml`` before a stage uses it.

    A file that is not a mapping with a ``frames`` list used to pass ``giye
    collect`` and then raise ``TypeError`` inside publish, explore, or render.
    The same check runs at the start of every stage that reads the file.
    """
    # Imported here: giye.collect.frames does not import this module, but a
    # top-level import would couple configuration to collection at import time.
    from giye.collect.frames import load_frames

    path = config.frames
    if not path.is_file():
        raise ConfigError(f"frames file not found: {path}")
    try:
        return load_frames(path)
    except (TypeError, ValueError, OSError) as exc:
        raise ConfigError(str(exc)) from exc


def load(path: str | Path) -> Config:
    """Read a ``giye.toml``; relative paths are taken from the file's directory."""
    path = Path(path).resolve()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    archive, paths, collect = raw.get("archive", {}), raw.get("paths", {}), raw.get("collect", {})
    # archive_fallback_for_disallowed is not a setting. A disallowed host is
    # link-only (giye.collect.evidence). A leftover key in this table is ignored.
    evidence = raw.get("evidence") or {}
    if "name" not in archive:
        raise ValueError(f"{path}: [archive] name is required")
    if not isinstance(evidence, dict):
        raise TypeError(f"{path}: [evidence] must be a table")
    resolve = raw.get("resolve") or {}
    if not isinstance(resolve, dict):
        raise TypeError(f"{path}: [resolve] must be a table")
    known = {"archive", "paths", "collect", "evidence", "resolve", "normalize", "extract", "publish", "ledger"}
    ledger = raw.get("ledger") or {}
    if not isinstance(ledger, dict):
        raise TypeError(f"{path}: [ledger] must be a table")
    publish = raw.get("publish") or {}
    if not isinstance(publish, dict):
        raise TypeError(f"{path}: [publish] must be a table")
    extract = raw.get("extract") or {}
    if not isinstance(extract, dict):
        raise TypeError(f"{path}: [extract] must be a table")
    extract_provider = _extract_provider(extract.get("provider", "anthropic"))
    normalize = raw.get("normalize") or {}
    if not isinstance(normalize, dict):
        raise TypeError(f"{path}: [normalize] must be a table")
    cv_dir = resolve.get("cv_dir")
    cv_path = None
    if cv_dir:
        cv_path = Path(str(cv_dir))
        if not cv_path.is_absolute():
            cv_path = (root / cv_path).resolve()
    frames = (root / paths.get("frames", "frames.yml")).resolve()
    return Config(
        root=root,
        name=archive["name"],
        id_prefix=archive.get("id_prefix", "GY"),
        territory=archive.get("territory", ""),
        languages=tuple(archive.get("languages", ["en"])),
        data=(root / paths.get("data", "data")).resolve(),
        frames=frames,
        field_file=_optional_path(root, paths.get("field")),
        field_config=_field_config(root, paths.get("field"), frames),
        language_module=_language_module(normalize.get("language_module", "giye.normalize.lang.ko_en:KoEn")),
        user_agent=collect.get("user_agent", "GiyeArchive/0.1 (+https://example.org/contact)"),
        min_delay_s=float(collect.get("min_delay_s", 2.0)),
        timeout_s=float(collect.get("timeout_s", 45.0)),
        robots_timeout_s=float(collect.get("robots_timeout_s", 20.0)),
        offline_roots=_offline_roots(root, collect),
        collector_modules=_collector_modules(collect),
        event_patterns=_event_patterns(resolve),
        cv_dir=cv_path,
        generic_title_records=_generic_title_records(resolve.get("generic_title_records", 4)),
        normalize_reference=_optional_path(root, normalize.get("reference")),
        normalize_glossary=_optional_path(root, normalize.get("glossary")),
        normalize_gazetteer=_optional_path(root, normalize.get("gazetteer")),
        venue_name_rules=_venue_name_rules(normalize.get("venue_name_rules", "")),
        extract_provider=extract_provider,
        extract_base_url=_extract_base_url(extract.get("base_url", "http://localhost:11434/v1")),
        extract_model=_extract_model(extract.get("model", "claude-opus-5-5")),
        extract_temperature=_extract_temperature(extract),
        extract_reasoning_effort=_extract_reasoning_effort(extract),
        extract_chunk_chars=_extract_chunk_chars(extract, extract_provider),
        extract_chunk_chars_explicit="chunk_chars" in extract,
        extract_api_key_env=_extract_api_key_env(extract.get("api_key_env", "GIYE_LLM_API_KEY")),
        extract_cache=_optional_path(root, extract.get("cache")),
        extract_allow_team=bool(extract.get("allow_team", False)),
        extract_grounding=_extract_grounding(extract),
        extract_sources=_extract_sources(extract),
        site_url=_site_url(publish.get("site_url", "")),
        dataset_version=_plain(publish.get("dataset_version", "0.2"), "0.2", "[publish] dataset_version"),
        dataset_title=_plain(publish.get("dataset_title", ""), "", "[publish] dataset_title"),
        citation_author=_plain(publish.get("citation_author", "기예 Giye"), "기예 Giye", "[publish] citation_author"),
        cadence=_cadence(publish.get("cadence")),
        keep_backups_days=_keep_backups_days(ledger.get("keep_backups_days")),
        extra={k: v for k, v in raw.items() if k not in known},
    )


def _keep_backups_days(value: object) -> int | None:
    """``[ledger] keep_backups_days``: unset keeps every backup; otherwise a whole number of days, at least 1."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TypeError("[ledger] keep_backups_days must be a whole number of days, at least 1")
    return value


def _cadence(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(item, str) for item in value.values()):
        raise TypeError("[publish.cadence] must be a table of label = string")
    return {str(key): item for key, item in value.items()}


def _site_url(value: object) -> str:
    """Public origin. Unset stays empty; ``giye publish`` then raises ``ConfigError``."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise TypeError("[publish] site_url must be a string")
    return value.rstrip("/")


def _plain(value: object, default: str, label: str) -> str:
    """A config string, or ``default`` when the key was omitted."""
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


def _generic_title_records(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError("[resolve] generic_title_records must be a non-negative integer")
    return value


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


def _extract_grounding(extract: dict) -> bool:
    value = extract.get("grounding", Config.extract_grounding)
    if not isinstance(value, bool):
        raise TypeError("[extract] grounding must be true or false")
    return value


def _extract_provider(value: object) -> str:
    """``anthropic`` is the default. A local server is ``openai_compatible``."""
    if value is None or value == "":
        return "anthropic"
    if not isinstance(value, str):
        raise TypeError("[extract] provider must be a string")
    if value not in ("anthropic", "openai_compatible"):
        raise ValueError("[extract] provider must be 'anthropic' or 'openai_compatible'")
    return value


def _extract_base_url(value: object) -> str:
    """Ollama's OpenAI-compatible prefix. The provider appends ``/chat/completions``."""
    if value is None or value == "":
        return "http://localhost:11434/v1"
    if not isinstance(value, str):
        raise TypeError("[extract] base_url must be a string")
    return value.rstrip("/")


def _extract_model(value: object) -> str:
    if value is None or value == "":
        return "claude-opus-5-5"
    if not isinstance(value, str):
        raise TypeError("[extract] model must be a string")
    return value


def default_chunk_chars(provider: str) -> int:
    """8000 for a local OpenAI-compatible server, 0 (whole CV) otherwise."""
    return 8000 if provider == "openai_compatible" else 0


def _extract_chunk_chars(extract: dict, provider: str) -> int:
    """How long one CV piece may be. ``0`` leaves the CV whole.

    A local model defaults to 8000 characters. Input plus a long JSON reply
    does not fit a 32k context, and the share of rows kept falls as the CV
    grows. An explicit value wins over that default. The split itself is a
    data rule (``giye.extract.chunk``).
    """
    if "chunk_chars" not in extract:
        return default_chunk_chars(provider)
    value = extract["chunk_chars"]
    # bool is an int subclass. A TOML true is not a character budget.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("[extract] chunk_chars must be an integer")
    if value < 0:
        raise ValueError("[extract] chunk_chars must be >= 0")
    return value


_REASONING_EFFORTS = ("none", "low", "medium", "high")


def _extract_reasoning_effort(extract: dict) -> str | None:
    """None when absent: the request carries no reasoning_effort, as before.

    A local thinking model writes its reasoning before the JSON. On one
    24 GB GPU that took about 70 s for a one-line request that answered in
    about 1 s with "none", so a long CV split into pieces is far slower.
    """
    value = extract.get("reasoning_effort")
    if value is None:
        return None
    if value not in _REASONING_EFFORTS:
        raise ValueError(f"[extract] reasoning_effort must be one of {', '.join(_REASONING_EFFORTS)}")
    return value


def _extract_temperature(extract: dict) -> float | None:
    """None when the key is absent, so the live call omits temperature and leaves the model's own default."""
    if "temperature" not in extract or extract["temperature"] is None:
        return None
    return float(extract["temperature"])


def _extract_api_key_env(value: object) -> str:
    """Name of the environment variable, not the key itself. Default ``GIYE_LLM_API_KEY``."""
    if value is None or value == "":
        return "GIYE_LLM_API_KEY"
    if not isinstance(value, str):
        raise TypeError("[extract] api_key_env must be a string")
    if any(char.isspace() for char in value):
        raise ValueError("[extract] api_key_env must be an environment variable name")
    return value


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


def _field_config(root: Path, value: object, frames: Path | None = None) -> Field:
    path = _optional_path(root, value)
    if path is None:
        return Field()
    if not path.is_file() and frames is not None:
        # A copied config often rewrites ``frames`` to an absolute path and
        # leaves ``field`` as a filename. The field file sits next to frames.yml.
        beside = frames.parent / path.name
        if beside.is_file():
            path = beside
    if not path.is_file():
        raise FileNotFoundError(f"field file not found: {path}")
    return load_field(path)


def _language_module(value: object) -> str:
    """``package.module:Class``. The class provides ``load``."""
    if value is None or value == "":
        return "giye.normalize.lang.ko_en:KoEn"
    if not isinstance(value, str) or ":" not in value or value.startswith(":") or value.endswith(":"):
        raise ValueError("[normalize] language_module must be 'package.module:Class'")
    return value


def _event_patterns(resolve: dict) -> tuple[tuple[str, str], ...]:
    raw = resolve.get("event_patterns") or {}
    if not isinstance(raw, dict):
        raise TypeError("[resolve.event_patterns] must be a table of frame prefix = regex")
    return tuple((str(key), str(pattern)) for key, pattern in raw.items())
