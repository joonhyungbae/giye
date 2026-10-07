# SPDX-License-Identifier: AGPL-3.0-only
"""Run normalisation: ledger → ``data/processed`` (the configured data directory).

The ledger is not modified. Derived rows name the rule that produced them.
A value already on the artist row wins over P5 (country, region, active_since,
medium). Outputs:

  activities.csv          title/venue normalisation, place, venue id, P1 flags, P4 link,
                          and K1 activity_kind / activity_channel.
                          venue_rule names the rules that put the row at its entity
                          (V4, V7, V5a…V9); rules names every rule that filled a value
                          of the row (P2, V1, P3, P4, Y0–Y2, K1 and the kind reason).
  artist_attributes.csv   one row per derived value, with rule and evidence.
                          P6 record_depth (1–4) is one row per published person.
                          It has no ledger cell and is not copied into the site snapshot.
  venues.csv              institution and funder entities, with the rules that formed each
  venue_merges.csv        every merge: rule, entity, the two keys it joined
  venue_audit.md          every merge, with its rule
  manifest.json           hashes of the inputs, rule version, counts
  report.md               what each rule filled and flagged
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from giye.config import Config
from giye.extract.paths import verified_cv_text
from giye.field import Field
from giye.ledger.io import read_csv, write_csv, write_text_atomic
from giye.ledger.ledger import without_hidden
from giye.normalize.kinds import KINDS, classify
from giye.normalize.language import LanguageModule, language_for, packaged_dir
from giye.normalize.rules import (
    FLAG_RULES,
    HEAD_CHARS,
    active_since,
    based_in,
    birth_year,
    event_links,
    lang_of,
    load_snippet_classes,
    match_key,
    medium_tags,
    norm_text,
    published_ids,
    record_depth,
    venue_place,
    year_flags,
)
from giye.normalize.venues import NAME_RULES, BuildResult, build
from giye.resolve.evidence import event_pattern, pattern_table
from giye.resolve.teams import team_like

# 2026-10-08 adds K1 activity kind. P1–P6 are unchanged.
RULES_VERSION = "2026-10-08"
ACT_FIELDS = [
    "activity_id",
    "ledger_id",
    "year",
    "activity_type",
    "publishable",
    "origin",
    "title_norm",
    "venue_norm",
    "title_key",
    "lang",
    "venue_country",
    "venue_region",
    "venue_id",
    "funder_id",
    "venue_kind",
    "venue_rule",
    "event_link",
    "flags",
    "activity_kind",
    "activity_channel",
    "rules",
]
ATTR_FIELDS = ["ledger_id", "field", "value", "rule", "evidence", "evidence_url"]


@dataclass
class NormalizeResult:
    """What one normalisation run wrote: the processed directory, the report, and row counts.

    ``venue_merges`` maps a rule id (V5a–V5f, V7–V9) to how many institution
    spellings that rule joined.
    """

    processed: Path
    report: str
    activities: int
    attributes: int
    venue_merges: dict[str, int] = field(default_factory=dict)


def parse_name_rules(raw: str | None) -> frozenset[str] | None:
    """``None`` means every name rule. ``none`` / ``off`` / ``base`` means none of them.

    Ids are matched without case, then stored as ``NAME_RULES`` spells them
    (``V4n``, not ``V4N``).
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.lower() in {"none", "off", "base"}:
        return frozenset()
    canonical = {rule.upper(): rule for rule in NAME_RULES}
    parts = [part.strip() for part in text.split(",") if part.strip()]
    unknown = sorted(part for part in parts if part.upper() not in canonical)
    if unknown:
        raise ValueError(f"unknown venue name rules: {unknown}")
    return frozenset(canonical[part.upper()] for part in parts)


