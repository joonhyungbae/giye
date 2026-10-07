# SPDX-License-Identifier: AGPL-3.0-only
"""Build the site snapshot (``<data>/site/*.json``) from the ledger.

The JSON objects are what the site reads. Counts are written to ``frames.json``
and ``coverage.json``; ``frames.yml`` is not rewritten. Embedding flight files
are stage 6 and are not built here. ``citations.json`` holds the APA, Chicago,
and BibTeX strings (see ``giye.publish.cite``). See docs/RULES.md (publication).

Who is published: a ledger row that is in scope, has an http(s) source, is on an
admitted roster (``cv_link_ok`` does not admit anyone), and whose status is empty, ``PUBLISHED``, or
``STAGED``. A roster membership counts only when the frame's decision is
``included`` or ``adjacent``. ``adjacent`` stays an adjacent strand.
``excluded``, ``planned``, ``no_public_roster``, and any other decision are
listed on the coverage files with that decision, and their memberships are
not published. A roster row of an admitted frame is itself the evidence of
participation. A row that already has a ``gy_id`` but is not published becomes
a stub: ``HIDDEN_BY_REQUEST``
or ``WITHDRAWN``, with no name and no records. A retired id is a redirect to the
survivor's current id (the ledger already points every retirement at the final
survivor).

F4 coverage is members recorded / roster size, printed as
``round(100 * included / max(declared, included), 1)``. ``declared`` is
``roster_size_declared``, a size the programme states itself with a
``roster_size_source``; the greater of it and the membership count is taken so
a declared size smaller than the rows on file does not hide members. Without a
declared size coverage is null (unknown): ``roster_count`` in ``frames.yml`` is
written from what was collected, and dividing by it would read 100% by
construction. ``roster_count`` in the snapshot stays the greater of that key
and the membership count, the roster size the site displays.

An edition whose own size is declared (``roster_size_declared_by_edition``)
gets the same three keys in its ``editions`` entry, computed from that
edition's members. Editions without a declared size carry none of them, so a
registry without per-edition sizes yields the same snapshot as before.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from giye.collect.frames import FrameRegistry, declared_sizes_by_edition, load_frames, validate_transcribed_membership
from giye.config import Config, ConfigError
from giye.extract.apply import PRIVATE_TITLE
from giye.extract.grounding import NOTE_KEY as GROUNDING_KEY
from giye.field import Field, edition_alias
from giye.ledger.ids import activity_id_for, activity_id_key, gy_number, mint_id
from giye.ledger.io import write_text_atomic
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import split_pipe
from giye.normalize.kinds import OFF_ACTIVITY_LIST, SESSION_REASON
from giye.normalize.rules import admitted_memberships, published_ids, roster_source_urls
from giye.publish.cite import citation_texts

# Practice records the site lists. Anything else is stored as ``other``.
ALLOWED_TYPES = {
    "solo_exhibition",
    "group_exhibition",
    "screening",
    "performance",
    "festival",
    "online_release",
    "award",
    "residency",
    "other",
}
# CV sections listed apart from practice. Scholarship and service stay unpublished.
BACKGROUND_SECTIONS = ["education", "employment", "teaching", "press"]
# Note keys that keep a background row off the site (see _background).
_BACKGROUND_OFF = re.compile(rf"(?:^|;)\s*(?:{GROUNDING_KEY}|suppressed)=")
# The only preprocessing flag a reader is shown. The row stays; the year is in doubt.
SHOWN_FLAGS = {"year_from_title"}

SNAPSHOT_FILES = (
    "artists.json",
    "activities.json",
    "links.json",
    "collaborations.json",
    "background.json",
    "frames.json",
    "dataset_versions.json",
    "coverage.json",
    "artist_stubs.json",
    "gy_redirects.json",
    "citations.json",
    "vocabularies.json",
    "content_pages.json",
    "content_revisions.json",
    "research.json",
)


@dataclass
class PublishResult:
    """Counts and paths written by :func:`publish`.

    ``files`` lists every snapshot path touched, including JSON arrays left
    empty when the archive has not authored them yet.
    """

    site: Path
    artists: int
    activities: int
    links: int
    frames: int
    stubs: int
    redirects: int
    files: list[Path] = field(default_factory=list)


def publish(config: Config, *, now: datetime | None = None) -> PublishResult:
    """Write the snapshot under ``config.site`` and return the counts and paths.

    ``now`` defaults to the current UTC time and stamps generation, citation
    access dates, and the version row. Who is a page, who is a stub, and F4
    coverage follow docs/RULES.md (publication).
    """
    from giye.config import checked_frames

    _require_site_url(config)
    registry = checked_frames(config)
    clock = _clock(now)
    stamp = clock.strftime("%Y-%m-%dT%H:%M:%SZ")
    today = clock.date().isoformat()
    ledger = Ledger.open(config)
    artists_in = ledger.read("artists")
    acts_in = ledger.read("activities")
    links_in = ledger.read("links")
    membership = ledger.read("frame_membership")
    _check_membership(registry, membership)
    prefix = config.id_prefix or "GY"
    roster_role = _activity_roster_roles(acts_in)
    frame_rows = list(_frames_document(config.frames).get("frames") or [])
    edition_of = _edition_resolver(frame_rows, config.field_config)
    decisions = {frame.code: frame.eligibility.decision for frame in registry.frames}
    public_membership = admitted_memberships(membership, decisions, edition_of)
    mem_by_ledger = _codes_by_ledger(public_membership)
    scope_rows = ledger.read("scope")
    scope = _scope_ids(scope_rows)
    hidden = _hidden_ids(artists_in)
    _fill_roster_sources(artists_in, roster_source_urls(public_membership, frame_rows, edition_of))
    published = published_ids(artists_in, membership, scope_rows, frame_rows, edition_of, decisions)
    publishable = _publishable_people(artists_in, published)
    ledger_to_gy = _assign_gy_ids(ledger, artists_in, publishable, prefix)
    _warn_gy_gaps(artists_in, ledger.read("gy_retired"), prefix)
    derived = _load_derived(config.processed / "artist_attributes.csv")
    processed_activities = config.processed / "activities.csv"
    flags = _load_activity_flags(processed_activities)
    kinds = _load_activity_kinds(processed_activities)
    cv_status = _cv_status(ledger.read("cv_sources"))
    same_name = _same_name(ledger.read("review_queue"), ledger_to_gy)
    tags = config.field_config.resolved()
    collective = _team_ids(config, publishable)
    members, member_of = _team_links(
        artists_in, membership, acts_in, ledger_to_gy, config.field_config.team_prefix or "팀:"
    )
    artists_out = [
        _artist_record(
            artist,
            gy=ledger_to_gy[artist["ledger_id"]],
            derived=derived.get(artist["ledger_id"], {}),
            codes=mem_by_ledger.get(artist["ledger_id"], []),
            edition_of=edition_of,
            roster_role=roster_role,
            cv_status=cv_status.get(artist["ledger_id"], "none"),
            same_name=same_name.get(artist["ledger_id"], []),
            stamp=stamp,
            tags=tags,
            collective=artist["ledger_id"] in collective,
            members=members.get(artist["ledger_id"], []),
            member_of=member_of.get(artist["ledger_id"], []),
        )
        for artist in publishable
    ]
    page_ids = {row["id"] for row in artists_out}
    stubs = _stubs(artists_in, page_ids)
    activities_out = _activities(acts_in, ledger_to_gy, flags, stamp, kinds)
    links_out = _links(links_in, ledger_to_gy, stamp)
    collaborations_out = _collaborations(ledger, ledger_to_gy)
    background_out = _background(acts_in, ledger_to_gy, clock.year, kinds)
    frames_out = _frames(frame_rows, public_membership, edition_of, ledger_to_gy, scope)
    _refuse_unpublished_roster(mem_by_ledger, ledger_to_gy, scope, hidden)
    active = _active_frame_count(frame_rows)
    coverage = _coverage_document(stamp, artists_out, artists_in, frame_rows, config, active)
    version = config.dataset_version or "0.2"
    redirects = _redirects(artists_in, ledger.read("gy_retired"))
    content = {
        "artists.json": artists_out,
        "activities.json": activities_out,
        "links.json": links_out,
        "collaborations.json": collaborations_out,
        "background.json": background_out,
        "frames.json": frames_out,
        "artist_stubs.json": stubs,
        "gy_redirects.json": redirects,
    }
    versions, current = _version_history(
        config.site / "dataset_versions.json",
        version,
        _content_digest(content, stamp),
        today,
        stamp,
        active,
        len(artists_out),
    )
    citations = _citations(config, artists_out, current, today)
    payloads = {
        "artists.json": artists_out,
        "activities.json": activities_out,
        "links.json": links_out,
        "collaborations.json": collaborations_out,
        "background.json": background_out,
        "frames.json": frames_out,
        "dataset_versions.json": versions,
        "coverage.json": coverage,
        "artist_stubs.json": stubs,
        "gy_redirects.json": redirects,
        "citations.json": citations,
    }
    files = _write_snapshot(config.site, payloads)
    # The ring reads the same published view. A ring written by an earlier
    # `giye explore` would still list a person hidden or unpublished since,
    # so publish rewrites it when one is there (`giye explore` writes it first).
    rim = config.site / "rim_order.json"
    if rim.exists():
        from giye.explore.rim import build_rim_order, write_rim_order

        files.append(write_rim_order(build_rim_order(config, now=clock), config.site))
    print(
        f"site artists={len(artists_out)} activities={len(activities_out)} "
        f"links={len(links_out)} frames={len(frames_out)}"
    )
    return PublishResult(
        site=config.site,
        artists=len(artists_out),
        activities=len(activities_out),
        links=len(links_out),
        frames=len(frames_out),
        stubs=len(stubs),
        redirects=len(redirects),
        files=files,
    )


def _require_site_url(config: Config) -> None:
    """Citations need a public origin. An empty ``site_url`` stops the build."""
    if not (config.site_url or "").strip():
        raise ConfigError(
            "[publish] site_url is required. Set it to this archive's public origin "
            "(the demo uses https://example.org). Citations use <site_url>/artist/<id> and <site_url>/data."
        )


def _check_membership(registry: FrameRegistry, membership: list[dict[str, str]]) -> None:
    """A transcribed-membership failure is a config error, the same as a bad frame file."""
    try:
        validate_transcribed_membership(registry, membership)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _activity_roster_roles(acts_in: list[dict]) -> dict[tuple[str, str], str]:
    """Role each person had on a roster edition. A later row with the same origin replaces it.

    A collector writes the frame code into the roster activity's origin. A CV origin is not a roster role.
    """
    return {
        (row["ledger_id"], row["origin"]): row["role"]
        for row in acts_in
        if row.get("role") and row.get("origin") and not row["origin"].startswith("cv:")
    }


def _edition_resolver(frame_rows: list, field: Field):
    """Membership code → (frame, edition), using this archive's field file."""
    frame_codes = [str(row.get("code") or "") for row in frame_rows]
    years_by_frame = {str(row.get("code") or ""): str(row.get("years_covered") or "") for row in frame_rows}

    def edition_of(mem_code: str) -> tuple[str, str | None] | None:
        return resolve_frame_edition(mem_code, frame_codes, years_by_frame, field=field)

    return edition_of


