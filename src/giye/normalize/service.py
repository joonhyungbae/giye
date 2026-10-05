# SPDX-License-Identifier: AGPL-3.0-only
"""Run normalisation: ledger → ``data/processed`` (the configured data directory).

The ledger is not modified. Derived rows name the rule that produced them.
A value already on the artist row wins over P5 (country, region, active_since,
medium). Outputs:

  activities.csv          title/venue normalisation, place, venue id, P1 flags, P4 link
  artist_attributes.csv   one row per derived value, with rule and evidence
  venues.csv              institution and funder entities
  venue_audit.md          every merge, with its rule
  manifest.json           hashes of the inputs, rule version, counts
  report.md               what each rule filled and flagged
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from giye.config import Config
from giye.ledger.io import read_csv, write_csv
from giye.normalize.language import language_for, packaged_dir
from giye.normalize.rules import (
    HEAD_CHARS,
    active_since,
    based_in,
    birth_year,
    event_links,
    lang_of,
    match_key,
    medium_tags,
    norm_text,
    venue_place,
    year_flags,
)
from giye.normalize.venues import NAME_RULES, build
from giye.resolve.evidence import event_pattern, pattern_table
from giye.resolve.teams import team_like

RULES_VERSION = "2026-09-25.5"
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
    "event_link",
    "flags",
]
ATTR_FIELDS = ["ledger_id", "field", "value", "rule", "evidence", "evidence_url"]


@dataclass
class NormalizeResult:
    processed: Path
    report: str
    activities: int
    attributes: int
    # Rule id → how many institution spellings that rule joined (V5a–V5f, V7–V9).
    venue_merges: dict[str, int] = field(default_factory=dict)


def parse_name_rules(raw: str | None) -> frozenset[str] | None:
    """``None`` means V7, V8 and V9. ``none`` / ``off`` / ``base`` means none of them."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.lower() in {"none", "off", "base"}:
        return frozenset()
    chosen = frozenset(part.strip().upper() for part in text.split(",") if part.strip())
    unknown = chosen - NAME_RULES
    if unknown:
        raise ValueError(f"unknown venue name rules: {sorted(unknown)}")
    return chosen


def resolve_stored(config: Config, stored: str) -> Path:
    """Map a ``data/...`` snapshot path onto the configured data directory.

    Production stores ``data/raw/cv/...`` and resolves it against the data root.
    An absolute path is kept. Anything else is relative to the data directory.
    """
    path = Path(stored)
    if path.is_absolute():
        return path
    parts = path.parts
    if parts and parts[0] == "data":
        return config.data.joinpath(*parts[1:])
    return config.data / path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cv_texts(config: Config, sources: list[dict]) -> list[tuple[str, str]]:
    """(text, url) of the latest snapshot of each active CV source.

    The stored path has no ``.txt`` suffix; production appends it.
    """
    found: list[tuple[str, str]] = []
    for source in sources:
        if source.get("active", "true") != "true" or not source.get("snapshot_path"):
            continue
        path = resolve_stored(config, f"{source['snapshot_path']}.txt")
        if path.is_file():
            found.append((path.read_text(encoding="utf-8", errors="replace"), source.get("url") or ""))
    return found


def _table(config: Config, filename: str) -> list[dict[str, str]]:
    return read_csv(config.ledger / filename)


