# SPDX-License-Identifier: AGPL-3.0-only
"""Entry-generation order for the home-page ring (``rim_order.json``).

The ledger is the source of truth, so the order is read from ledger rows and
written as the JSON the page already reads. The letters R1–R7 name the rules
below so a paper can cite them. See docs/RULES.md and docs/EXPLORE.md.

R1. Entry year. The earliest four-digit year on a roster membership code that
    ends in ``-YYYY``. A staff role on that code is not an entry: running or
    judging an edition is not entering it. A year that exists only as a frame's
    ``years_covered``, or only on an activity row, is not used. The year has to
    be on the code, so a yearless roster stays undated instead of inheriting a
    span that covers other people.

R2. Bins. Arcs are five-year generations anchored at 2000. ``GEN-2000`` is
    2000–2004, ``GEN-2005`` is 2005–2009, and the same step continues. A year
    before 2000 falls in that grid extended backwards (1995 is ``GEN-1995``),
    so an older edition gets its own arc instead of stopping the build.
    ``GEN-UNDATED`` is everyone with no such year, placed last. An empty bin
    is omitted: an arc with nobody in it would be a label with no slots.

R3. Arc order. Chronological, clockwise from 12 o'clock, oldest first, undated
    last. The seam is the boundary between that last arc and the oldest:
    undated when anyone is undated, otherwise the newest. ``boundaries`` lists
    every consecutive pair, including the wrap. ``shared``, ``cos`` and
    ``agree`` are 0. They stay in the file because the page reads those keys;
    they are not a similarity.

R4. Inside an arc. Entry year, then each team followed by its members, then
    Unicode order of the primary name (Hangul code points are dictionary
    order), then id. ``year`` and ``edition`` are both that entry year
    (``edition`` is the four-digit string, or ``""`` when undated). The page
    leaves a gap wherever ``year|edition`` changes, so the gap falls between
    entry years. A team is the activity role ``<team prefix> X`` (the field
    file's prefix; the default is ``팀:``); the row whose name is
    the team name comes first, then the members. Record counts, programme
    counts, and any other activity measure are not sort keys.

R5. ``also``. The programme families the person took part in, staff excluded.
    A field file may fold several codes into one family (a prefix and that
    prefix plus ``-…``). The home arc is a generation, so this is every such
    family, not "families other than the home programme". Codes are sorted:
    that order does not measure activity.

R6. ``programmes``. One row per family that appears in ``also``. Labels are
    the registry name with the parenthetical and the subtitle after `` · `` or
    `` — `` removed; the first registry row wins. A field file may set the
    label for a folded family. Order is the family code.
    The page counts members from ``also``, so this table carries no counts.

R7. One slot per artist. ``families[].participants`` equals ``n``. ``linked``
    is false only for ``GEN-UNDATED``. The keep-tolerance that used to retain
    a previous programme order is gone: a year's generation does not move
    between builds.

Who is on the ring is who the site builder publishes: in scope, an http(s)
source, on a roster, status empty / ``PUBLISHED`` /
``STAGED``. A roster row is the evidence of participation. A publishable row
with no ``gy_id`` is left off the ring. The collector, or the site builder,
issues that permanent id and writes it; this function does not.

A code without ``-YYYY`` is undated. R1 does not read a year that lives only
on an activity. The demo collectors write ``<FRAME>-<YYYY>``. The base
``RosterCollector`` still stores the bare frame, and that ring stays undated.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from giye.config import Config
from giye.field import Field, edition_alias, rim_family, rim_label
from giye.ledger.io import read_csv

# R1. Running or judging an edition is not entering it. The pattern is the staff test.
STAFF_ROLE = re.compile(
    r"기획|총괄|퍼실리테이터|facilitat|연구위원|엮음|curat|큐레이|organi[sz]|운영|심사|judge|jury|mentor|멘토|"
    r"moderat|coordinator|코디네이|자문|advis|위원|컨설턴트|consultant",
    re.IGNORECASE,
)
YEAR_SUFFIX = re.compile(r"-(\d{4})$")
BIN_START = 2000
BIN_WIDTH = 5
UNDATED = "GEN-UNDATED"
# Sort keys for an undated person sit after every real year. 99999 is not a year.
_UNDATED_START = 99999
_UNDATED_YEAR = 9999


def short_name(name: str) -> str:
    """R6 label. Returns the registry name without a parenthetical or a `` · `` / `` — `` subtitle.

    Returns the original name when stripping would leave it empty.
    """
    return re.sub(r"\s*[(（][^)）]*[)）]", "", name).split(" · ")[0].split(" — ")[0].strip() or name


def family_of(code: str, field: Field | None = None) -> str:
    """R5 family fold. Returns the field-file family, or ``code`` when nothing folds it."""
    if field is None:
        return code
    return rim_family(code, field)


def code_year(code: str) -> int | None:
    """R1 year. Returns the four-digit suffix of ``code``, or None when the code is undated."""
    match = YEAR_SUFFIX.search(code)
    return int(match.group(1)) if match else None


def generation_of(year: int) -> tuple[str, int]:
    """R2 bin. Returns ``(GEN-<start>, start)``.

    Floor division extends the five-year grid before the 2000 anchor, so an
    older edition gets its own arc.
    """
    start = BIN_START + BIN_WIDTH * ((year - BIN_START) // BIN_WIDTH)
    return f"GEN-{start}", start


def generation_labels(code: str, start: int | None) -> tuple[str, str]:
    """R2 arc labels. Returns ``(label_ko, label_en)`` for the page to display as written."""
    if code == UNDATED:
        return "진입 연도 미상", "Entry year unknown"
    if start is None:
        raise ValueError(f"{code} has no bin start")
    end = start + BIN_WIDTH - 1
    return f"{start}–{end} 진입", f"Entered {start}–{end}"


def resolve_frame_edition(
    mem_code: str,
    registry: Sequence[str],
    years_by_frame: Mapping[str, str],
    *,
    field: Field | None = None,
) -> tuple[str, str | None] | None:
    """Membership code → ``(registry frame, edition year)``.

    ``NORTH-2022`` → ``(NORTH, 2022)`` when ``NORTH`` is the longest registry
    code that is a prefix. A code equal to a registry row uses that row's
    ``years_covered`` when it is a single year. The entry year itself is read
    from the code suffix, not from ``years_covered``.

    A field file may declare an edition alias. It applies when the target
    frame is in the registry. The alias is the whole exception: the resolver
    does not name a programme.
    """
    if field is not None:
        aliased = edition_alias(mem_code, registry, field)
        if aliased:
            return aliased
    best: tuple[str, str] | None = None
    for code in registry:
        if mem_code == code:
            covered = years_by_frame.get(code, "")
            return code, covered if re.fullmatch(r"\d{4}", covered) else None
        suffix = mem_code[len(code) + 1 :]
        if mem_code.startswith(code + "-") and suffix.isdigit() and (not best or len(code) > len(best[0])):
            best = (code, suffix)
    return best


def build_rim_order(
    source: Config | Mapping[str, Any], *, now: datetime | None = None, field: Field | None = None
) -> dict[str, Any]:
    """Ring document for ``source`` (R1–R7).

    Returns the JSON object the home page reads. ``source`` is a
    :class:`giye.config.Config`, or a mapping with ``artists``, ``activities``,
    ``frame_membership`` and ``frames`` (optional ``scope``). Rows are the
    ledger CSV rows. Frame rows are the ``frames.yml`` objects (``code``,
    ``name_ko``, ``name_en``, ``years_covered``, ``source_url``). ``now`` fixes
    ``version`` and ``computed_at``; the default is the current UTC time.
    """
    tables = _tables(source)
    clock = _clock(now)
    artists, frames, activities = _published_view(tables)
    if field is None and isinstance(source, Config):
        field = source.field_config
    elif field is None and isinstance(source, Mapping):
        supplied = source.get("field")
        field = supplied if isinstance(supplied, Field) else None
    if field is None:
        field = Field()
    return _order(artists, frames, activities, clock, field)


def write_rim_order(document: Mapping[str, Any], site_dir: str | Path) -> Path:
    """Write ``document`` to ``<site_dir>/rim_order.json`` and return that path.

    UTF-8, no ASCII escaping, no spaces, and no trailing newline: the page
    reads the file as one JSON value.
    """
    directory = Path(site_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "rim_order.json"
    path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path


def _clock(now: datetime | None) -> datetime:
    """UTC clock for ``version`` and ``computed_at``. A naive datetime is taken as UTC."""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _tables(source: Config | Mapping[str, Any]) -> dict[str, Any]:
    """Ledger tables from a Config, or from a mapping that already holds them."""
    if isinstance(source, Config):
        from giye.config import checked_frames

        checked_frames(source)
        return {
            "artists": read_csv(source.ledger / "artists.csv"),
            "activities": read_csv(source.ledger / "activities.csv"),
            "frame_membership": read_csv(source.ledger / "frame_membership.csv"),
            "scope": read_csv(source.ledger / "scope.csv"),
            "frames": _frames_from_file(source.frames),
        }
    if isinstance(source, Mapping):
        missing = [key for key in ("artists", "activities", "frame_membership", "frames") if key not in source]
        if missing:
            raise KeyError(f"rim order needs {', '.join(missing)}")
        return {
            "artists": list(source["artists"]),
            "activities": list(source["activities"]),
            "frame_membership": list(source["frame_membership"]),
            "scope": list(source.get("scope") or []),
            "frames": list(source["frames"]),
        }
    raise TypeError("build_rim_order expects a Config or a mapping of ledger tables")


def _frames_from_file(path: Path) -> list[dict[str, Any]]:
    """Frame rows in file order. Names and ``years_covered`` are what the labels use.

    Eligibility is not an input to the order, so a missing F1–F5 block does not
    stop the ring. The site builder is what checks those judgements.
    """
    if not path.is_file():
        raise FileNotFoundError(f"frames file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("frames"), list):
        raise TypeError(f"{path}: expected a mapping with a 'frames' list")
    return list(raw["frames"])


def _parse_year(value: object) -> int | None:
    """First 19xx or 20xx in ``value``. A year outside that window is not used."""
    if value is None or value == "":
        return None
    match = re.search(r"(19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


def _mapping_rows(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """Keep mapping rows. A non-dict row is not a ledger record."""
    return [row for row in rows if isinstance(row, dict)]


def _codes_by_person(membership: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Person → membership codes, in file order."""
    mem_by: dict[str, list[str]] = {}
    for row in membership:
        mem_by.setdefault(str(row.get("ledger_id") or ""), []).append(str(row.get("frame_code") or ""))
    return mem_by