def _codes_by_ledger(public_membership: list[dict]) -> dict[str, list[str]]:
    """Person → membership codes, in file order."""
    mem_by_ledger: dict[str, list[str]] = {}
    for row in public_membership:
        mem_by_ledger.setdefault(row["ledger_id"], []).append(row["frame_code"])
    return mem_by_ledger


def _scope_ids(scope_rows: list[dict]) -> set[str]:
    """Ledger ids whose scope row says ``out``."""
    return {row["ledger_id"] for row in scope_rows if row.get("scope") == "out"}


def _hidden_ids(artists_in: list[dict]) -> set[str]:
    """People hidden by request. They stay on the roster; the tombstone is the page."""
    return {row["ledger_id"] for row in artists_in if row.get("status") == "HIDDEN_BY_REQUEST"}


def _fill_roster_sources(artists_in: list[dict], roster_url: dict[str, str]) -> None:
    """Copy a roster URL onto a person who has no http(s) source of their own."""
    for artist in artists_in:
        if not (artist.get("source_url") or "").startswith("http") and artist["ledger_id"] in roster_url:
            artist["source_url"] = roster_url[artist["ledger_id"]]


def _team_ids(config: Config, publishable: list[dict]) -> set[str]:
    """Ledger ids the T1 team test marks (``giye.resolve.teams.team_like``).

    The site's ``type`` vocabulary is ``individual`` / ``collective``. A team
    published as ``individual`` would be labelled a person and described as a
    schema.org Person, so the record carries the same verdict the resolver
    uses to keep teams and people apart.
    """
    from giye.normalize.language import language_for
    from giye.resolve.teams import team_like

    words = config.field_config.compiled_team_words()
    language = language_for(config)
    return {
        artist["ledger_id"] for artist in publishable if team_like(artist, words=words, language=language)
    }


