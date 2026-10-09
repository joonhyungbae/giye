# SPDX-License-Identifier: AGPL-3.0-only
"""Sampling-frame registry (``frames.yml``) and eligibility F1–F5.

One judgement per programme is recorded in ``frames.yml`` and published on the
sampling-frame page. The five criteria were fixed before any candidate
programme was examined (adopted 2026-09-20), so inclusion is not a decision made
after seeing who was selected.

The software checks that a judgement is complete. It does not re-decide F1–F5:
the text in the file is the record (docs/RULES.md). Collection and publication
then admit only ``included`` and ``adjacent``. An ``adjacent`` frame is published
as an adjacent strand. ``excluded``, ``planned``, ``no_public_roster``, and any
other decision are not collected, and their memberships are not published. The
coverage files still list every frame with its decision.

F1 purpose. The programme's own public description states a purpose within the
field. In the Korean media-art archive that is art–technology (or art–science)
convergence, media art, new media, or digital art. The programme's document is
the evidence, not the institution's reputation.

F2 cohort. Participants are fixed by an open call, jury, selection, award, or
residency. A curated or rented exhibition does not qualify.

F3 territory. The programme is held in the archive's configured territory.
An archive whose territory is Korea stored the sentence under ``f3_korea``.
This loader accepts ``f3_territory`` and, as an alias, ``f3_korea``
(docs/RULES.md, F3).

F4 roster. The participant list is verifiable in a public record (official page,
catalogue, or press release).

F5 period. The programme has editions since 2010 and recurs at least twice. A
single edition counts only when it represents the field that year. The year
bound is the census rule and is stored in the frame's own sentence; it is
not hard-coded here (docs/RULES.md, F5).

Coverage is members recorded / max(declared roster size, members recorded)
(the ratio the site prints as a percentage). The declared size is
``roster_size_declared``: a size the programme states itself (its own page,
catalogue, or press release), cited by ``roster_size_source``. ``roster_count``
is not a declared size: in practice it is written from what the collector
found, so dividing by it would give 100% by construction. Coverage is
``None`` (unknown) when no size is declared independently.

A programme usually states its size once per edition ("12 artists selected
in 2024"), not for its whole history. ``roster_size_declared_by_edition``
maps an edition (the year in a membership code such as ``CODE-2024``) to
``{size, source}``. Edition coverage is that edition's members recorded /
max(declared, recorded). Edition sizes are not summed into the frame-level
size: the frame counts each person once across editions, while edition sizes
count a returning person in every edition, so the sum is a different unit.

``operators`` records an organiser or operator named on the programme's own
archived page. The loader checks the role, the http(s) source, and that the
quote contains the name. Country fill (G13) reads the names; this module
does not decide a country.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

# ``planned`` and ``no_public_roster`` are recorded verdicts, not collection
# decisions (docs/RULES.md). The sampling-frame page still lists them.
DECISIONS = frozenset({"included", "excluded", "adjacent", "planned", "no_public_roster"})

# Collected and published. ``adjacent`` stays a public strand. Every other
# decision is only a record: the collector is skipped and the memberships
# are left out of the site snapshot. Coverage still lists the frame.
ADMITTED = frozenset({"included", "adjacent"})


@dataclass(frozen=True)
class Eligibility:
    """One F1–F5 judgement. The strings are the reasons published with the verdict."""

    decision: str
    f1_purpose: str
    f2_cohort: str
    f3_territory: str
    f4_roster: str
    f5_period: str
    note: str = ""
    judged_at: str = ""


@dataclass(frozen=True)
class DeclaredSize:
    """A roster size one edition of a programme states itself, and the http(s) page that states it."""

    size: int
    source: str


# G13 reads these roles. A host line, a funder role, and the other rejected
# gates are not programme operators (docs/RULES.md).
OPERATOR_ROLES = frozenset({"organiser", "operator"})


@dataclass(frozen=True)
class FrameOperator:
    """One organiser or operator named on the programme's own archived page.

    ``name_ko`` and ``name_en`` are the spelling that equalled one venue.
    Either may be empty. The quote is the page text that contains the name.
    """

    name_ko: str
    name_en: str
    role: str
    source_url: str
    snapshot_path: str
    quote: str


@dataclass(frozen=True)
class Frame:
    """One programme in ``frames.yml``: names, source, and the recorded F1–F5 judgement."""

    code: str
    name_en: str
    source_url: str
    eligibility: Eligibility
    name_ko: str = ""
    roster_count: int | None = None
    # A roster size the programme states itself, and where it says so. Only
    # this size is a coverage denominator (see the module docstring).
    roster_size_declared: int | None = None
    roster_size_source: str = ""
    # Sizes stated per edition, as (edition, size) pairs sorted by edition.
    # A tuple keeps the frozen dataclass hashable.
    roster_size_declared_by_edition: tuple[tuple[str, DeclaredSize], ...] = ()
    included_count: int | None = None
    years_covered: str = ""
    status: str = ""
    # ``transcribed`` means the roster was copied by hand. The frame may then
    # have no single source page; each membership row carries its own.
    collector: str = ""
    # Organiser and operator credits from the programme's own page (G13).
    operators: tuple[FrameOperator, ...] = ()

    def coverage(self, members_recorded: int | None = None) -> float | None:
        """Members recorded / max(declared size, members recorded), or ``None``.

        ``members_recorded`` defaults to ``included_count``, the number of
        roster rows collected. Counting only rows that have a CV link would
        understate the roster. Without ``roster_size_declared`` the result is
        ``None``: ``roster_count`` is not an independent size (F4).
        """
        recorded = self.included_count if members_recorded is None else members_recorded
        if recorded is None or not self.roster_size_declared:
            return None
        return coverage(recorded, max(self.roster_size_declared, recorded))

    def declared_size(self, edition: str) -> DeclaredSize | None:
        """The size this edition states itself, or ``None`` when it states none."""
        for key, declared in self.roster_size_declared_by_edition:
            if key == str(edition):
                return declared
        return None

    def edition_coverage(self, edition: str, members_recorded: int) -> float | None:
        """Edition members recorded / max(the edition's declared size, recorded), or ``None``."""
        declared = self.declared_size(edition)
        if declared is None:
            return None
        return coverage(members_recorded, max(declared.size, members_recorded))


@dataclass(frozen=True)
class FrameRegistry:
    """The frames file and the programmes it lists."""

    path: Path
    frames: tuple[Frame, ...]
    version: int = 1
    updated_at: str = ""

    def by_code(self, code: str) -> Frame | None:
        """The frame with this code, or None when the file does not list it."""
        for frame in self.frames:
            if frame.code == code:
                return frame
        return None


def is_admitted(decision: str) -> bool:
    """True when this frame is collected and its memberships are published.

    The loader still only checks that the word is one of ``DECISIONS``. It does
    not re-decide F1–F5. Admission is this set, not a new judgement.
    """
    return decision in ADMITTED


def coverage(members_recorded: int, roster_size: int) -> float | None:
    """F4 coverage: members recorded / roster size.

    Returns ``None`` when ``roster_size`` is zero or negative (nothing to divide by).
    The site prints this ratio as a percentage, and a dash when the roster size
    is zero.
    """
    if roster_size <= 0:
        return None
    return members_recorded / roster_size


def load_frames(path: str | Path) -> FrameRegistry:
    """Load and validate ``frames.yml``. Raises ``ValueError`` when a judgement is incomplete."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("frames"), list):
        raise TypeError(f"{path}: expected a mapping with a 'frames' list")
    seen: set[str] = set()
    frames: list[Frame] = []
    for entry in raw["frames"]:
        frame = _frame(entry, path)
        if frame.code in seen:
            raise ValueError(f"{path}: duplicate frame code {frame.code}")
        seen.add(frame.code)
        frames.append(frame)
    version = raw.get("version", 1)
    return FrameRegistry(
        path=path,
        frames=tuple(frames),
        version=int(version) if version is not None else 1,
        updated_at=str(raw.get("updated_at") or ""),
    )


def _frame(entry: object, path: Path) -> Frame:
    if not isinstance(entry, dict):
        raise TypeError(f"{path}: each frame must be a mapping")
    code = str(entry.get("code") or "").strip()
    if not code:
        raise ValueError(f"{path}: frame code is required")
    name_en = str(entry.get("name_en") or "").strip()
    if not name_en:
        raise ValueError(f"{code}: name_en is required")
    collector = str(entry.get("collector") or "").strip()
    source_url = str(entry.get("source_url") or "").strip()
    # A hand-copied roster has no single page. The frame URL may be empty only
    # then; publish checks that every membership row of that frame has its own.
    transcribed_without_page = collector == "transcribed" and not source_url
    if not transcribed_without_page and not source_url.startswith(("http://", "https://")):
        raise ValueError(f"{code}: source_url must be an http(s) URL")
    eligibility = _eligibility(code, entry.get("eligibility"))
    # A declared size is a fact about the programme, so it needs a source like
    # any other fact (F4). A size without one is refused rather than ignored.
    declared_size = _optional_int(entry.get("roster_size_declared"))
    declared_source = str(entry.get("roster_size_source") or "").strip()
    if declared_size is not None and not declared_source.startswith(("http://", "https://")):
        raise ValueError(f"{code}: roster_size_declared needs an http(s) roster_size_source (F4)")
    by_edition = declared_sizes_by_edition(code, entry.get("roster_size_declared_by_edition"))
    return Frame(
        code=code,
        name_en=name_en,
        name_ko=str(entry.get("name_ko") or "").strip(),
        source_url=source_url,
        eligibility=eligibility,
        roster_count=_optional_int(entry.get("roster_count")),
        roster_size_declared=declared_size,
        roster_size_source=declared_source,
        roster_size_declared_by_edition=tuple(sorted(by_edition.items())),
        included_count=_optional_int(entry.get("included_count")),
        years_covered=str(entry.get("years_covered") or ""),
        status=str(entry.get("status") or ""),
        collector=collector,
        operators=_operators(code, entry.get("operators")),
    )


def declared_sizes_by_edition(code: str, raw: object) -> dict[str, DeclaredSize]:
    """Parse ``roster_size_declared_by_edition``: edition → ``{size, source}``.

    Each size is a fact about one edition, so like the frame-level size it needs
    its own http(s) source (F4). A positive integer is required: a stated size
    of zero would be a statement that nobody took part, not a roster.
    """
    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict):
        raise TypeError(f"{code}: roster_size_declared_by_edition must map an edition to {{size, source}}")
    out: dict[str, DeclaredSize] = {}
    for edition, value in raw.items():
        key = str(edition).strip()
        if not key:
            raise ValueError(f"{code}: roster_size_declared_by_edition has an empty edition")
        if not isinstance(value, dict):
            raise TypeError(f"{code} {key}: roster_size_declared_by_edition needs {{size, source}}")
        size = _optional_int(value.get("size"))
        source = str(value.get("source") or "").strip()
        if size is None or size <= 0:
            raise ValueError(f"{code} {key}: roster_size_declared_by_edition size must be a positive integer")
        if not source.startswith(("http://", "https://")):
            raise ValueError(f"{code} {key}: roster_size_declared_by_edition needs an http(s) source (F4)")
        out[key] = DeclaredSize(size=size, source=source)
    return out


def validate_transcribed_membership(registry: FrameRegistry, membership: list[Mapping[str, str]]) -> None:
    """Require a ``source_url`` on every membership row of a transcribed frame with none.

    The frame-level URL may be empty only when ``collector`` is ``transcribed``.
    The roster was copied from several third-party pages, so the source lives on
    the membership row. A row whose ``frame_code`` is that frame, or an edition
    of it (``CODE-2025``), counts.
    """
    needing = {frame.code for frame in registry.frames if frame.collector == "transcribed" and not frame.source_url}
    if not needing:
        return
    codes = [frame.code for frame in registry.frames]
    missing: list[str] = []
    owners: set[str] = set()
    for row in membership:
        mem_code = str(row.get("frame_code") or "")
        owner = _registry_code(mem_code, codes)
        if owner not in needing:
            continue
        if str(row.get("source_url") or "").strip():
            continue
        missing.append(mem_code or owner)
        owners.add(owner)
    if missing:
        shown = ", ".join(sorted(owners))
        raise ValueError(
            f"{shown}: collector is transcribed and source_url is empty, "
            f"so every membership row needs a source_url ({len(missing)} missing)"
        )


def _registry_code(mem_code: str, codes: list[str]) -> str | None:
    """Membership code → registry frame. The longest ``CODE`` such that the row is ``CODE`` or ``CODE-…``."""
    if mem_code in codes:
        return mem_code
    best = ""
    for code in codes:
        if mem_code.startswith(code + "-") and len(code) > len(best):
            best = code
    return best or None


def _operators(code: str, raw: object) -> tuple[FrameOperator, ...]:
    """Parse ``operators``. Absent is an empty list. A bad credit is refused.

    The quote has to contain each name that is set: the credit is the page
    sentence, not a name written beside it. ``snapshot_path`` is a relative
    archive path. The file is not opened here.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TypeError(f"{code}: operators must be a list")
    out: list[FrameOperator] = []
    for index, item in enumerate(raw, start=1):
        where = f"{code} operators item {index}"
        if not isinstance(item, dict):
            raise TypeError(f"{where} must be a mapping")
        name_ko = _text(item.get("name_ko"))
        name_en = _text(item.get("name_en"))
        role = _text(item.get("role"))
        source_url = _text(item.get("source_url"))
        snapshot_path = _text(item.get("snapshot_path"))
        quote = _text(item.get("quote"))
        if role not in OPERATOR_ROLES:
            allowed = ", ".join(sorted(OPERATOR_ROLES))
            raise ValueError(f"{where}: role must be one of {allowed}")
        if not name_ko and not name_en:
            raise ValueError(f"{where}: name_ko or name_en is required")
        if not source_url.startswith(("http://", "https://")):
            raise ValueError(f"{where}: source_url must be an http(s) URL")
        parts = Path(snapshot_path).parts
        if not snapshot_path or snapshot_path.startswith(("/", "\\")) or ".." in parts:
            raise ValueError(f"{where}: snapshot_path must be a relative archive path")
        if not quote:
            raise ValueError(f"{where}: quote is required")
        for name in (name_ko, name_en):
            if name and name not in quote:
                raise ValueError(f"{where}: quote does not contain the name")
        out.append(
            FrameOperator(
                name_ko=name_ko,
                name_en=name_en,
                role=role,
                source_url=source_url,
                snapshot_path=snapshot_path,
                quote=quote,
            )
        )
    return tuple(out)


def _eligibility(code: str, raw: object) -> Eligibility:
    if not isinstance(raw, dict):
        raise TypeError(f"{code}: eligibility is required (F1–F5)")
    decision = str(raw.get("decision") or "").strip()
    if decision not in DECISIONS:
        allowed = ", ".join(sorted(DECISIONS))
        raise ValueError(f"{code}: eligibility.decision must be one of {allowed}")
    purpose = _text(raw.get("f1_purpose"))
    cohort = _text(raw.get("f2_cohort"))
    territory = _text(raw.get("f3_territory")) or _text(raw.get("f3_korea"))
    roster = _text(raw.get("f4_roster"))
    period = _text(raw.get("f5_period"))
    missing = [
        (name, rule)
        for name, rule, value in (
            ("f1_purpose", "F1", purpose),
            ("f2_cohort", "F2", cohort),
            ("f3_territory", "F3", territory),
            ("f4_roster", "F4", roster),
            ("f5_period", "F5", period),
        )
        if not value
    ]
    if missing:
        label, rule = missing[0]
        alias = " (f3_korea is accepted as an alias)" if label == "f3_territory" else ""
        raise ValueError(f"{code}: eligibility.{label} is required ({rule}){alias}")
    return Eligibility(
        decision=decision,
        f1_purpose=purpose,
        f2_cohort=cohort,
        f3_territory=territory,
        f4_roster=roster,
        f5_period=period,
        note=_text(raw.get("note")),
        judged_at=_text(raw.get("judged_at")),
    )


def _text(value: object) -> str:
    return str(value or "").strip()


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