def _roster_http_by_person(
    membership: Sequence[Mapping[str, Any]],
    frames: Sequence[Mapping[str, Any]],
    registry: Sequence[str],
    years_by_frame: Mapping[str, str],
) -> dict[str, str]:
    """First http(s) source for a person: the membership row, else the frame page.

    A person with no http(s) source of their own can still be published from the roster.
    """
    frame_url = {str(row.get("code") or ""): str(row.get("source_url") or "") for row in frames}
    roster_url: dict[str, str] = {}
    for row in membership:
        edition = resolve_frame_edition(str(row.get("frame_code") or ""), registry, years_by_frame)
        for url in (str(row.get("source_url") or ""), frame_url.get(edition[0], "") if edition else ""):
            if url.startswith("http"):
                roster_url.setdefault(str(row.get("ledger_id") or ""), url)
                break
    return roster_url


def _scope_out_ids(scope_rows: Sequence[Any]) -> set[str]:
    """Ledger ids marked out of scope."""
    return {
        str(row.get("ledger_id") or "")
        for row in scope_rows
        if isinstance(row, dict) and row.get("scope") == "out"
    }


def _last_non_cv_role(activities: Sequence[Any]) -> dict[tuple[str, str], str]:
    """Last non-CV role for a (person, membership code). A later row replaces an earlier one.

    The activity ``origin`` is the membership code, which is how a roster role is attached.
    """
    roster_role: dict[tuple[str, str], str] = {}
    for row in activities:
        if not isinstance(row, dict):
            continue
        origin = str(row.get("origin") or "")
        role = str(row.get("role") or "")
        if role and origin and not origin.startswith("cv:"):
            roster_role[(str(row.get("ledger_id") or ""), origin)] = role
    return roster_role