def _team_links(
    artists_in: list[dict],
    membership: list[dict],
    acts_in: list[dict],
    ledger_to_gy: dict[str, str],
    team_prefix: str,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Team → published member ids, and member → published team ids, keyed by ledger id.

    The ledger already records who belongs to which team: the markers
    ``expand_teams`` (rule T2) writes, read by
    ``giye.resolve.teams.team_credits`` (a ``팀 구성원: <team> (<id>)`` note,
    a ``team:<id>`` membership, a ``<team prefix> <team>`` role on one of the
    team's editions). Only published records are linked, both ways, so a team
    page lists its members and a member's page names the team without the site
    deriving either. Ids are sorted so the snapshot does not depend on row order.
    """
    from giye.resolve.teams import team_credits

    members: dict[str, set[str]] = {}
    member_of: dict[str, set[str]] = {}
    for team_lid, lids in team_credits(artists_in, membership, acts_in, team_prefix).items():
        team_gy = ledger_to_gy.get(team_lid)
        if not team_gy:
            continue
        for lid in lids:
            gy = ledger_to_gy.get(lid)
            if not gy or lid == team_lid:
                continue
            members.setdefault(team_lid, set()).add(gy)
            member_of.setdefault(lid, set()).add(team_gy)
    return (
        {lid: sorted(ids) for lid, ids in members.items()},
        {lid: sorted(ids) for lid, ids in member_of.items()},
    )


def _publishable_people(artists_in: list[dict], published: set[str]) -> list[dict]:
    """Rows that become pages, in name order so a batch of new ids follows it.

    ``published`` comes from ``giye.normalize.rules.published_ids``, the same
    function P6 counts record depth on, so the site and the processed layer
    agree on who is published.
    """
    publishable = [artist for artist in artists_in if artist["ledger_id"] in published]
    publishable.sort(key=lambda row: row.get("name_ko") or row.get("name_en") or "")
    return publishable


def _stubs(artists_in: list[dict], published_ids: set[str]) -> dict[str, str]:
    """A ``gy_id`` that is not a page: hidden by request, otherwise withdrawn.

    The URL stays. The page has no name and no records.
    """
    return {
        artist["gy_id"]: "HIDDEN_BY_REQUEST" if artist.get("status") == "HIDDEN_BY_REQUEST" else "WITHDRAWN"
        for artist in artists_in
        if artist.get("gy_id") and artist["gy_id"] not in published_ids
    }


def _refuse_unpublished_roster(
    mem_by_ledger: dict[str, list[str]],
    ledger_to_gy: dict[str, str],
    scope: set[str],
    hidden: set[str],
) -> None:
    """Every admitted roster member is published, out of scope, or hidden.

    A member who is none of those stops the build: a roster row is evidence of
    participation and has to reach the site.
    """
    unpublished = sorted(set(mem_by_ledger) - set(ledger_to_gy) - scope - hidden)
    if unpublished:
        raise SystemExit(
            f"{len(unpublished)} roster members are not published (e.g. {unpublished[:5]}); "
            "every roster row must reach the site"
        )


def _active_frame_count(frame_rows: list) -> int:
    """How many frames have status ``active``."""
    return sum(1 for row in frame_rows if row.get("status") == "active")


def _coverage_document(
    stamp: str,
    artists_out: list[dict],
    artists_in: list[dict],
    frame_rows: list,
    config: Config,
    active: int,
) -> dict:
    """F4 coverage, copied from the rows :func:`_frames` already counted.

    Only a schedule the archive declares is stated publicly. Nothing is claimed by default.
    """
    coverage = {
        "generated_at": stamp,
        "published_artists": len(artists_out),
        "ledger_artists": len(artists_in),
        "frame_count_active": active,
        "frame_count_total": len(frame_rows),
        "frames": [
            {
                "code": row["code"],
                "roster_count": row.get("roster_count") or 0,
                "included_count": row.get("included_count") or 0,
                "roster_size_declared": row.get("roster_size_declared"),
                "coverage_pct": row.get("coverage_pct"),
                "status": row.get("status"),
                "last_fetched_at": row.get("last_fetched_at"),
            }
            for row in frame_rows
        ],
    }
    if config.cadence:
        coverage["cadence"] = dict(config.cadence)
    return coverage


def _content_digest(content: dict, stamp: str) -> str:
    """sha256 of the published content, without the build stamp.

    The stamp is a fallback for ``updated_at`` and similar fields, so it is
    removed: a rebuild of an unchanged ledger has the same digest.
    """
    text = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.replace(stamp, "").encode("utf-8")).hexdigest()


def _version_history(
    path: Path, version: str, digest: str, today: str, stamp: str, active: int, n_artists: int
) -> tuple[list[dict], dict]:
    """``dataset_versions.json`` with this build's row, and that row.

    The rows already published are kept as they are, and a row is appended
    only when the content changed (by ``content_digest``) or the version
    string changed. Why: one version string (``[publish] dataset_version``)
    covers every weekly rebuild, and a citation must say which content it
    names; the row's ``released_at`` is the date that content first appeared,
    so the history records what each citation pointed at. The id is stable
    for one version string and one content.
    """
    previous: list[dict] = []
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        previous = [row for row in loaded if isinstance(row, dict)] if isinstance(loaded, list) else []
    last = previous[-1] if previous else None
    if last and last.get("version") == version and last.get("content_digest") == digest:
        return previous, last
    row = {
        "id": mint_id(f"site-version\x1f{version}\x1f{digest}"),
        "version": version,
        "released_at": today,
        "doi": None,
        "notes": (
            f"Ledger snapshot · frames active={active} · "
            f"published={n_artists} · generated {stamp}"
        ),
        "artist_count": n_artists,
        "content_digest": digest,
    }
    return [*previous, row], row


def _write_snapshot(site: Path, payloads: dict) -> list[Path]:
    """Write each payload. A missing optional file is created once, as an empty list.

    Optional files are vocabularies, content pages, revisions, and research.
    An existing file is left as the archive wrote it.
    """
    site.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for name, payload in payloads.items():
        path = site / name
        _dump(path, payload)
        files.append(path)
    for name in ("vocabularies.json", "content_pages.json", "content_revisions.json", "research.json"):
        path = site / name
        if not path.exists():
            _dump(path, [])
        files.append(path)
    return files


def resolve_frame_edition(
    mem_code: str,
    registry: list[str],
    years_by_frame: dict[str, str],
    *,
    field: Field | None = None,
) -> tuple[str, str | None] | None:
    """Membership code → ``(registry frame, edition year)``.

    ``NORTH-2022`` → ``(NORTH, 2022)`` when ``NORTH`` is the longest registry
    code that is a prefix. A code equal to a registry row uses that row's
    ``years_covered`` when it is a single year.

    A field file may declare an edition alias: a membership code that names a
    registry frame and an edition even when the code is not that frame plus a
    year. The alias applies only when the target frame is in the registry.
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


def region_tags(country: str, region: str, *, field: Field | None = None) -> list[str]:
    """Coarse region tags from the field file's needle list.

    A home-country pattern with no finer tag, or any other non-empty text that
    matched nothing, becomes the field's fallback tag. An empty field list
    returns nothing.
    """
    vocab = (field or Field()).resolved()
    text = f"{country} {region}".strip()
    if not text or not vocab.region_map:
        return []
    out: list[str] = []
    for needle, tag in vocab.region_map:
        if needle in text and tag not in out:
            out.append(tag)
    if vocab.home_pattern and re.search(vocab.home_pattern, text, re.IGNORECASE):
        if not out and vocab.region_fallback:
            out.append(vocab.region_fallback)
    elif text and not out and vocab.region_fallback:
        out.append(vocab.region_fallback)
    return out


def guess_medium(field_name: str, category: str, *, field: Field | None = None) -> list[str]:
    """Medium tags guessed from the artist row's field and category.

    The patterns are the field file's. Derived ``medium`` values are used
    when this returns nothing.
    """
    vocab = (field or Field()).resolved()
    text = f"{field_name} {category}".lower()
    tags: list[str] = []
    for tag, pattern in vocab.medium_guess:
        if re.search(pattern, text, re.IGNORECASE):
            tags.append(tag)
    return tags


def field_keywords(text: str) -> list[str]:
    """The ledger's ``field`` cell as a list of keywords, in the order written.

    Split on commas outside brackets ("Development (e.g., Web, Game), XR" is two
    keywords), whitespace collapsed, empty parts dropped. The keywords are shown as
    written: they name fields of research or practice, which the medium vocabulary
    does not cover, so they are not mapped onto it.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text or "":
        if char in "([{（［":
            depth += 1
        elif char in ")]}）］":
            depth = max(depth - 1, 0)
        if char in ",，" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [" ".join(part.split()) for part in parts if part.strip()]


def parse_year(value: object) -> int | None:
    """First 19xx or 20xx in ``value``, or None.

    A year outside that window is not a publication year. Activities, background,
    and collaborations all use this reading.
    """
    if value is None or value == "":
        return None
    match = re.search(r"(19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


def _clock(now: datetime | None) -> datetime:
    """UTC clock for snapshot stamps. A naive datetime is taken as UTC."""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _dump(path: Path, payload: object) -> None:
    """Write JSON the site reads: UTF-8, non-ASCII kept, two-space indent, trailing newline."""
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _frames_document(path: Path) -> dict:
    """Validate F1–F5, then return the raw document so optional keys survive.

    The loader's dataclass drops ``stage``, ``last_fetched_at``, and
    ``edition_labels``. The snapshot publishes those as written.
    """
    load_frames(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: expected a mapping")
    return raw


def _assign_gy_ids(
    ledger: Ledger, artists_in: list[dict], publishable: list[dict], prefix: str
) -> dict[str, str]:
    """Issue a permanent id to a publishable row that does not have one yet.

    The next number is one past the highest ever issued, retired ids included.
    ``publishable`` is already in name order, so a batch of new ids follows that
    order. An id already on the row is kept. The collector usually issued the
    id at insert; a row that reaches publish without one is numbered here and
    the ledger is written, because the id is permanent.
    """
    retired = ledger.read("gy_retired")
    issued = [gy_number(row.get("gy_id", ""), prefix=prefix) for row in artists_in]
    issued += [gy_number(row.get("gy_id", ""), prefix=prefix) for row in retired]
    next_no = max(issued, default=0) + 1
    fresh = 0
    for artist in publishable:
        if not artist.get("gy_id"):
            artist["gy_id"] = f"{prefix}-{next_no:06d}"
            next_no += 1
            fresh += 1
    if fresh:
        ledger.write("artists", artists_in, task="publish")
        print(f"issued {fresh} new GY ids (up to {prefix}-{next_no - 1:06d})")
    ids = [row["gy_id"] for row in artists_in if row.get("gy_id")]
    dup = {gy for gy in ids if ids.count(gy) > 1} if len(ids) != len(set(ids)) else set()
    if dup:
        raise SystemExit(f"GY id held by more than one ledger row: {sorted(dup)[:5]}")
    return {row["ledger_id"]: row["gy_id"] for row in publishable}


def _warn_gy_gaps(artists_in: list[dict], retired: list[dict], prefix: str) -> None:
    """Warn when a number up to the highest issued id is neither held nor retired.

    A gap means a published row was deleted. The build still writes the snapshot.
    """
    held = {gy_number(row.get("gy_id", ""), prefix=prefix) for row in artists_in}
    held |= {gy_number(row.get("gy_id", ""), prefix=prefix) for row in retired}
    held.discard(0)
    gaps = [number for number in range(1, max(held, default=0) + 1) if number not in held]
    if gaps:
        shown = ", ".join(f"{prefix}-{number:06d}" for number in gaps[:10])
        print(f"WARNING: {len(gaps)} GY ids issued but held by no row and not retired: {shown}")


def _load_derived(path: Path) -> dict[str, dict[str, dict]]:
    """``artist_attributes.csv`` → ledger id → field → row. Medium tags gather under ``values``."""
    from giye.ledger.io import read_csv

    out: dict[str, dict[str, dict]] = {}
    for row in read_csv(path) if path.exists() else []:
        fields = out.setdefault(row["ledger_id"], {})
        if row["field"] == "record_depth":
            # P6 feeds the depth queue. Copying it into artist.derived would add
            # a key on every published person, so the snapshot omits it.
            continue
        if row["field"] == "medium":
            bucket = fields.setdefault("medium", {**row, "values": []})
            bucket["values"].append(row["value"])
        else:
            fields[row["field"]] = row
    return out


def _derived_value(derived: dict, name: str, cast=str):
    """Cast one derived value, or None when the field is missing or blank."""
    value = derived.get(name, {}).get("value")
    return cast(value) if value not in (None, "") else None


@dataclass(frozen=True)
class _KindRow:
    """K1 columns of one processed activity. ``session`` is the ``roster_session`` guard."""

    kind: str
    channel: str
    session: bool


def _load_activity_kinds(path: Path) -> dict[str, _KindRow]:
    """``activity_kind`` / ``activity_channel`` written by K1, keyed by activity id.

    A file from before K1 has neither column. Those rows are left out of the
    dict, and the activity list keeps the decision it had then.
    """
    from giye.ledger.io import read_csv

    out: dict[str, _KindRow] = {}
    for row in read_csv(path) if path.exists() else []:
        kind = (row.get("activity_kind") or "").strip()
        channel = (row.get("activity_channel") or "").strip()
        if not kind and not channel:
            continue
        rules = {part for part in (row.get("rules") or "").split("|") if part}
        out[row.get("activity_id") or ""] = _KindRow(kind, channel, SESSION_REASON in rules)
    out.pop("", None)
    return out


def _listed_activity(kind: _KindRow | None, origin: str) -> bool:
    """Whether K1 lets this row onto the activity list.

    Missing K1 keeps the pre-K1 decision (the caller still requires
    ``publishable=yes``). Education, teaching, employment, and service stay
    off the list. A CV row of any other kind stays, so a press or funding
    line still follows ``publishable`` as it did before K1. A roster row
    leaves only when its channel is not ``activity``. A numbered session
    stays: the background block does not read ``publishable``, so dropping
    ``roster_session`` would publish rows the activity list had held back.
    """
    if kind is None or kind.session:
        return True
    if kind.kind in OFF_ACTIVITY_LIST:
        return False
    if origin.startswith("cv:") or not kind.channel:
        return True
    return kind.channel == "activity"


def _load_activity_flags(path: Path) -> dict[str, list[str]]:
    """Flags a reader is shown. Only ``year_from_title`` (P1) is copied onto the row."""
    from giye.ledger.io import read_csv

    out: dict[str, list[str]] = {}
    for row in read_csv(path) if path.exists() else []:
        shown = [flag for flag in (row.get("flags") or "").split("|") if flag in SHOWN_FLAGS]
        if shown:
            out[row["activity_id"]] = shown
    return out


def _cv_status(sources: list[dict]) -> dict[str, str]:
    """CV state per person: ``found`` once a snapshot exists, else ``pending``. Inactive rows are ignored."""
    out: dict[str, str] = {}
    for row in sources:
        if row.get("active", "true") != "true":
            continue
        status = "found" if row.get("snapshot_path") else "pending"
        if out.get(row["ledger_id"]) != "found":
            out[row["ledger_id"]] = status
    return out


def _same_name(review: list[dict], ledger_to_gy: dict[str, str]) -> dict[str, list[str]]:
    """Open same-name reviews as other published ids. They are notes, not merges."""
    pairs: dict[str, set[str]] = {}
    for row in review:
        if row.get("reason") != "possible_same_person" or row.get("status") != "open":
            continue
        ids = {row.get("ledger_id") or "", *re.findall(r"(?:CAND|LED)-[0-9A-Za-z]+", row.get("detail") or "")}
        ids = {item for item in ids if item in ledger_to_gy}
        for item in ids:
            pairs.setdefault(item, set()).update(ids - {item})
    return {item: sorted(ledger_to_gy[other] for other in others) for item, others in pairs.items() if others}


def _artist_record(
    artist: dict,
    *,
    gy: str,
    derived: dict,
    codes: list[str],
    edition_of,
    roster_role: dict[tuple[str, str], str],
    cv_status: str,
    same_name: list[str],
    stamp: str,
    tags: Field | None = None,
    collective: bool = False,
    members: list[str] | None = None,
    member_of: list[str] | None = None,
) -> dict:
    """One ``artists.json`` record. ``collective`` is the T1 team test's verdict for this row.

    ``members`` and ``member_of`` are the published ids linked through team
    membership (see ``_team_links``).
    """
    editions = []
    seen = []
    for code in codes:
        item = (edition_of(code) or (None, None)) + (code,)
        if item not in seen:
            seen.append(item)
    for frame, edition, code in seen:
        if not frame:
            continue
        record = {"frame": frame, "edition": edition}
        role = roster_role.get((artist["ledger_id"], code))
        if role:
            record["role"] = role
        editions.append(record)
    return {
        "id": gy,
        "name_ko": artist.get("name_ko") or artist.get("name_en") or "이름 미상",
        "name_en": artist.get("name_en") or None,
        "aliases": split_pipe(artist.get("aliases")),
        "type": "collective" if collective else "individual",
        "bio_short": None,
        "birth_year": _derived_value(derived, "birth_year", int),
        "birth_year_source_url": derived.get("birth_year", {}).get("evidence_url") or None,
        "active_since": parse_year(artist.get("active_since")) or _derived_value(derived, "active_since", int),
        "regions": region_tags(artist.get("country") or "", artist.get("region") or "", field=tags)
        or split_pipe(derived.get("region", {}).get("value")),
        "countries": split_pipe(derived.get("country", {}).get("value")),
        "medium_tags": guess_medium(artist.get("field") or "", artist.get("category") or "", field=tags)
        or derived.get("medium", {}).get("values", []),
        "field_keywords": field_keywords(artist.get("field") or ""),
        "derived": {
            name: {"rule": row["rule"], **({"url": row["evidence_url"]} if row.get("evidence_url") else {})}
            for name, row in derived.items()
        },
        "technique_tags": [],
        "theme_tags": [],
        "frame_status": artist.get("frame_status") or ("IN_FRAME" if codes else "OUT_OF_FRAME"),
        "frame_codes": codes,
        "frame_editions": editions,
        "verification": artist.get("verification") or "UNVERIFIED",
        "cv_status": cv_status,
        "same_name": same_name,
        "members": members or [],
        "member_of": member_of or [],
        "status": "PUBLISHED",
        "source_url": artist["source_url"],
        "source_type": artist.get("source_type") or "PUBLIC_RECORD",
        "collected_at": artist["collected_at"].strip()[:10],
        "external_ids": {"ledger_id": artist["ledger_id"]},
        "created_at": stamp,
        "updated_at": artist.get("updated_at") or stamp,
    }


def _row_activity_id(row: dict) -> str:
    """Keep an id already on the row. Otherwise mint one from the row's identity fields."""
    existing = (row.get("activity_id") or "").strip()
    if existing:
        return existing
    origin = row.get("origin") or ""
    cv = origin.startswith("cv:")
    key = activity_id_key(
        ledger_id=row.get("ledger_id") or "",
        source=origin[3:] if cv else (row.get("source_url") or ""),
        title=row.get("title") or "",
        year=str(row.get("year") or ""),
        activity_type=row.get("activity_type") or "other",
        venue=row.get("venue") or "",
        origin="" if cv else origin,
    )
    return activity_id_for(key, 0)


def _activities(
    rows: list[dict],
    ledger_to_gy: dict[str, str],
    flags: dict[str, list[str]],
    stamp: str,
    kinds: dict[str, _KindRow],
) -> list[dict]:
    out = []
    for row in rows:
        gy = ledger_to_gy.get(row.get("ledger_id") or "")
        if not gy or row.get("publishable") != "yes":
            continue
        if not _listed_activity(kinds.get(row.get("activity_id") or ""), row.get("origin") or ""):
            continue
        title = (row.get("title") or "").strip()
        year = parse_year(row.get("year"))
        source_url = (row.get("source_url") or "").strip()
        collected_at = (row.get("collected_at") or "").strip()[:10]
        # No source URL or no collection date: the row is not published.
        if not title or year is None or not source_url.startswith("http") or not collected_at:
            continue
        activity_type = row.get("activity_type") or "other"
        if activity_type not in ALLOWED_TYPES:
            activity_type = "other"
        record = {
            "id": _row_activity_id(row),
            "artist_id": gy,
            "title": title[:500],
            "venue": row.get("venue") or None,
            "year": year,
            "activity_type": activity_type,
            "role": row.get("role") or None,
            "source_url": source_url,
            "source_type": row.get("source_type") or "SELF_SUBMITTED",
            "collected_at": collected_at,
            "created_at": stamp,
        }
        if row.get("activity_id") in flags:
            record["flags"] = flags[row["activity_id"]]
        out.append(record)
    # Year and title are not unique. The id keeps the order when a later apply
    # rewrites the same rows, so a second run publishes the same bytes.
    out.sort(key=lambda item: (-item["year"], item["title"], item["id"]))
    return out


def _links(rows: list[dict], ledger_to_gy: dict[str, str], stamp: str) -> list[dict]:
    out = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        gy = ledger_to_gy.get(row.get("ledger_id") or "")
        if not gy:
            continue
        url = row.get("url") or ""
        if not url.startswith("http"):
            continue
        key = (gy, url)
        if key in seen:
            continue
        seen.add(key)
        existing = (row.get("link_id") or "").strip()
        link_id = existing or mint_id("site-link\x1f" + (row.get("ledger_id") or "") + "\x1f" + url)
        out.append(
            {
                "id": link_id,
                "artist_id": gy,
                "label": row.get("label") or "Link",
                "url": url,
                "link_type": row.get("link_type") or "website",
                "last_checked_at": row.get("last_checked_at") or None,
                "http_status": row.get("http_status") or None,
                "is_dead": str(row.get("is_dead")).lower() in ("1", "true", "yes"),
                "created_at": stamp,
            }
        )
    return out


def _collaborations(ledger: Ledger, ledger_to_gy: dict[str, str]) -> list[dict]:
    people = {row["collaborator_id"]: row for row in ledger.read("collaborators")}
    out = []
    for row in ledger.read("collaborations"):
        gy = ledger_to_gy.get(row.get("ledger_id") or "")
        person = people.get(row.get("collaborator_id") or "")
        if not gy or not person or row.get("publishable") != "yes":
            continue
        source_url = (row.get("source_url") or person.get("source_url") or "").strip()
        collected_at = (row.get("collected_at") or "").strip()[:10]
        if not source_url.startswith("http") or not collected_at:
            continue
        out.append(
            {
                "id": row["collaboration_id"],
                "artist_id": gy,
                "collaborator_id": person["collaborator_id"],
                "name_ko": person.get("name_ko") or None,
                "name_en": person.get("name_en") or None,
                "affiliation": person.get("affiliation") or None,
                "lab": person.get("lab") or None,
                "role": person.get("role") or None,
                "frame_code": row.get("frame_code") or None,
                "year": parse_year(row.get("year")),
                "topic": row.get("topic") or None,
                "source_url": source_url,
                "collected_at": collected_at,
            }
        )
    out.sort(key=lambda item: (-(item["year"] or 0), item["name_ko"] or item["name_en"] or ""))
    return out


def _background(
    rows: list[dict], ledger_to_gy: dict[str, str], year_now: int, kinds: dict[str, _KindRow]
) -> list[dict]:
    """CV education, employment, teaching, and press, plus roster teaching posts.

    Scholarship-like titles and a future upcoming year stay off. A roster row
    is added only when K1 says ``background`` and ``teaching``. ``roster_session``
    is not that row: a numbered session stays on the activity channel, because
    this path does not read ``publishable`` and does not treat ``예정`` as
    ``upcoming``. ``publishable`` is not a gate for either path.
    """
    out = []
    seen: set[tuple] = set()
    for row in rows:
        gy = ledger_to_gy.get(row.get("ledger_id") or "")
        match = re.search(r"cv_section=(\w+)", row.get("reviewer_note") or "")
        origin = row.get("origin") or ""
        kind = kinds.get(row.get("activity_id") or "")
        section = ""
        if gy and match and match.group(1) in BACKGROUND_SECTIONS and origin.startswith("cv:"):
            section = match.group(1)
        elif (
            gy
            and kind is not None
            and not kind.session
            and not origin.startswith("cv:")
            and kind.channel == "background"
            and kind.kind == "teaching"
        ):
            section = "teaching"
        if not section:
            continue
        # Apply sets publishable=no on every background row as a section
        # marker, so publishable cannot carry these two decisions here: a row
        # that failed grounding (ungrounded=…) or that a curator took off the
        # site (suppressed=<reason>, e.g. on a correction request) stays off.
        if _BACKGROUND_OFF.search(row.get("reviewer_note") or ""):
            continue
        title = (row.get("title") or "").strip()
        year = parse_year(row.get("year"))
        source_url = (row.get("source_url") or "").strip()
        collected_at = (row.get("collected_at") or "").strip()[:10]
        if not title or year is None or not source_url.startswith("http") or not collected_at:
            continue
        if "upcoming" in (row.get("reviewer_note") or "") and year > year_now:
            continue
        if PRIVATE_TITLE.search(f"{title} {row.get('role') or ''}"):
            continue
        key = (gy, section, year, re.sub(r"[\W_]+", "", title.lower()))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "id": _row_activity_id(row),
                "artist_id": gy,
                "section": section,
                "title": title[:500],
                "venue": row.get("venue") or None,
                "year": year,
                "role": row.get("role") or None,
                "source_url": source_url,
                "source_type": row.get("source_type") or "SELF_SUBMITTED",
                "collected_at": collected_at,
            }
        )
    out.sort(key=lambda item: (item["artist_id"], BACKGROUND_SECTIONS.index(item["section"]), -item["year"], item["title"]))
    return out


