# SPDX-License-Identifier: AGPL-3.0-only
"""Field vocabulary loaded from a field file.

Programme names, event phrases, team-word lists, tag vocabularies, and the
ring's edition aliases live in the archive's field file (the path in
``[paths] field``). The Korean media-art field file is shipped as package data
at ``giye/fields/korean-media-art/field.toml`` so a regular (non-editable)
install finds it too. ``[extract] prompt`` names the field's CV extraction
prompt, relative to the field file; without it the packaged default is used. The package does not compile one field's
programmes into the rules. An empty tag list inherits the shipped Korean
media-art vocabulary so a small demo file can omit it; ``[tags] inherit = false``
keeps the lists empty. Event patterns, frame families, and ring aliases are
never inherited: those are the field's own programmes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class EditionAlias:
    """Membership code → registry frame and edition, when that frame is present.

    A declared row, not a branch in the resolver. ``reason`` says why the
    membership code and the registry code differ.
    """

    membership: str
    frame: str
    edition: str
    reason: str = ""


@dataclass(frozen=True)
class RimFamily:
    """One programme family on the ring.

    ``prefix`` matches that code and any ``prefix-…`` code (``SYNFLD`` and
    ``SYNFLD-2019``). ``label_ko`` and ``label_en``, when set, replace the
    registry's shortened name.
    """

    prefix: str
    family: str
    label_ko: str = ""
    label_en: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Field:
    """One field's constants. ``source`` is the file they were read from."""

    source: Path | None = None
    # E2. Frame-code prefix → regex. Order is first-match order.
    event_patterns: tuple[tuple[str, str], ...] = ()
    # E4 and the ring's team credit. Production writes ``팀: <name>``.
    team_prefix: str = "팀:"
    # T1. Empty means the shipped Korean media-art list, unless inherit_tags is false.
    team_words: str = ""
    # A1. After a trailing -YYYY is removed: startswith prefix, then exact base.
    family_prefixes: tuple[tuple[str, str], ...] = ()
    family_exact: tuple[tuple[str, str], ...] = ()
    inherit_tags: bool = True
    region_map: tuple[tuple[str, str], ...] = ()
    home_pattern: str = ""
    region_fallback: str = ""
    medium_guess: tuple[tuple[str, str], ...] = ()
    medium_words: tuple[tuple[str, str], ...] = ()
    screening_tag: str = ""
    medium_min_rows: int = 2
    edition_aliases: tuple[EditionAlias, ...] = ()
    rim_families: tuple[RimFamily, ...] = ()
    # CV extraction prompt file. None is the packaged default (giye/extract/prompts/).
    extract_prompt: Path | None = None

    def resolved(self) -> Field:
        """Copy tag lists from the shipped field when this file left them empty.

        Event patterns, attachment families, and ring aliases stay as written.
        The shipped file itself is returned unchanged.
        """
        if not self.inherit_tags:
            return self
        base = shipped_field()
        if self.source is not None and base.source is not None and self.source == base.source:
            return self
        return replace(
            self,
            team_words=self.team_words or base.team_words,
            region_map=self.region_map or base.region_map,
            home_pattern=self.home_pattern or base.home_pattern,
            region_fallback=self.region_fallback or base.region_fallback,
            medium_guess=self.medium_guess or base.medium_guess,
            medium_words=self.medium_words or base.medium_words,
            screening_tag=self.screening_tag or base.screening_tag,
            medium_min_rows=self.medium_min_rows if self.medium_words else base.medium_min_rows,
        )

    def compiled_team_words(self) -> re.Pattern[str]:
        """T1 team-word pattern. An empty list matches nothing when inheritance is off."""
        text = self.resolved().team_words
        if not text:
            return re.compile(r"(?!)")
        return re.compile(text, re.IGNORECASE)


_SHIPPED: Field | None = None


def shipped_field_path() -> Path:
    """The packaged ``giye/fields/korean-media-art/field.toml``.

    Resolved through ``importlib.resources`` rather than a path relative to the
    source tree: after ``pip install .`` the package sits in site-packages and
    the repository root is not next to it.
    """
    ref = resources.files("giye") / "fields" / "korean-media-art" / "field.toml"
    return Path(str(ref))


def shipped_field() -> Field:
    """The Korean media-art vocabulary shipped with the package. Cached."""
    global _SHIPPED
    if _SHIPPED is None:
        _SHIPPED = load_field(shipped_field_path())
    return _SHIPPED


