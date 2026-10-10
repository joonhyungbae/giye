# SPDX-License-Identifier: AGPL-3.0-only
"""Published people, the CV layer, roster editions, and the art-tech set.

What: loads the same population the release uses, by calling the same public
functions (``published_ids``, ``admitted_memberships``, ``checked_frames``,
``privacy_for``, the edition resolver). Staff roles are the rim's
``STAFF_ROLE``. A hidden person is not stored.

Why: the career bundle and the open release have to agree on who is published.
``giye.export.release._load_population`` is private and in flux, so this module
is its own copy of that assembly.

How to run: imported by ``python -m giye.career build``. This module is not a
script.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from giye.career.measures import CvRow, arttech_venue_ids, country_group, norm_name, published_regions
from giye.career.rules import C0
from giye.collect.frames import Frame, is_admitted
from giye.config import Config, checked_frames
from giye.explore.rim import STAFF_ROLE, code_year, generation_of
from giye.export.privacy import privacy_for
from giye.ledger.io import read_csv
from giye.normalize.language import language_for
from giye.normalize.rules import admitted_memberships, published_ids
from giye.publish.snapshot import _edition_resolver, _frames_document
from giye.resolve.teams import team_like

_YEAR = re.compile(r"^\d{4}$")


def generation_label(year: int) -> str:
    """C2. ``GEN-<start>`` from the rim's five-year bins."""
    code, _start = generation_of(year)
    return code


def _year(value: str) -> int | None:
    if _YEAR.fullmatch(value or ""):
        return int(value)
    return None


@dataclass
class Person:
    """One published person. ``ledger_id`` stays in memory for the build and is not written out."""

    ledger_id: str
    first_year: int | None
    generation: str
    weight: float
    has_weight: bool
    team: bool
    # Dated non-staff editions, unique (programme, year), sorted.
    editions: tuple[tuple[str, int], ...]
    rows: tuple[CvRow, ...]
    cv: bool


@dataclass
class Programme:
    """An admitted frame. Names are the programme's, which the bundle may publish."""

    code: str
    name_en: str
    name_ko: str
    access_mode: str


@dataclass
class Population:
    """Everyone the bundle is allowed to count, and the sets the measures share."""

    people: dict[str, Person]
    programmes: dict[str, Programme]
    arttech: set[str]
    regions_published: list[str]
    territory: str
    published_n: int
    cv_layer_n: int
    cv_no_first_year: int
    unweighted_people: int
    # CV-layer people with a first year, sorted by ledger id so later draws are stable.
    cv_ready: list[Person] = field(default_factory=list)


def _operator_names(frames: list[Frame]) -> set[str]:
    """G13 names on admitted frames, NFC-stripped. An excluded frame does not mark a venue."""
    names: set[str] = set()
    for frame in frames:
        if not is_admitted(frame.eligibility.decision):
            continue
        for operator in frame.operators:
            for part in (operator.name_ko, operator.name_en):
                name = norm_name(part)
                if name:
                    names.add(name)
    return names


def _editions(
    memberships: list[dict[str, str]],
    edition_of,
    roles: dict[tuple[str, str], str],
    ledger_id: str,
) -> tuple[tuple[str, int], ...]:
    """R1. Admitted memberships become (programme, year). A staff role is not a participation.

    Undated codes are not an entry year (C8 asks for the first dated edition).
    Repeated rows for one (person, programme, year) count once.
    """
    seen: set[tuple[str, int]] = set()
    for row in memberships:
        code = row.get("frame_code") or ""
        role = roles.get((ledger_id, code), "")
        if role and STAFF_ROLE.search(role):
            continue
        resolved = edition_of(code)
        if resolved:
            programme, year_text = resolved
            programme = str(programme)
            year = _year(str(year_text or ""))
        else:
            # Same fallback as the release: a trailing -YYYY is the edition year.
            year = code_year(code)
            programme = re.sub(r"-\d{4}$", "", code)
        if year is None or not programme:
            continue
        seen.add((programme, year))
    return tuple(sorted(seen))