def _frames(frame_rows, membership, edition_of, ledger_to_gy, scope) -> list[dict]:
    out = []
    for order, row in enumerate(frame_rows):
        code = str(row.get("code") or "")
        matched = {item["ledger_id"] for item in membership if _matches(edition_of, code, item["frame_code"])}
        by_edition: dict[str, set[str]] = {}
        for item in membership:
            edition = edition_of(item["frame_code"])
            if edition and edition[0] == code:
                by_edition.setdefault(edition[1] or "", set()).add(item["ledger_id"])
        labels = {str(key): value for key, value in (row.get("edition_labels") or {}).items()}
        editions = [
            {
                "edition": edition or None,
                "label": labels.get(edition) or None,
                "roster_count": len(ids),
                "published_count": sum(1 for lid in ids if lid in ledger_to_gy),
                "out_of_scope_count": sum(1 for lid in ids if lid in scope),
            }
            for edition, ids in sorted(by_edition.items(), key=lambda pair: pair[0], reverse=True)
        ]
        _declare_edition_sizes(editions, declared_sizes_by_edition(code, row.get("roster_size_declared_by_edition")))
        declared = int(row.get("roster_count") or 0)
        roster = max(declared, len(matched)) if matched or declared else 0
        included = len(matched)
        published = sum(1 for lid in matched if lid in ledger_to_gy)
        declared_size = _declared_size(row)
        pct = _coverage_pct(included, max(declared_size, included)) if declared_size else None
        row["roster_count"] = roster
        row["included_count"] = included
        row["coverage_pct"] = pct
        out.append(
            {
                "id": mint_id(f"site-frame\x1f{code}"),
                "code": code,
                "name_ko": row.get("name_ko") or "",
                "name_en": row.get("name_en"),
                "years_covered": row.get("years_covered"),
                "source_url": row["source_url"],
                "included_count": included,
                "published_count": published,
                "roster_count": roster,
                "roster_size_declared": declared_size or None,
                "roster_size_source": (row.get("roster_size_source") or None) if declared_size else None,
                "coverage_pct": pct,
                "status": row.get("status"),
                "stage": row.get("stage"),
                "last_fetched_at": row.get("last_fetched_at"),
                "order": order,
                "editions": editions,
                "eligibility": row.get("eligibility") or None,
            }
        )
    return out


