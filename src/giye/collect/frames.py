# SPDX-License-Identifier: MIT
"""Sampling-frame registry (``frames.yml``) and eligibility F1–F5.

Production records one judgement per programme in ``data/frames.yml`` and publishes
it on the sampling-frame page. The five criteria were fixed before any candidate
programme was examined (adopted 2026-09-20), so inclusion is not a decision made
after seeing who was selected.

The software checks that a judgement is complete. It does not re-decide F1–F5:
the text in the file is the record, as in production.

F1 purpose. The programme's own public description states a purpose within the
field. In the Korean media-art archive that is art–technology (or art–science)
convergence, media art, new media, or digital art. The programme's document is
the evidence, not the institution's reputation.

F2 cohort. Participants are fixed by an open call, jury, selection, award, or
residency. A curated or rented exhibition does not qualify.

F3 territory. The programme is held in the archive's configured territory.
Production stored this sentence under ``f3_korea`` because that archive's
territory is Korea. This loader accepts ``f3_territory`` and, as an alias,
``f3_korea``.

F4 roster. The participant list is verifiable in a public record (official page,
catalogue, or press release).

F5 period. The programme has editions since 2010 and recurs at least twice. A
single edition counts only when it represents the field that year. The year
bound is the production census rule and is stored in the frame's own sentence;
it is not hard-coded here.

Coverage is members recorded / roster size (the ratio the site prints as a
percentage). ``None`` when the roster size is unknown or zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# Production also publishes ``planned`` and ``no_public_roster`` (see the sampling-frame page).
DECISIONS = frozenset({"included", "excluded", "adjacent", "planned", "no_public_roster"})


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
class Frame:
    code: str
    name_en: str
    source_url: str
    eligibility: Eligibility
    name_ko: str = ""
    roster_count: int | None = None
    included_count: int | None = None
    years_covered: str = ""
    status: str = ""

    def coverage(self, members_recorded: int | None = None) -> float | None:
        """Members recorded / roster size. Defaults to ``included_count`` when set.

        At this stage ``included_count`` is the number of roster rows collected.
        Production's site builder uses the same ratio (membership size / roster size).
        An earlier collector path counted only rows with a CV link; the site builder
        overwrote that, and this ratio follows the site builder.
        """
        recorded = self.included_count if members_recorded is None else members_recorded
        if recorded is None or not self.roster_count:
            return None
        return coverage(recorded, self.roster_count)


@dataclass(frozen=True)
class FrameRegistry:
    path: Path
    frames: tuple[Frame, ...]
    version: int = 1
    updated_at: str = ""

    def by_code(self, code: str) -> Frame | None:
        for frame in self.frames:
            if frame.code == code:
                return frame
        return None


def coverage(members_recorded: int, roster_size: int) -> float | None:
    """F4 coverage: members recorded / roster size.

    Returns ``None`` when ``roster_size`` is zero or negative (nothing to divide by).
    Production displayed ``round(100 * included / roster, 1)`` and an em dash when
    the roster size was zero.
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
    source_url = str(entry.get("source_url") or "").strip()
    if not source_url.startswith(("http://", "https://")):
        raise ValueError(f"{code}: source_url must be an http(s) URL")
    eligibility = _eligibility(code, entry.get("eligibility"))
    return Frame(
        code=code,
        name_en=name_en,
        name_ko=str(entry.get("name_ko") or "").strip(),
        source_url=source_url,
        eligibility=eligibility,
        roster_count=_optional_int(entry.get("roster_count")),
        included_count=_optional_int(entry.get("included_count")),
        years_covered=str(entry.get("years_covered") or ""),
        status=str(entry.get("status") or ""),
    )


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