def load_field(path: str | Path) -> Field:
    """Read a field file. Relative paths are used as given; the caller resolves them."""
    path = Path(path)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: field file must be a table")
    attach = _table(raw.get("attach") or {}, f"{path}: [attach]")
    resolve = _table(raw.get("resolve") or {}, f"{path}: [resolve]")
    tags = _table(raw.get("tags") or {}, f"{path}: [tags]")
    rim = raw.get("rim") or {}
    if not isinstance(rim, dict):
        raise TypeError(f"{path}: [rim] must be a table")
    extract = _table(raw.get("extract") or {}, f"{path}: [extract]")
    prompt = _text(extract.get("prompt", ""), f"{path}: [extract] prompt")
    prompt_file = (path.parent / prompt).resolve() if prompt else None
    if prompt_file is not None and not prompt_file.is_file():
        raise FileNotFoundError(f"{path}: [extract] prompt not found: {prompt_file}")
    return Field(
        source=path.resolve(),
        event_patterns=_pairs(resolve.get("events") or {}, f"{path}: [resolve.events]"),
        team_prefix=_text(resolve.get("team_prefix", "팀:"), f"{path}: [resolve] team_prefix"),
        team_words=_text(resolve.get("team_words", ""), f"{path}: [resolve] team_words"),
        family_prefixes=_prefix_pairs(attach.get("prefix") or [], f"{path}: [[attach.prefix]]"),
        family_exact=_pairs(attach.get("exact") or {}, f"{path}: [attach.exact]"),
        inherit_tags=bool(tags.get("inherit", False)) if "inherit" in tags else True,
        region_map=_needle_tags(tags.get("region") or [], f"{path}: [[tags.region]]"),
        home_pattern=_text(tags.get("home_pattern", ""), f"{path}: [tags] home_pattern"),
        region_fallback=_text(tags.get("fallback", ""), f"{path}: [tags] fallback"),
        medium_guess=_pattern_tags(tags.get("medium_guess") or [], f"{path}: [[tags.medium_guess]]"),
        medium_words=_pattern_tags(tags.get("medium") or [], f"{path}: [[tags.medium]]"),
        screening_tag=_text(tags.get("screening_tag", ""), f"{path}: [tags] screening_tag"),
        medium_min_rows=_positive_int(tags.get("medium_min_rows", 2), f"{path}: [tags] medium_min_rows"),
        edition_aliases=_aliases(rim.get("alias") or [], f"{path}: [[rim.alias]]"),
        rim_families=_families(rim.get("family") or [], f"{path}: [[rim.family]]"),
        extract_prompt=prompt_file,
    )


def frame_family(code: str, field: Field) -> str:
    """Programme lineage for rule A1.

    A trailing ``-YYYY`` is removed. A configured prefix (one family for every
    code that starts with it) wins, then an exact base, then the base itself.
    Prefixes are the field file's, in file order.
    """
    base = re.sub(r"-\d{4}$", "", code or "")
    for prefix, family in field.family_prefixes:
        if prefix and base.startswith(prefix):
            return family
    for name, family in field.family_exact:
        if base == name:
            return family
    return base


def rim_family(code: str, field: Field) -> str:
    """Ring family (R5). Exact code or ``prefix-…``. No configured prefix leaves the code."""
    for item in field.rim_families:
        if code == item.prefix or code.startswith(item.prefix + "-"):
            return item.family
    return code


def rim_label(family: str, field: Field) -> tuple[str, str] | None:
    """Declared Korean and English labels for a ring family, or None."""
    for item in field.rim_families:
        if item.family == family and (item.label_ko or item.label_en):
            return item.label_ko or family, item.label_en or family
    return None


def edition_alias(mem_code: str, registry: set[str] | list[str], field: Field) -> tuple[str, str] | None:
    """Declared membership alias, when the target frame is in the registry."""
    present = set(registry)
    for alias in field.edition_aliases:
        if mem_code == alias.membership and alias.frame in present:
            return alias.frame, alias.edition
    return None


def _table(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a table")
    return value


def _text(value: object, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TypeError(f"{label} must be an integer ≥ 1")
    return value


def _pairs(value: object, label: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a table of key = string")
    pairs: list[tuple[str, str]] = []
    for key, pattern in value.items():
        if not isinstance(pattern, str):
            raise TypeError(f"{label} value for {key!r} must be a string")
        pairs.append((str(key), pattern))
    return tuple(pairs)


def _prefix_pairs(value: object, label: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array of tables")
    found: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or "prefix" not in item or "family" not in item:
            raise ValueError(f"{label} needs prefix and family")
        found.append((str(item["prefix"]), str(item["family"])))
    return tuple(found)


def _needle_tags(value: object, label: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array of tables")
    found: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or "needle" not in item or "tag" not in item:
            raise ValueError(f"{label} needs needle and tag")
        found.append((str(item["needle"]), str(item["tag"])))
    return tuple(found)


def _pattern_tags(value: object, label: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array of tables")
    found: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or "pattern" not in item or "tag" not in item:
            raise ValueError(f"{label} needs pattern and tag")
        found.append((str(item["tag"]), str(item["pattern"])))
    return tuple(found)


def _aliases(value: object, label: str) -> tuple[EditionAlias, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array of tables")
    found: list[EditionAlias] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError(f"{label} entry must be a table")
        for key in ("membership", "frame", "edition"):
            if key not in item:
                raise ValueError(f"{label} needs membership, frame, and edition")
        found.append(
            EditionAlias(
                membership=str(item["membership"]),
                frame=str(item["frame"]),
                edition=str(item["edition"]),
                reason=str(item.get("reason") or ""),
            )
        )
    return tuple(found)


def _families(value: object, label: str) -> tuple[RimFamily, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array of tables")
    found: list[RimFamily] = []
    for item in value:
        if not isinstance(item, dict) or "prefix" not in item or "family" not in item:
            raise ValueError(f"{label} needs prefix and family")
        found.append(
            RimFamily(
                prefix=str(item["prefix"]),
                family=str(item["family"]),
                label_ko=str(item.get("label_ko") or ""),
                label_en=str(item.get("label_en") or ""),
                reason=str(item.get("reason") or ""),
            )
        )
    return tuple(found)