def _matches(edition_of, frame_code: str, mem_code: str) -> bool:
    """True when the membership code resolves to this frame."""
    edition = edition_of(mem_code)
    return bool(edition) and edition[0] == frame_code


def _declare_edition_sizes(editions: list[dict], sizes: Mapping) -> None:
    """Add the declared size, its source, and coverage to each edition that states its size (F4)."""
    for entry in editions:
        declared = sizes.get(entry["edition"] or "")
        if declared is None:
            continue
        recorded = entry["roster_count"]
        entry["roster_size_declared"] = declared.size
        entry["roster_size_source"] = declared.source
        entry["coverage_pct"] = _coverage_pct(recorded, max(declared.size, recorded))


def _declared_size(row: Mapping) -> int:
    """``roster_size_declared`` as an int, 0 when unset. The loader already required its source."""
    value = row.get("roster_size_declared")
    return int(value) if value not in (None, "") else 0


def _coverage_pct(included: int, roster: int) -> float | None:
    if roster <= 0:
        return None
    return round(100.0 * included / roster, 1)


def _redirects(artists_in: list[dict], retired: list[dict]) -> dict[str, str]:
    gy_of = {row["ledger_id"]: row.get("gy_id", "") for row in artists_in}
    return {
        row["gy_id"]: gy_of[row["merged_into_ledger_id"]]
        for row in retired
        if row.get("gy_id") and gy_of.get(row.get("merged_into_ledger_id", ""))
    }


