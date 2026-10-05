# SPDX-License-Identifier: AGPL-3.0-only
"""Build the site snapshot (``<data>/site/*.json``) from the ledger.

Ported from ``scripts/build_site_dataset.py``. The JSON objects keep the
production keys. Two production side effects are not done here: rewriting
``frames.yml`` (the counts live in ``frames.json`` and ``coverage.json``), and
building the embedding flight file (stage 6, not ported). ``citations.json``
is new: the production site built the same sentences in the browser.

Who is published: a ledger row that is in scope, has an http(s) source, is on a
roster or has ``cv_link_ok=yes``, and whose status is empty, ``PUBLISHED``, or
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
``round(100 * included / roster, 1)``. ``roster`` is the greater of the declared
``roster_count`` and the membership count. Production's comment says the declared
size is used when it is set; the code uses the maximum, and this follows the code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from giye.collect.frames import is_admitted, load_frames, validate_transcribed_membership
from giye.config import Config, ConfigError
from giye.extract.apply import PRIVATE_TITLE
from giye.field import Field, edition_alias
from giye.ledger.ids import activity_id_for, activity_id_key, gy_number, mint_id
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import split_pipe
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
    site: Path
    artists: int
    activities: int
    links: int
    frames: int
    stubs: int
    redirects: int
    files: list[Path] = field(default_factory=list)


def publish(config: Config, *, now: datetime | None = None) -> PublishResult:
    """Write the snapshot under ``config.site``. ``now`` defaults to the current UTC time."""
    from giye.config import checked_frames

    if not (config.site_url or "").strip():
        raise ConfigError(
            "[publish] site_url is required. Set it to this archive's public origin "
            "(the demo uses https://example.org). Citations use <site_url>/artist/<id> and <site_url>/data."
        )
    registry = checked_frames(config)
    clock = _clock(now)
    stamp = clock.strftime("%Y-%m-%dT%H:%M:%SZ")
    today = clock.date().isoformat()
    ledger = Ledger.open(config)
    artists_in = ledger.read("artists")
    acts_in = ledger.read("activities")
    links_in = ledger.read("links")
    membership = ledger.read("frame_membership")
    try:
        validate_transcribed_membership(registry, membership)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    prefix = config.id_prefix or "GY"

    # A collector writes the frame code into the roster activity's origin, so this is the
    # role each person had on that edition. A later row with the same origin replaces it.
    roster_role = {
        (row["ledger_id"], row["origin"]): row["role"]
        for row in acts_in
        if row.get("role") and row.get("origin") and not row["origin"].startswith("cv:")
    }
    frames_doc = _frames_document(config.frames)
    frame_rows = list(frames_doc.get("frames") or [])
    frame_codes = [str(row.get("code") or "") for row in frame_rows]
    years_by_frame = {str(row.get("code") or ""): str(row.get("years_covered") or "") for row in frame_rows}

    def edition_of(mem_code: str) -> tuple[str, str | None] | None:
        return resolve_frame_edition(mem_code, frame_codes, years_by_frame, field=config.field_config)

    decisions = {frame.code: frame.eligibility.decision for frame in registry.frames}

    def admitted_membership(mem_code: str) -> bool:
        """A code that does not resolve is not a recorded decision, so it stays."""
        resolved = edition_of(mem_code)
        if resolved is None:
            return True
        return is_admitted(decisions.get(resolved[0], ""))

    # Coverage still lists every frame. Memberships of a frame that was not
    # admitted are not part of the published roster.
    public_membership = [row for row in membership if admitted_membership(row["frame_code"])]

    mem_by_ledger: dict[str, list[str]] = {}
    for row in public_membership:
        mem_by_ledger.setdefault(row["ledger_id"], []).append(row["frame_code"])

    scope = {row["ledger_id"] for row in ledger.read("scope") if row.get("scope") == "out"}
    # A hidden person stays on the roster. That is not a row the pipeline forgot
    # to publish: the tombstone is the page. Scope-out is the other exclusion.
    hidden = {row["ledger_id"] for row in artists_in if row.get("status") == "HIDDEN_BY_REQUEST"}
    frame_url = {str(row.get("code") or ""): row.get("source_url") or "" for row in frame_rows}
    roster_url: dict[str, str] = {}
    for row in public_membership:
        edition = edition_of(row["frame_code"])
        for url in (row.get("source_url") or "", frame_url.get(edition[0], "") if edition else ""):
            if str(url).startswith("http"):
                roster_url.setdefault(row["ledger_id"], str(url))
                break
    for artist in artists_in:
        if not (artist.get("source_url") or "").startswith("http") and artist["ledger_id"] in roster_url:
            artist["source_url"] = roster_url[artist["ledger_id"]]

    publishable = [
        artist
        for artist in artists_in
        if artist["ledger_id"] not in scope
        and (artist.get("cv_link_ok") == "yes" or artist["ledger_id"] in mem_by_ledger)
        and (artist.get("source_url") or "").startswith("http")
        # A fact without its collection date is not published, like one without a source.
        and (artist.get("collected_at") or "").strip()
        and artist.get("status") in ("", "PUBLISHED", "STAGED")
    ]
    publishable.sort(key=lambda row: row.get("name_ko") or row.get("name_en") or "")
    ledger_to_gy = _assign_gy_ids(ledger, artists_in, publishable, prefix)
    _warn_gy_gaps(artists_in, ledger.read("gy_retired"), prefix)

    derived = _load_derived(config.processed / "artist_attributes.csv")
    flags = _load_activity_flags(config.processed / "activities.csv")
    cv_status = _cv_status(ledger.read("cv_sources"))
    same_name = _same_name(ledger.read("review_queue"), ledger_to_gy)

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
            tags=config.field_config.resolved(),
        )
        for artist in publishable
    ]
    published_ids = {row["id"] for row in artists_out}
    stubs = {
        artist["gy_id"]: "HIDDEN_BY_REQUEST" if artist.get("status") == "HIDDEN_BY_REQUEST" else "WITHDRAWN"
        for artist in artists_in
        if artist.get("gy_id") and artist["gy_id"] not in published_ids
    }
    activities_out = _activities(acts_in, ledger_to_gy, flags, stamp)
    links_out = _links(links_in, ledger_to_gy, stamp)
    collaborations_out = _collaborations(ledger, ledger_to_gy)
    background_out = _background(acts_in, ledger_to_gy, clock.year)
    frames_out = _frames(frame_rows, public_membership, edition_of, ledger_to_gy, scope)
    unpublished = sorted(set(mem_by_ledger) - set(ledger_to_gy) - scope - hidden)
    if unpublished:
        raise SystemExit(
            f"{len(unpublished)} roster members are not published (e.g. {unpublished[:5]}); "
            "every roster row must reach the site"
        )
    active = sum(1 for row in frame_rows if row.get("status") == "active")
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
                "coverage_pct": _coverage_pct(int(row.get("included_count") or 0), int(row.get("roster_count") or 0)),
                "status": row.get("status"),
                "last_fetched_at": row.get("last_fetched_at"),
            }
            for row in frame_rows
        ],
    }
    # Only a schedule the archive declares is stated publicly. The package runs
    # no link check on its own, so nothing is claimed by default.
    if config.cadence:
        coverage["cadence"] = dict(config.cadence)
    version = config.dataset_version or "0.2"
    versions = [
        {
            "id": mint_id(f"site-version\x1f{version}"),
            "version": version,
            "released_at": today,
            "doi": None,
            "notes": (
                f"Ledger snapshot · frames active={active} · "
                f"published={len(artists_out)} · generated {stamp}"
            ),
            "artist_count": len(artists_out),
        }
    ]
    citations = _citations(config, artists_out, version, today, clock.year)
    redirects = _redirects(artists_in, ledger.read("gy_retired"))

    site = config.site
    site.mkdir(parents=True, exist_ok=True)
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
    print(
        f"site artists={len(artists_out)} activities={len(activities_out)} "
        f"links={len(links_out)} frames={len(frames_out)}"
    )
    return PublishResult(
        site=site,
        artists=len(artists_out),
        activities=len(activities_out),
        links=len(links_out),
        frames=len(frames_out),
        stubs=len(stubs),
        redirects=len(redirects),
        files=files,
    )


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


def parse_year(value: object) -> int | None:
    """First 19xx or 20xx in ``value``, or None. Same as production ``parse_year``."""
    if value is None or value == "":
        return None
    match = re.search(r"(19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


def _clock(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    order. An id already on the row is kept. The collector in this package
    usually issued the id at insert; production issued it here.
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
            # a key on every published person. Production's loader skips it.
            continue
        if row["field"] == "medium":
            bucket = fields.setdefault("medium", {**row, "values": []})
            bucket["values"].append(row["value"])
        else:
            fields[row["field"]] = row
    return out


def _derived_value(derived: dict, name: str, cast=str):
    value = derived.get(name, {}).get("value")
    return cast(value) if value not in (None, "") else None


def _load_activity_flags(path: Path) -> dict[str, list[str]]:
    from giye.ledger.io import read_csv

    out: dict[str, list[str]] = {}
    for row in read_csv(path) if path.exists() else []:
        shown = [flag for flag in (row.get("flags") or "").split("|") if flag in SHOWN_FLAGS]
        if shown:
            out[row["activity_id"]] = shown
    return out


def _cv_status(sources: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in sources:
        if row.get("active", "true") != "true":
            continue
        status = "found" if row.get("snapshot_path") else "pending"
        if out.get(row["ledger_id"]) != "found":
            out[row["ledger_id"]] = status
    return out


def _same_name(review: list[dict], ledger_to_gy: dict[str, str]) -> dict[str, list[str]]:
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
) -> dict:
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
        "type": "individual",
        "bio_short": None,
        "birth_year": _derived_value(derived, "birth_year", int),
        "birth_year_source_url": derived.get("birth_year", {}).get("evidence_url") or None,
        "active_since": parse_year(artist.get("active_since")) or _derived_value(derived, "active_since", int),
        "regions": region_tags(artist.get("country") or "", artist.get("region") or "", field=tags)
        or split_pipe(derived.get("region", {}).get("value")),
        "countries": split_pipe(derived.get("country", {}).get("value")),
        "medium_tags": guess_medium(artist.get("field") or "", artist.get("category") or "", field=tags)
        or derived.get("medium", {}).get("values", []),
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
        "status": "PUBLISHED",
        "source_url": artist["source_url"],
        "source_type": artist.get("source_type") or "PUBLIC_RECORD",
        "collected_at": artist["collected_at"].strip()[:10],
        "external_ids": {"ledger_id": artist["ledger_id"]},
        "created_at": stamp,
        "updated_at": artist.get("updated_at") or stamp,
    }


def _row_activity_id(row: dict) -> str:
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


def _activities(rows: list[dict], ledger_to_gy: dict[str, str], flags: dict[str, list[str]], stamp: str) -> list[dict]:
    out = []
    for row in rows:
        gy = ledger_to_gy.get(row.get("ledger_id") or "")
        if not gy or row.get("publishable") != "yes":
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


def _background(rows: list[dict], ledger_to_gy: dict[str, str], year_now: int) -> list[dict]:
    out = []
    seen: set[tuple] = set()
    for row in rows:
        gy = ledger_to_gy.get(row.get("ledger_id") or "")
        match = re.search(r"cv_section=(\w+)", row.get("reviewer_note") or "")
        origin = row.get("origin") or ""
        if not gy or not match or match.group(1) not in BACKGROUND_SECTIONS or not origin.startswith("cv:"):
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
        key = (gy, match.group(1), year, re.sub(r"[\W_]+", "", title.lower()))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "id": _row_activity_id(row),
                "artist_id": gy,
                "section": match.group(1),
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
        declared = int(row.get("roster_count") or 0)
        roster = max(declared, len(matched)) if matched or declared else 0
        included = len(matched)
        published = sum(1 for lid in matched if lid in ledger_to_gy)
        row["roster_count"] = roster
        row["included_count"] = included
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
                "coverage_pct": _coverage_pct(included, roster),
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
    edition = edition_of(mem_code)
    return bool(edition) and edition[0] == frame_code


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


def _citations(config: Config, artists: list[dict], version: str, today: str, clock_year: int) -> dict:
    title = config.dataset_title or config.name
    author = config.citation_author
    origin = config.site_url.rstrip("/")
    dataset_url = f"{origin}/data"
    dataset = {
        "title": title,
        "version": version,
        "url": dataset_url,
        "year": clock_year,
        "accessed": today,
        **citation_texts(
            author=author,
            title=title,
            record_id=None,
            version=version,
            url=dataset_url,
            year=clock_year,
            accessed=today,
        ),
    }
    people = []
    for artist in artists:
        updated = str(artist.get("updated_at") or "")
        year = int(updated[:4]) if re.match(r"\d{4}", updated) else clock_year
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
                    url=url,
                    year=year,
                    accessed=today,
                ),
            }
        )
    return {"dataset": dataset, "artists": people}