def load_population(config: Config, weights: dict[str, float] | None, *, k: int) -> Population:
    """Published people and their CV rows. A person hidden by request is not stored.

    ``weights`` is the IPW file keyed by ledger id, or None when the build has
    no weights. Missing people, when a file was given, get weight 1 and are
    counted as unweighted. With no file every weight is 1 and the unweighted
    count stays 0, because no weighted column is written.
    """
    privacy = privacy_for(config)
    registry = checked_frames(config)
    frame_rows = list(_frames_document(config.frames).get("frames") or [])
    decisions = {frame.code: frame.eligibility.decision for frame in registry.frames}
    edition_of = _edition_resolver(frame_rows, config.field_config)
    artists = {row["ledger_id"]: row for row in read_csv(config.ledger / "artists.csv") if row.get("ledger_id")}
    published = published_ids(
        list(artists.values()),
        read_csv(config.ledger / "frame_membership.csv"),
        read_csv(config.ledger / "scope.csv"),
        frame_rows,
        edition_of,
        decisions,
    )
    published -= privacy.hidden_ids
    published = {
        ledger_id
        for ledger_id in published
        if artists.get(ledger_id, {}).get("status") != "HIDDEN_BY_REQUEST"
    }

    admitted = admitted_memberships(read_csv(config.ledger / "frame_membership.csv"), decisions, edition_of)
    by_person: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in admitted:
        if row.get("ledger_id") in published:
            by_person[row["ledger_id"]].append(row)

    roles: dict[tuple[str, str], str] = {}
    for row in read_csv(config.ledger / "activities.csv"):
        origin = row.get("origin") or ""
        # A CV origin is not a roster role. The first roster role for a code wins.
        if row.get("role") and origin and not origin.startswith("cv:") and row.get("ledger_id") in published:
            roles.setdefault((row["ledger_id"], origin), row["role"])

    attributes = read_csv(config.processed / "artist_attributes.csv")
    first_year: dict[str, int] = {}
    for row in attributes:
        if row.get("field") != "active_since" or row.get("ledger_id") not in published:
            continue
        year = _year(row.get("value") or "")
        if year is not None:
            first_year[row["ledger_id"]] = year

    cv_ids: set[str] = set()
    cv_rows: dict[str, list[CvRow]] = defaultdict(list)
    processed_rows = read_csv(config.processed / "activities.csv")
    for row in processed_rows:
        ledger_id = row.get("ledger_id") or ""
        if ledger_id not in published:
            continue
        origin = row.get("origin") or ""
        if not origin.startswith("cv:"):
            continue
        cv_ids.add(ledger_id)
        cv_rows[ledger_id].append(
            CvRow(
                year=_year(row.get("year") or ""),
                kind=row.get("activity_kind") or "",
                country=row.get("venue_country") or "",
                region=row.get("venue_region") or "",
                venue_id=(row.get("venue_id") or "").strip(),
                venue_kind=row.get("venue_kind") or "",
            )
        )

    words = config.field_config.compiled_team_words()
    language = language_for(config)
    people: dict[str, Person] = {}
    unweighted = 0
    for ledger_id in sorted(published):
        artist = artists.get(ledger_id, {})
        has_weight = bool(weights) and ledger_id in weights
        if weights is None:
            weight = 1.0
        elif ledger_id in weights:
            weight = weights[ledger_id]
        else:
            weight = 1.0
            if ledger_id in cv_ids and ledger_id in first_year:
                unweighted += 1
        year = first_year.get(ledger_id)
        people[ledger_id] = Person(
            ledger_id=ledger_id,
            first_year=year,
            generation=generation_label(year) if year is not None else "",
            weight=weight,
            has_weight=has_weight,
            team=bool(team_like(artist, words=words, language=language)),
            editions=_editions(by_person.get(ledger_id, []), edition_of, roles, ledger_id),
            rows=tuple(cv_rows.get(ledger_id, ())),
            cv=ledger_id in cv_ids,
        )

    cv_ready = [people[ledger_id] for ledger_id in sorted(cv_ids & set(first_year))]
    cv_no_first = len(cv_ids - set(first_year))

    access = {str(row.get("code") or ""): str(row.get("access_mode") or "") for row in frame_rows}
    programmes = {
        frame.code: Programme(
            code=frame.code,
            name_en=frame.name_en,
            name_ko=frame.name_ko,
            access_mode=access.get(frame.code, ""),
        )
        for frame in registry.frames
        if is_admitted(frame.eligibility.decision)
    }

    venues = read_csv(config.processed / "venues.csv")
    # C5's event_link scan uses published people's rows only. A hidden person's
    # row is not read, so it cannot add a venue to the set and move someone
    # else's art-tech share.
    published_activity = [row for row in processed_rows if (row.get("ledger_id") or "") in published]
    arttech = arttech_venue_ids(published_activity, venues, _operator_names(list(registry.frames)))

    region_people: dict[str, set[str]] = defaultdict(set)
    for person in cv_ready:
        for row in person.rows:
            if row.venue_kind in {"empty", "title_only"}:
                continue
            if country_group(row.country, config.territory) != "home":
                continue
            region = (row.region or "").strip()
            if region:
                region_people[region].add(person.ledger_id)

    return Population(
        people=people,
        programmes=programmes,
        arttech=arttech,
        regions_published=published_regions(region_people, k),
        territory=config.territory or "",
        published_n=len(published),
        cv_layer_n=len(cv_ids),
        cv_no_first_year=cv_no_first,
        unweighted_people=unweighted,
        cv_ready=cv_ready,
    )


def default_current_year(config: Config, cap_year: int) -> int:
    """Largest four-digit year on a processed row that is not channel ``hidden``, capped at ``cap_year``.

    The scan is every processed row, as the spec says, not only published
    people. A build with no such year uses the cap.
    """
    largest: int | None = None
    for row in read_csv(config.processed / "activities.csv"):
        if (row.get("activity_channel") or "") == "hidden":
            continue
        if not _YEAR.fullmatch(row.get("year") or ""):
            continue
        year = int(row["year"])
        if largest is None or year > largest:
            largest = year
    if largest is None:
        return cap_year
    return min(largest, cap_year)


# C0 is the cv flag on Person. Named here so the rule id is referenced from the loader.
_CV_LAYER = C0


def generation_of_year(year: int) -> str:
    """Alias kept for callers that read the rule name. Same value as ``generation_label``."""
    return generation_label(year)