def _edition_records(
    codes: Sequence[str],
    roster_role: Mapping[tuple[str, str], str],
    ledger_id: str,
    registry: Sequence[str],
    years_by_frame: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Roster editions for one person, with the role recorded on that membership code."""
    editions: list[dict[str, Any]] = []
    seen: list[tuple[Any, ...]] = []
    for code in codes:
        item = (resolve_frame_edition(code, registry, years_by_frame) or (None, None)) + (code,)
        if item not in seen:
            seen.append(item)
    for frame, edition, code in seen:
        if not frame:
            continue
        record: dict[str, Any] = {"frame": frame, "edition": edition}
        role = roster_role.get((ledger_id, code))
        if role:
            record["role"] = role
        editions.append(record)
    return editions


def _ring_people(
    artists: Sequence[Any],
    *,
    mem_by: Mapping[str, list[str]],
    roster_url: Mapping[str, str],
    out_of_scope: set[str],
    roster_role: Mapping[tuple[str, str], str],
    registry: Sequence[str],
    years_by_frame: Mapping[str, str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """People on the ring, and ledger id → ``gy_id``.

    Same publication test as the site: in scope, an http(s) source, on a
    roster, status empty / ``PUBLISHED`` / ``STAGED``, and a
    ``gy_id`` already issued. Returns the ring rows. This does not mint an id.
    """
    artists_out: list[dict[str, Any]] = []
    ledger_to_gy: dict[str, str] = {}
    for raw in artists:
        if not isinstance(raw, dict):
            continue
        artist = dict(raw)
        ledger_id = str(artist.get("ledger_id") or "")
        if ledger_id in out_of_scope:
            continue
        source = str(artist.get("source_url") or "")
        if not source.startswith("http") and ledger_id in roster_url:
            source = roster_url[ledger_id]
        # A CV link does not admit a person: only a roster membership does.
        if ledger_id not in mem_by:
            continue
        if not source.startswith("http"):
            continue
        if artist.get("status") not in ("", "PUBLISHED", "STAGED"):
            continue
        gy = str(artist.get("gy_id") or "")
        # The id is permanent and is issued by the collector or the site builder.
        # Minting one here would write the ledger. Until the id exists the person
        # has no page, so they have no slot on the ring either.
        if not gy:
            continue
        codes = mem_by.get(ledger_id, [])
        editions = _edition_records(codes, roster_role, ledger_id, registry, years_by_frame)
        name_ko = artist.get("name_ko") or artist.get("name_en") or "이름 미상"
        artists_out.append(
            {
                "id": gy,
                "name_ko": name_ko,
                "name_en": artist.get("name_en") or None,
                "frame_codes": codes,
                "frame_editions": editions,
            }
        )
        ledger_to_gy[ledger_id] = gy
    return artists_out, ledger_to_gy


def _team_credit_rows(activities: Sequence[Any], ledger_to_gy: Mapping[str, str]) -> list[dict[str, Any]]:
    """Publishable activities with a title, a year, and an http(s) source.

    Newest year first, then title. The first team-prefix role kept later is
    therefore the latest year (R4).
    """
    team_rows: list[dict[str, Any]] = []
    for row in activities:
        if not isinstance(row, dict) or row.get("publishable") != "yes":
            continue
        gy = ledger_to_gy.get(str(row.get("ledger_id") or ""))
        if not gy:
            continue
        title = str(row.get("title") or "").strip()
        year = _parse_year(row.get("year"))
        source = str(row.get("source_url") or "").strip()
        if not title or year is None or not source.startswith("http"):
            continue
        team_rows.append({"artist_id": gy, "title": title, "year": year, "role": row.get("role") or ""})
    team_rows.sort(key=lambda item: (-item["year"], item["title"]))
    return team_rows


def _published_view(
    tables: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Artist, frame, and activity rows the ring draws, read from the ledger.

    Built from the ledger so the ring does not depend on a snapshot that may
    be older than the ledger. Returns those three lists.
    """
    frames = _mapping_rows(tables["frames"])
    registry = [str(row.get("code") or "") for row in frames]
    years_by_frame = {str(row.get("code") or ""): str(row.get("years_covered") or "") for row in frames}
    membership = _mapping_rows(tables["frame_membership"])
    mem_by = _codes_by_person(membership)
    roster_url = _roster_http_by_person(membership, frames, registry, years_by_frame)
    artists_out, ledger_to_gy = _ring_people(
        tables["artists"],
        mem_by=mem_by,
        roster_url=roster_url,
        out_of_scope=_scope_out_ids(tables["scope"]),
        roster_role=_last_non_cv_role(tables["activities"]),
        registry=registry,
        years_by_frame=years_by_frame,
    )
    return artists_out, frames, _team_credit_rows(tables["activities"], ledger_to_gy)


def _labels_by_family(frames: Sequence[Mapping[str, Any]], field: Field) -> dict[str, tuple[str, str]]:
    """R6 labels. A field-file label wins; otherwise the first registry short name is kept."""
    name_of: dict[str, tuple[str, str]] = {}
    for frame in frames:
        fam = family_of(str(frame.get("code") or ""), field)
        declared = rim_label(fam, field)
        if declared:
            name_of[fam] = declared
        else:
            name_ko = short_name(str(frame.get("name_ko") or ""))
            name_en = short_name(str(frame.get("name_en") or frame.get("name_ko") or ""))
            name_of.setdefault(fam, (name_ko, name_en))
    return name_of


def _teams_by_person(activities: Sequence[Mapping[str, Any]], field: Field) -> dict[str, str]:
    """R4 team name. The first team-prefix role for a person wins."""
    team_role = re.compile(re.escape(field.team_prefix or "팀:") + r"\s*(.+)")
    team_of: dict[str, str] = {}
    for row in activities:
        match = team_role.match(str(row.get("role") or ""))
        if match:
            team_of.setdefault(str(row.get("artist_id") or ""), match.group(1).strip())
    return team_of


def _place_one(
    artist: Mapping[str, Any],
    *,
    registry: Sequence[str],
    years_by_frame: Mapping[str, str],
    field: Field,
    team_of: Mapping[str, str],
    name_key: Mapping[str, str],
) -> dict[str, Any]:
    """One ring slot: R1 entry year, R2 bin, R4 sort key, R5 programme families."""
    artist_id = str(artist["id"])
    pool: dict[tuple[str, str], list[str]] = {}
    for edition_row in artist.get("frame_editions") or []:
        key = (str(edition_row.get("frame") or ""), str(edition_row.get("edition") or ""))
        pool.setdefault(key, []).append(str(edition_row.get("role") or ""))
    years: list[int] = []
    programmes: set[str] = set()
    for code in artist.get("frame_codes") or []:
        code = str(code)
        resolved = resolve_frame_edition(code, registry, years_by_frame, field=field)
        role = ""
        if resolved is None:
            frame = YEAR_SUFFIX.sub("", code)
        else:
            frame, edition = resolved
            roles = pool.get((frame, edition or ""), [])
            if roles:
                role = roles.pop(0)
        year = code_year(code)
        if role and STAFF_ROLE.search(role):
            continue
        if frame:
            programmes.add(family_of(frame, field))
        if year is not None:
            years.append(year)
    entry = min(years) if years else None
    if entry is None:
        code, start = UNDATED, None
    else:
        code, start = generation_of(entry)
    edition_label = str(entry) if entry is not None else ""
    team = team_of.get(artist_id)
    person_name = name_key[artist_id]
    group_name = team or person_name
    return {
        "id": artist_id,
        "family": code,
        "year": entry,
        "edition": edition_label,
        "also": sorted(programmes),
        "_start": start,
        "_k": (
            start if start is not None else _UNDATED_START,
            entry if entry is not None else _UNDATED_YEAR,
            edition_label,
            group_name,
            0 if person_name == group_name else 1,
            person_name,
            artist_id,
        ),
    }


def _arc_sequence(
    placed: list[dict[str, Any]],
) -> tuple[list[str], dict[str, int], dict[str, int | None]]:
    """R2–R3. Count occupied bins, drop the private sort keys, oldest first, undated last."""
    counts: dict[str, int] = {}
    starts: dict[str, int | None] = {}
    for row in placed:
        counts[row["family"]] = counts.get(row["family"], 0) + 1
        starts.setdefault(row["family"], row["_start"])
        del row["_k"]
        del row["_start"]
    order = sorted(counts, key=lambda code: (starts[code] is None, starts[code] or 0, code))
    return order, counts, starts


def _programme_rows(
    placed: Sequence[Mapping[str, Any]], name_of: Mapping[str, tuple[str, str]]
) -> list[dict[str, str]]:
    """R6. One row per family that appears in ``also``, in family-code order, with no counts."""
    return [
        {
            "code": code,
            "label_ko": name_of.get(code, (code, code))[0],
            "label_en": name_of.get(code, (code, code))[1],
        }
        for code in sorted({item for row in placed for item in row["also"]})
    ]


def _boundary_rows(order: Sequence[str]) -> list[dict[str, Any]]:
    """R3. Every consecutive pair, including the wrap. The similarity keys stay 0."""
    boundaries: list[dict[str, Any]] = []
    if len(order) >= 2:
        for index, left in enumerate(order):
            right = order[(index + 1) % len(order)]
            boundaries.append(
                {
                    "a": left,
                    "b": right,
                    "shared": 0,
                    "cos": 0,
                    "agree": 0,
                    "seam": index == len(order) - 1,
                }
            )
    return boundaries


def _family_arcs(
    order: Sequence[str], counts: Mapping[str, int], starts: Mapping[str, int | None]
) -> list[dict[str, Any]]:
    """R7. One arc per occupied bin. ``participants`` equals ``n``. Undated is not linked."""
    return [
        {
            "code": code,
            "label_ko": generation_labels(code, starts[code])[0],
            "label_en": generation_labels(code, starts[code])[1],
            "n": counts[code],
            "participants": counts[code],
            "linked": code != UNDATED,
            "first_year": starts[code],
        }
        for code in order
    ]


def _order(
    artists: Sequence[Mapping[str, Any]],
    frames: Sequence[Mapping[str, Any]],
    activities: Sequence[Mapping[str, Any]],
    clock: datetime,
    field: Field,
) -> dict[str, Any]:
    """Rim document for the published rows. Returns the R1–R7 object the page reads."""
    registry = [str(row.get("code") or "") for row in frames]
    years_by_frame = {str(row.get("code") or ""): str(row.get("years_covered") or "") for row in frames}
    name_of = _labels_by_family(frames, field)
    team_of = _teams_by_person(activities, field)
    name_key = {str(artist["id"]): str(artist.get("name_ko") or artist.get("name_en") or "") for artist in artists}
    placed = [
        _place_one(
            artist,
            registry=registry,
            years_by_frame=years_by_frame,
            field=field,
            team_of=team_of,
            name_key=name_key,
        )
        for artist in artists
    ]
    placed.sort(key=lambda row: row["_k"])
    order, counts, starts = _arc_sequence(placed)
    return {
        "version": clock.strftime("%Y-%m-%d"),
        "computed_at": clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": {
            "grouping": (
                "5-year entry generation: earliest roster frame code ending -YYYY, staff roles excluded"
            ),
            "bins": (
                f"GEN-{BIN_START} is {BIN_START}–{BIN_START + BIN_WIDTH - 1}, then every "
                f"{BIN_WIDTH} years anchored at {BIN_START}; {UNDATED} last"
            ),
            "order": "chronological, clockwise from 12 o'clock, oldest first, undated last",
            "boundaries": (
                "consecutive generations; shared, cos and agree are 0 because the order "
                "is chronological, not similarity-based"
            ),
            "seam": (
                "boundary between undated (or the newest, if every entry year is dated) and the oldest"
            ),
            "within_arc": (
                "entry year, then each team followed by its members, then Korean collation "
                "of the name, then id; not record counts, programme counts, or any activity measure"
            ),
            "home": "entry generation",
        },
        "programmes": _programme_rows(placed, name_of),
        "families": _family_arcs(order, counts, starts),
        "boundaries": _boundary_rows(order),
        "artists": placed,
    }