def resolve_stored(config: Config, stored: str) -> Path:
    """Map a stored snapshot path onto the configured data directory.

    Ledger paths are written as ``data/...`` from the archive root. The
    configured data directory is that ``data/``, so a leading ``data`` segment
    is dropped. An absolute path is kept. Anything else is relative to the
    data directory.
    """
    path = Path(stored)
    if path.is_absolute():
        return path
    parts = path.parts
    if parts and parts[0] == "data":
        return config.data.joinpath(*parts[1:])
    return config.data / path


def _sha256(path: Path) -> str:
    """Hex digest of a file's bytes, for the manifest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cv_texts(config: Config, sources: list[dict]) -> list[tuple[str, str]]:
    """(text, url) of the latest snapshot of each active CV source, in source id order.

    The ledger stores the snapshot stem without ``.txt``; the extracted text
    is that stem plus ``.txt``. Source id order: L1 takes the first CV that names
    a base and B1/L1 cite the first matching CV, and that must not follow the
    order of the cv_sources rows.
    """
    found: list[tuple[str, str]] = []
    for source in sorted(sources, key=lambda row: (row.get("source_id") or "", row.get("url") or "")):
        if source.get("active", "true") != "true" or not source.get("snapshot_path"):
            continue
        text = verified_cv_text(config, source, errors="replace")
        if text is not None:
            found.append((text, source.get("url") or ""))
    return found


def _table(config: Config, filename: str) -> list[dict[str, str]]:
    """One ledger CSV as row dicts. A missing file is an empty table."""
    return read_csv(config.ledger / filename)


def _frame_rows(config: Config) -> list[dict[str, str]]:
    """Registry frames as the dicts P6's published-person test reads.

    A missing file is an empty registry: nothing can borrow a frame URL.
    """
    if not config.frames.is_file():
        return []
    from giye.collect.frames import load_frames

    return [
        {
            "code": frame.code,
            "source_url": frame.source_url,
            "years_covered": frame.years_covered,
            "decision": frame.eligibility.decision,
        }
        for frame in load_frames(config.frames).frames
    ]


def _edition_of(
    config: Config, frames: list[dict[str, str]]
) -> Callable[[str], tuple[str, str | None] | None]:
    """Membership code → (registry frame, edition year), including field-file aliases."""
    from giye.publish.snapshot import resolve_frame_edition

    codes = [row["code"] for row in frames if row.get("code")]
    years = {row["code"]: row.get("years_covered") or "" for row in frames if row.get("code")}

    def edition(mem_code: str) -> tuple[str, str | None] | None:
        return resolve_frame_edition(mem_code, codes, years, field=config.field_config)

    return edition


@dataclass
class _Inputs:
    """Ledger tables read once, in file order, at the start of a run."""

    artists: list[dict[str, str]]
    activities: list[dict[str, str]]
    by_artist: dict[str, list[dict[str, str]]]
    sources: dict[str, list[dict[str, str]]]
    frames_of: dict[str, list[str]]
    membership_rows: list[dict[str, str]]
    link_of: dict[str, list[dict[str, str]]]
    scope_rows: list[dict[str, str]]
    frame_rows: list[dict[str, str]]


def _read_inputs(config: Config) -> _Inputs:
    """Ledger tables a normalisation run reads, in that order.

    A person hidden by request is left out of every table here, so nothing of
    theirs reaches ``data/processed/`` (a hide request stops processing).
    """
    everyone = _table(config, "artists.csv")
    artists = without_hidden(everyone, everyone)
    activities = without_hidden(_table(config, "activities.csv"), everyone)
    by_artist: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in activities:
        by_artist[row["ledger_id"]].append(row)
    sources: dict[str, list[dict[str, str]]] = defaultdict(list)
    for source in read_csv(config.ledger / "cv_sources.csv"):
        sources[source.get("ledger_id") or ""].append(source)
    frames_of: dict[str, list[str]] = defaultdict(list)
    membership_rows = without_hidden(read_csv(config.ledger / "frame_membership.csv"), everyone)
    for membership in membership_rows:
        frames_of[membership.get("ledger_id") or ""].append(membership.get("frame_code") or "")
    link_of: dict[str, list[dict[str, str]]] = defaultdict(list)
    for link in read_csv(config.ledger / "links.csv"):
        link_of[link.get("ledger_id") or ""].append(link)
    scope_rows = read_csv(config.ledger / "scope.csv")
    return _Inputs(
        artists=artists,
        activities=activities,
        by_artist=by_artist,
        sources=sources,
        frames_of=frames_of,
        membership_rows=membership_rows,
        link_of=link_of,
        scope_rows=scope_rows,
        frame_rows=_frame_rows(config),
    )


def _gy_to_ledger(config: Config, artists: list[dict[str, str]]) -> dict[str, str]:
    """gy_id → ledger_id, including a retired id redirected to the record that absorbed it.

    Evidence filed under a retired id belongs to the surviving record, the same
    way the site's redirects do.
    """
    mapped = {
        artist["gy_id"]: artist["ledger_id"] for artist in artists if artist.get("gy_id") and artist.get("ledger_id")
    }
    for row in read_csv(config.ledger / "gy_retired.csv"):
        retired, survivor = row.get("gy_id") or "", row.get("merged_into_ledger_id") or ""
        if retired and survivor and retired not in mapped:
            mapped[retired] = survivor
    return mapped


def _flags_and_links(
    by_artist: dict[str, list[dict[str, str]]],
    frames_of: dict[str, list[str]],
    patterns: dict[str, str],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """P1 year flags and P4 edition links, one pass per artist."""

    def pattern_for(code: str) -> str | None:
        return event_pattern(code, patterns)

    flags: dict[str, list[str]] = {}
    links: dict[str, str] = {}
    for ledger_id, rows in by_artist.items():
        flags.update(year_flags(rows))  # P1
        links.update(event_links(rows, frames_of.get(ledger_id, []), pattern_for))  # P4
    return flags, links


def _places_by_venue(
    activities: list[dict[str, str]], language: LanguageModule
) -> dict[str, tuple[str, str]]:
    """V1: normalised venue string → (country, Korean region), once per distinct string."""
    venue_places: dict[str, tuple[str, str]] = {}
    for row in activities:
        venue = norm_text(row.get("venue"))
        if venue not in venue_places:
            venue_places[venue] = venue_place(venue, language.gazetteer)  # V1
    return venue_places


def _row_rules(
    country: str, annotation: dict[str, str], link: str, row_flags: list[str], kind_reason: str
) -> str:
    """N-8: the rule ids behind the derived values of one processed activity row.

    K1 is always present. ``kind_reason`` is the clause that chose the kind
    (``private_section``, ``award_grant``, ``roster_token_<word>``, ``roster_session``).
    """
    found = ["P2"]
    if country:
        found.append("V1")
    if annotation.get("venue_kind"):
        found.append("P3")
    if link:
        found.append("P4")
    found.extend(FLAG_RULES.get(flag, flag) for flag in row_flags)
    found.append("K1")
    if kind_reason and kind_reason not in found:
        found.append(kind_reason)
    return "|".join(found)


def _activity_rows(
    activities: list[dict[str, str]],
    venue_places: dict[str, tuple[str, str]],
    venue_result: BuildResult,
    links: dict[str, str],
    flags: dict[str, list[str]],
) -> list[dict[str, str]]:
    """One processed activity row per ledger row: P2 text, V1 place, entity ids, P4, P1, K1."""
    activity_out: list[dict[str, str]] = []
    assigned = classify(activities)
    for row, kind in zip(activities, assigned, strict=True):
        venue = norm_text(row.get("venue"))
        country, region = venue_places.get(venue, ("", ""))
        activity_id = row.get("activity_id", "")
        annotation = venue_result.annotations.get(
            activity_id, {"venue_id": "", "funder_id": "", "venue_kind": "", "venue_rule": ""}
        )
        link = links.get(activity_id, "")
        row_flags = flags.get(activity_id, [])
        activity_out.append(
            {
                "activity_id": row.get("activity_id", ""),
                "ledger_id": row.get("ledger_id", ""),
                "year": row.get("year", ""),
                "activity_type": row.get("activity_type", ""),
                "publishable": row.get("publishable", ""),
                "origin": row.get("origin", ""),
                "title_norm": norm_text(row.get("title")),
                "venue_norm": venue,
                "title_key": match_key(row.get("title")),
                "lang": lang_of(row.get("title")),
                "venue_country": country,
                "venue_region": region,
                **annotation,
                "event_link": link,
                "flags": "|".join(row_flags),
                "activity_kind": kind.kind,
                "activity_channel": kind.channel,
                "rules": _row_rules(country, annotation, link, row_flags, kind.reason),
            }
        )
    return activity_out


def _derive_attributes(
    config: Config,
    artists: list[dict[str, str]],
    by_artist: dict[str, list[dict[str, str]]],
    sources: dict[str, list[dict[str, str]]],
    language: LanguageModule,
    flags: dict[str, list[str]],
    link_of: dict[str, list[dict[str, str]]],
    frames_of: dict[str, list[str]],
    published: set[str],
    snippets: dict[str, list[str]],
    tags: Field,
) -> tuple[list[dict], Counter[str], Counter[str]]:
    """P5 and P6 rows. A value already on the artist row is left as it is (P5)."""
    attributes: list[dict] = []
    filled: Counter[str] = Counter()
    depth_counts: Counter[str] = Counter()

    def put(ledger_id: str, field_name: str, value: str | int, rule: str, evidence: str = "", url: str = "") -> None:
        """Append one derived row and count it. Does not edit the ledger."""
        attributes.append(
            {
                "ledger_id": ledger_id,
                "field": field_name,
                "value": value,
                "rule": rule,
                "evidence": evidence,
                "evidence_url": url,
            }
        )
        filled[field_name] += 1

    for artist in artists:
        ledger_id = artist["ledger_id"]
        rows = by_artist.get(ledger_id, [])
        # A team CV lists members' births, so it is not that row's birth year.
        team = team_like(artist, words=tags.compiled_team_words(), language=language)
        texts = [] if team else _cv_texts(config, sources.get(ledger_id, []))
        # B1 has no ledger column. It is still only a derived row, never a ledger edit.
        born = birth_year([text for text, _url in texts])
        if born:
            url = next(url for text, url in texts if str(born) in text[:HEAD_CHARS])
            put(ledger_id, "birth_year", born, "B1 birth phrase in own CV", "", url)
        # P5: the ledger value wins.
        if not (artist.get("country") or artist.get("region")):
            places, phrase = based_in([text for text, _url in texts], language.gazetteer)
            if places:
                url = next(url for text, url in texts if phrase[:30] in norm_text(text[:HEAD_CHARS]))
                countries = "|".join(dict.fromkeys(country for country, _region in places))
                put(ledger_id, "country", countries, "L1 base phrase in own CV", phrase, url)
                regions = [region for country, region in places if country == "KR" and region]
                if regions:
                    put(ledger_id, "region", "|".join(dict.fromkeys(regions)), "L1 base phrase in own CV", phrase, url)
        if not artist.get("active_since"):
            got = active_since(rows, flags)
            if got:
                put(ledger_id, "active_since", got[0], "A1 earliest public practice row", got[1])
        medium_values: list[str] = []
        # M1 is written only when the ledger has no field and no category. P6 reads those
        # written rows, not a second pass over titles, so a ledger medium is not practice evidence.
        if not (artist.get("field") or artist.get("category")):
            for tag, ids in sorted(
                medium_tags(
                    rows,
                    words=tags.medium_words,
                    screening_tag=tags.screening_tag,
                    min_rows=tags.medium_min_rows,
                ).items()
            ):
                put(ledger_id, "medium", tag, f"M1 ≥{tags.medium_min_rows} rows name it", "|".join(sorted(ids)[:20]))
                medium_values.append(tag)
        if ledger_id in published:
            value, evidence = record_depth(
                activities=rows,
                links=link_of.get(ledger_id, []),
                cv_sources=sources.get(ledger_id, []),
                medium_values=medium_values,
                snippet_classes=snippets.get(ledger_id, []),
                memberships=frames_of.get(ledger_id, []),
            )
            put(ledger_id, "record_depth", value, "P6 record depth", evidence)
            depth_counts[value] += 1
    return attributes, filled, depth_counts


def _input_digests(
    config: Config, language: LanguageModule, snippet_path: Path
) -> tuple[dict[str, str], str]:
    """SHA-256 of ledger CSVs, gazetteer files, the glossary, and the snippets file.

    The second value is ``present`` or ``absent``. Absent means level 2 is the
    M1 rows of this run only.
    """
    inputs: dict[str, str] = {}
    ledger = config.ledger
    if ledger.is_dir():
        for path in sorted(ledger.glob("*.csv")):
            inputs[path.name] = _sha256(path)
    for path in language.gazetteer.sources:
        if path.is_file():
            inputs[path.name] = _sha256(path)
    glossary_file = config.normalize_glossary or (packaged_dir() / "glossary.yaml")
    if glossary_file.is_file():
        inputs[glossary_file.name] = _sha256(glossary_file)
    snippet_state = "absent"
    if snippet_path.is_file():
        inputs["work/tendency/snippets.jsonl"] = _sha256(snippet_path)
        snippet_state = "present"
    return inputs, snippet_state


def _manifest(
    *,
    name_rules: frozenset[str] | None,
    language_name: str,
    inputs: dict[str, str],
    artists: int,
    activities: int,
    flag_counts: Counter[str],
    filled: Counter[str],
    depth_by_level: dict[str, int],
    published: int,
    snippet_state: str,
    activity_out: list[dict[str, str]],
    venue_result: BuildResult,
) -> dict:
    """Input hashes, rule version, and counts. ``generated_at`` is UTC, set here."""
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rules_version": RULES_VERSION,
        "venue_name_rules": sorted(NAME_RULES if name_rules is None else name_rules),
        "language": language_name,
        "inputs_sha256": inputs,
        "counts": {
            "artists": artists,
            "activities": activities,
            "flags": dict(flag_counts),
            "attributes": dict(filled),
            "record_depth": depth_by_level,
            "record_depth_published": published,
            "record_depth_snippets": snippet_state,
            "venue_country": sum(1 for row in activity_out if row["venue_country"]),
            "venue_entities": venue_result.stats["entities"],
            "venue_entities_shared_2plus": venue_result.stats["shared_entities"],
            "venue_kind": venue_result.stats["venue_kind"],
            "venue_alias_merges": venue_result.stats["alias_merges"],
            "event_link_roster": sum(
                1 for row in activity_out if row["event_link"] and not str(row["origin"]).startswith("cv:")
            ),
            "event_link_cv": sum(
                1 for row in activity_out if row["event_link"] and str(row["origin"]).startswith("cv:")
            ),
        },
    }


def _report_text(
    manifest: dict,
    artists: int,
    activities: int,
    flag_counts: Counter[str],
    venue_result: BuildResult,
    filled: Counter[str],
    tags: Field,
    depth_by_level: dict[str, int],
    published: int,
    snippet_state: str,
    medium_artists: int,
    kind_counts: Counter[str],
) -> str:
    """``report.md``: what each rule filled and flagged. Nothing here edits the ledger."""
    applied = ", ".join(manifest["venue_name_rules"]) or "(none)"
    lines = [
        f"# Preprocess report ({manifest['generated_at']}, rules {RULES_VERSION})",
        "",
        f"Artists {artists} · activities {activities}",
        f"Venue name rules: {applied}",
        "",
        "## P1 checks (flag only; nothing is deleted)",
        "",
        *[f"- {key}: {value}" for key, value in flag_counts.most_common()],
        "",
        "## P3 places and institutions · P4 same event",
        "",
        (
            f"- Activities whose venue yielded a country: {manifest['counts']['venue_country']} / {activities}"
            " (V1, G1–G6, every place-name fragment split on a delimiter)"
        ),
        f"- Institution entities: {venue_result.stats['entities']} (V2–V9, then V4n, V7f, V9u)",
        f"- Institution entities shared by two or more distinct artists: {venue_result.stats['shared_entities']}",
        "- venue_kind: "
        + " · ".join(
            f"{kind} {venue_result.stats['venue_kind'].get(kind, 0)}"
            for kind in ("institution", "funder", "online", "place_only", "title_only", "unclassified", "empty")
        ),
        (
            f"- Alias merges: {venue_result.stats['alias_merges']}"
            f" (qualified alias pairs {venue_result.stats['qualified_alias_pairs']},"
            f" merged entities {venue_result.stats['alias_entities']},"
            f" single-artist V5f {venue_result.stats['single_artist_alias_merges']})"
        ),
        f"- Components not grouped by V5e: {venue_result.stats['blocked_alias_components']}",
        "- Name-rule merges: "
        + (
            " · ".join(f"{rule} {count}" for rule, count in venue_result.stats["name_rule_merges"].items())
            or "none"
        ),
        f"- Roster rows (the edition itself): {manifest['counts']['event_link_roster']}",
        (
            f"- CV rows pointing at the same edition: {manifest['counts']['event_link_cv']}"
            " (P4, event word + same year)"
        ),
        "",
        "## P5 derivations (a value already on the ledger wins)",
        "",
        "| Field | Artists filled | Rule |",
        "|---|---|---|",
        (
            f"| birth_year | {filled['birth_year']} | B1 birth wording at the head of the artist's own CV; "
            "left empty when two or more years disagree |"
        ),
        f"| country | {filled['country']} | L1 based-in wording on the artist's own CV → place-name gazetteer |",
        f"| region | {filled['region']} | L1, Korean region only |",
        f"| active_since | {filled['active_since']} | A1 earliest year among public activities (flagged years excluded) |",
        f"| medium | {medium_artists} | M1 at least {tags.medium_min_rows} rows whose title, role, or strand name the same medium |",
        "",
        "## P6 record depth (published people; the highest level)",
        "",
        (
            "A person is published by the same function as the site build (`published_ids`): "
            "not scope=out, on an admitted roster, with a source URL and a "
            "collection date, status empty / PUBLISHED / STAGED. "
            f"Published {published} of {artists}. One `record_depth` row each. "
            "The value is not copied into the site snapshot."
        ),
        "",
        "| Level | People | What justifies it |",
        "|---|---|---|",
        (
            f"| 1 | {depth_by_level['1']} | Roster membership only: no M1 medium row, no T1 practice snippet, "
            "no non-social link, no active CV source, no standing CV extraction |"
        ),
        (
            f"| 2 | {depth_by_level['2']} | Practice evidence: an M1 medium row written above, "
            "or a T1 snippet classed has_description or has_medium_word |"
        ),
        f"| 3 | {depth_by_level['3']} | A non-social link or an active cv_sources row, and no standing CV extraction |",
        (
            f"| 4 | {depth_by_level['4']} | An activities.csv row with origin cv: "
            "whose note does not contain superseded_by_cv |"
        ),
        "",
        (
            f"Snippets file `work/tendency/snippets.jsonl`: {snippet_state}. "
            "When it is absent, level 2 is the M1 rows only. A later run of this script reads the file if it is there."
        ),
        "",
        "## K1 activity kind",
        "",
        (
            "activity_kind refines activity_type by cv_section. A private section wins, "
            "except two bare-prize titles. Roster words are discovered from this run's "
            "roster `other` rows, not taken from a fixed list. The ledger is not edited. "
            "Counts below are CV rows only (`origin` starts with `cv:`)."
        ),
        "",
        *[f"- {kind}: {kind_counts[kind]}" for kind in KINDS if kind_counts[kind]],
    ]
    return "\n".join(lines) + "\n"


def normalize(config: Config, *, venue_name_rules: str | None = None) -> NormalizeResult:
    """Write the processed layer for ``config``. Does not change the ledger.

    ``venue_name_rules`` overrides ``[normalize] venue_name_rules`` when it is
    not ``None``. An empty config value applies every name rule (V7, V8, V9, V4n, V7f, V9u).
    """
    raw_rules = venue_name_rules if venue_name_rules is not None else config.venue_name_rules
    name_rules = parse_name_rules(raw_rules if raw_rules else None)
    language = language_for(config)
    out = config.processed
    out.mkdir(parents=True, exist_ok=True)

    loaded = _read_inputs(config)
    published = published_ids(
        loaded.artists,
        loaded.membership_rows,
        loaded.scope_rows,
        loaded.frame_rows,
        _edition_of(config, loaded.frame_rows),
        {row["code"]: row.get("decision") or "" for row in loaded.frame_rows if row.get("code")},
    )
    # T1 writes this beside the ledger. Absent → level 2 is the M1 rows of this run only.
    snippet_path = config.work / "tendency" / "snippets.jsonl"
    snippets = load_snippet_classes(snippet_path, _gy_to_ledger(config, loaded.artists))

    patterns = pattern_table(config.field_config.event_patterns, config.event_patterns)
    tags = config.field_config.resolved()
    flags, links = _flags_and_links(loaded.by_artist, loaded.frames_of, patterns)
    venue_places = _places_by_venue(loaded.activities, language)
    venue_result = build(loaded.activities, out, name_rules=name_rules, lang=language)
    activity_out = _activity_rows(loaded.activities, venue_places, venue_result, links, flags)
    attributes, filled, depth_counts = _derive_attributes(
        config,
        loaded.artists,
        loaded.by_artist,
        loaded.sources,
        language,
        flags,
        loaded.link_of,
        loaded.frames_of,
        published,
        snippets,
        tags,
    )
    if sum(depth_counts.values()) != len(published):
        raise ValueError(
            f"P6 wrote {sum(depth_counts.values())} record_depth rows for {len(published)} published people"
        )

    write_csv(path=out / "activities.csv", fields=ACT_FIELDS, rows=activity_out)
    write_csv(path=out / "artist_attributes.csv", fields=ATTR_FIELDS, rows=attributes)

    inputs, snippet_state = _input_digests(config, language, snippet_path)
    flag_counts: Counter[str] = Counter(flag for group in flags.values() for flag in group)
    depth_by_level = {level: depth_counts[level] for level in ("1", "2", "3", "4")}
    manifest = _manifest(
        name_rules=name_rules,
        language_name=language.name,
        inputs=inputs,
        artists=len(loaded.artists),
        activities=len(loaded.activities),
        flag_counts=flag_counts,
        filled=filled,
        depth_by_level=depth_by_level,
        published=len(published),
        snippet_state=snippet_state,
        activity_out=activity_out,
        venue_result=venue_result,
    )
    write_text_atomic(out / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    medium_artists = len({row["ledger_id"] for row in attributes if row["field"] == "medium"})
    kind_counts: Counter[str] = Counter(
        row["activity_kind"] for row in activity_out if str(row.get("origin") or "").startswith("cv:")
    )
    report = _report_text(
        manifest,
        len(loaded.artists),
        len(loaded.activities),
        flag_counts,
        venue_result,
        filled,
        tags,
        depth_by_level,
        len(published),
        snippet_state,
        medium_artists,
        kind_counts,
    )
    (out / "report.md").write_text(report, encoding="utf-8")
    venue_merges = dict(sorted(Counter(rule for rule, _left, _right in venue_result.merges).items()))
    return NormalizeResult(
        processed=out,
        report=report,
        activities=len(loaded.activities),
        attributes=len(attributes),
        venue_merges=venue_merges,
    )