def normalize(config: Config, *, venue_name_rules: str | None = None) -> NormalizeResult:
    """Write the processed layer for ``config``. Does not change the ledger.

    ``venue_name_rules`` overrides ``[normalize] venue_name_rules`` when it is
    not ``None``. An empty config value applies V7, V8 and V9.
    """
    raw_rules = venue_name_rules if venue_name_rules is not None else config.venue_name_rules
    name_rules = parse_name_rules(raw_rules if raw_rules else None)
    language = language_for(config)
    out = config.processed
    out.mkdir(parents=True, exist_ok=True)

    artists = _table(config, "artists.csv")
    activities = _table(config, "activities.csv")
    by_artist: dict[str, list[dict]] = defaultdict(list)
    for row in activities:
        by_artist[row["ledger_id"]].append(row)
    sources: dict[str, list[dict]] = defaultdict(list)
    for source in read_csv(config.ledger / "cv_sources.csv"):
        sources[source.get("ledger_id") or ""].append(source)
    frames_of: dict[str, list[str]] = defaultdict(list)
    for membership in read_csv(config.ledger / "frame_membership.csv"):
        frames_of[membership.get("ledger_id") or ""].append(membership.get("frame_code") or "")

    patterns = pattern_table(config.field_config.event_patterns, config.event_patterns)
    tags = config.field_config.resolved()

    def pattern_for(code: str) -> str | None:
        return event_pattern(code, patterns)

    flags: dict[str, list[str]] = {}
    links: dict[str, str] = {}
    for ledger_id, rows in by_artist.items():
        flags.update(year_flags(rows))  # P1
        links.update(event_links(rows, frames_of.get(ledger_id, []), pattern_for))  # P4
    venue_places: dict[str, tuple[str, str]] = {}
    for row in activities:
        venue = norm_text(row.get("venue"))
        if venue not in venue_places:
            venue_places[venue] = venue_place(venue, language.gazetteer)  # V1
    venue_result = build(activities, out, name_rules=name_rules, lang=language)
    activity_out = []
    for row in activities:
        venue = norm_text(row.get("venue"))
        country, region = venue_places.get(venue, ("", ""))
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
                **venue_result.annotations.get(row.get("activity_id", ""), {"venue_id": "", "funder_id": "", "venue_kind": ""}),
                "event_link": links.get(row.get("activity_id", ""), ""),
                "flags": "|".join(flags.get(row.get("activity_id", ""), [])),
            }
        )

    attributes: list[dict] = []
    filled: Counter = Counter()

    def put(ledger_id: str, field_name: str, value, rule: str, evidence: str = "", url: str = "") -> None:
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
                url = next(
                    url for text, url in texts if phrase[:30] in norm_text(text[:HEAD_CHARS])
                )
                countries = "|".join(dict.fromkeys(country for country, _region in places))
                put(ledger_id, "country", countries, "L1 base phrase in own CV", phrase, url)
                regions = [region for country, region in places if country == "KR" and region]
                if regions:
                    put(ledger_id, "region", "|".join(dict.fromkeys(regions)), "L1 base phrase in own CV", phrase, url)
        if not artist.get("active_since"):
            got = active_since(rows, flags)
            if got:
                put(ledger_id, "active_since", got[0], "A1 earliest public practice row", got[1])
        if not (artist.get("field") or artist.get("category")):
            for tag, ids in sorted(
                medium_tags(
                    rows,
                    words=tags.medium_words,
                    screening_tag=tags.screening_tag,
                    min_rows=tags.medium_min_rows,
                ).items()
            ):
                put(ledger_id, "medium", tag, f"M1 ≥{tags.medium_min_rows} rows name it", "|".join(ids[:20]))

    write_csv(path=out / "activities.csv", fields=ACT_FIELDS, rows=activity_out)
    write_csv(path=out / "artist_attributes.csv", fields=ATTR_FIELDS, rows=attributes)

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

    flag_counts = Counter(flag for group in flags.values() for flag in group)
    manifest = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rules_version": RULES_VERSION,
        "venue_name_rules": sorted(NAME_RULES if name_rules is None else name_rules),
        "language": language.name,
        "inputs_sha256": inputs,
        "counts": {
            "artists": len(artists),
            "activities": len(activities),
            "flags": dict(flag_counts),
            "attributes": dict(filled),
            "venue_country": sum(1 for row in activity_out if row["venue_country"]),
            "venue_entities": venue_result.stats["entities"],
            "venue_entities_shared_2plus": venue_result.stats["shared_entities"],
            "venue_kind": venue_result.stats["venue_kind"],
            "venue_alias_merges": venue_result.stats["alias_merges"],
            "event_link_roster": sum(
                1 for row in activity_out if row["event_link"] and not str(row["origin"]).startswith("cv:")
            ),
            "event_link_cv": sum(1 for row in activity_out if row["event_link"] and str(row["origin"]).startswith("cv:")),
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    medium_artists = len({row["ledger_id"] for row in attributes if row["field"] == "medium"})
    applied = ", ".join(manifest["venue_name_rules"]) or "(none)"
    lines = [
        f"# Preprocess report ({manifest['generated_at']}, rules {RULES_VERSION})",
        "",
        f"Artists {len(artists)} · activities {len(activities)}",
        f"Venue name rules: {applied}",
        "",
        "## P1 checks (flag only; nothing is deleted)",
        "",
        *[f"- {key}: {value}" for key, value in flag_counts.most_common()],
        "",
        "## P3 places and institutions · P4 same event",
        "",
        (
            f"- Activities whose venue yielded a country: {manifest['counts']['venue_country']} / {len(activities)}"
            " (V1, G1–G6, every place-name fragment split on a delimiter)"
        ),
        f"- Institution entities: {venue_result.stats['entities']} (V2–V6, then V7–V9)",
        f"- Institution entities shared by two or more distinct artists: {venue_result.stats['shared_entities']}",
        "- venue_kind: "
        + " · ".join(
            f"{kind} {venue_result.stats['venue_kind'].get(kind, 0)}"
            for kind in ("institution", "funder", "online", "place_only", "empty")
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
    ]
    report = "\n".join(lines) + "\n"
    (out / "report.md").write_text(report, encoding="utf-8")
    venue_merges = dict(sorted(Counter(rule for rule, _left, _right in venue_result.merges).items()))
    return NormalizeResult(
        processed=out,
        report=report,
        activities=len(activities),
        attributes=len(attributes),
        venue_merges=venue_merges,
    )