def _citations(config: Config, artists: list[dict], current: dict, today: str) -> dict:
    """Citations of the dataset and of each person, naming the current version row.

    The version label and the year come from that row (``version``, and the
    ``released_at`` of its content), not from the build clock or a person's
    ``updated_at``: a rebuild of unchanged content then cites the same way,
    and different content cites a different date.
    """
    title = config.dataset_title or config.name
    author = config.citation_author
    origin = config.site_url.rstrip("/")
    dataset_url = f"{origin}/data"
    version = str(current["version"])
    released = str(current["released_at"])
    year = int(released[:4])
    dataset = {
        "title": title,
        "author": author,
        "version": version,
        "released_at": released,
        "url": dataset_url,
        "year": year,
        "accessed": today,
        **citation_texts(
            author=author,
            title=title,
            record_id=None,
            version=version,
            released=released,
            url=dataset_url,
            year=year,
            accessed=today,
        ),
    }
    people = []
    for artist in artists:
        url = f"{origin}/artist/{artist['id']}"
        people.append(
            {
                "id": artist["id"],
                "title": artist.get("name_ko") or artist["id"],
                "url": url,
                "year": year,
                "accessed": today,
                **citation_texts(
                    author=author,
                    title=str(artist.get("name_ko") or artist["id"]),
                    record_id=artist["id"],
                    version=version,
                    released=released,
                    url=url,
                    year=year,
                    accessed=today,
                ),
            }
        )
    return {"dataset": dataset, "artists": people}
