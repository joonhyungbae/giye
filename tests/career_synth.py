# SPDX-License-Identifier: AGPL-3.0-only
"""A fictitious archive for the career-bundle tests.

What: writes a complete ``giye.toml`` plus ledger and processed tables. People
are ``Person NNNN`` with ledger ids ``CAND-NNNN`` and ``GY-NNNNNN``. Nothing
here is a real person or a real artist URL.

Why: the career builder has to be tested offline, on a field large enough for
several generations to clear ``k`` and ``PCT_MIN``, and on small fields that
are entirely suppressed.

How to run: imported by ``tests/test_career_*.py``. Not a script.

    from tests.career_synth import synthetic_archive
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

from giye.ledger.io import write_csv
from giye.ledger.schemas import ACTIVITIES_FIELDS, ARTISTS_FIELDS, MEMBERSHIP_FIELDS, SCOPE_FIELDS, empty_row
from giye.normalize.kinds import KINDS

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo"

# Roughly the shape of a CV: exhibitions and talks are common, holdings are rare.
_KIND_WEIGHTS = {
    "group_exhibition": 16,
    "solo_exhibition": 8,
    "screening": 6,
    "performance": 5,
    "festival": 4,
    "talk_workshop": 8,
    "publication_press": 5,
    "residency": 4,
    "funding": 4,
    "award": 3,
    "teaching": 3,
    "education": 2,
    "commission": 2,
    "online_release": 2,
    "collection": 1,
    "employment": 2,
    "service": 1,
    "other": 3,
}
_CHANNEL = {
    "education": "background",
    "employment": "background",
    "teaching": "background",
    "service": "hidden",
}
_PROCESSED_FIELDS = [
    "activity_id",
    "ledger_id",
    "year",
    "activity_type",
    "publishable",
    "origin",
    "venue_country",
    "venue_region",
    "venue_id",
    "funder_id",
    "venue_kind",
    "event_link",
    "activity_kind",
    "activity_channel",
]
_ATTR_FIELDS = ["ledger_id", "field", "value", "rule", "evidence", "evidence_url"]
_VENUE_FIELDS = ["venue_id", "name", "aliases", "kind", "country", "kr_region"]


def synthetic_archive(
    tmp_path: Path,
    *,
    n_people: int = 400,
    seed: int = 1,
    territory: str = "KR",
    first_year_min: int = 1995,
    first_year_max: int = 2024,
    cv_rate: float = 0.60,
    a1_rate: float = 0.95,
    force_kind: str | None = None,
    single_first_year: int | None = None,
) -> Path:
    """Write a fake archive under ``tmp_path`` and return its ``giye.toml``.

    Odd ``CAND`` numbers are mostly domestic exhibitions. Even numbers are
    mostly overseas funding. A weights file can therefore move a median.
    ``force_kind`` and ``single_first_year`` build the small disclosure cases
    (one generation, one kind) without a second generator.
    """
    root = Path(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy(_DEMO / "field.toml", root / "field.toml")
    shutil.copy(_DEMO / "frames.yml", root / "frames.yml")
    data = root / "data"
    ledger = data / "ledger"
    processed = data / "processed"
    ledger.mkdir(parents=True)
    processed.mkdir(parents=True)
    (root / "giye.toml").write_text(
        "\n".join(
            [
                "[archive]",
                'name = "Example career archive"',
                f'territory = "{territory}"',
                "",
                "[paths]",
                'data = "data"',
                'frames = "frames.yml"',
                'field = "field.toml"',
                "",
                "[publish]",
                'site_url = "https://example.org"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    rng = random.Random(seed)
    kind_names = list(KINDS)
    kind_weights = [_KIND_WEIGHTS[name] for name in kind_names]
    artists = []
    memberships = []
    ledger_activities = []
    processed_rows = []
    attributes = []
    act_n = 0

    for index in range(n_people):
        number = index + 1
        ledger_id = f"CAND-{number:04d}"
        gy_id = f"GY-{number:06d}"
        team = number % 23 == 0
        name = f"Example Collective {number:04d}" if team else f"Person {number:04d}"
        artists.append(
            empty_row(
                ARTISTS_FIELDS,
                ledger_id=ledger_id,
                gy_id=gy_id,
                name_ko=name,
                name_en=name,
                status="PUBLISHED",
                source_url=f"https://example.org/p/{number:04d}",
                collected_at="2026-01-01",
            )
        )
        if number % 2 == 1:
            res_year = 2015 + (index % 8)
            ws_year = min(2024, res_year + 2)
        else:
            ws_year = 2016 + (index % 7)
            res_year = min(2024, ws_year + 2)
        editions = [("EXAMPLE-RESIDENCY", res_year), ("EXAMPLE-WORKSHOP", ws_year)]
        if number % 5 == 0:
            editions.append(("EXAMPLE-RESIDENCY", min(2024, res_year + 3)))
        seen: set[tuple[str, int]] = set()
        for programme, year in editions:
            if (programme, year) in seen:
                continue
            seen.add((programme, year))
            code = f"{programme}-{year}"
            memberships.append(
                empty_row(
                    MEMBERSHIP_FIELDS,
                    ledger_id=ledger_id,
                    frame_code=code,
                    source_url="https://example.org/roster",
                    collected_at="2026-01-01",
                )
            )
            role = "심사위원" if rng.random() < 0.03 else ""
            act_n += 1
            ledger_activities.append(
                empty_row(
                    ACTIVITIES_FIELDS,
                    activity_id=f"act-{act_n:06d}",
                    ledger_id=ledger_id,
                    year=str(year),
                    activity_type="residency",
                    role=role,
                    source_url="https://example.org/roster",
                    collected_at="2026-01-01",
                    publishable="yes",
                    origin=code,
                )
            )

        is_cv = True if force_kind else rng.random() < cv_rate
        if not is_cv:
            attributes.append(_attribute(ledger_id, "record_depth", "1", "P6"))
            continue
        if single_first_year is not None:
            first = single_first_year
        elif number % 17 == 0:
            # An entry year before A1, so C8's clamp has something to count.
            first = 2023
        else:
            first = rng.randint(first_year_min, first_year_max)
        has_a1 = True if force_kind else rng.random() < a1_rate
        attributes.append(_attribute(ledger_id, "record_depth", "2", "P6"))
        if has_a1:
            attributes.append(_attribute(ledger_id, "active_since", str(first), "A1"))
        n_rows = rng.randint(5, 40) if force_kind else rng.randint(5, 60)
        for row_index in range(n_rows):
            kind, country, region, venue_kind = _draw_row(rng, number, kind_names, kind_weights, force_kind)
            venue_id = ""
            event_link = ""
            if venue_kind != "empty":
                venue_id = f"V-{rng.randint(1, 120):04d}"
                if rng.random() < 0.08:
                    event_link = f"EXAMPLE-RESIDENCY-{rng.randint(2015, 2024)}"
            year = first + rng.randint(0, 6)
            if row_index == 0:
                year = first
            act_n += 1
            processed_rows.append(
                {
                    "activity_id": f"cv-{act_n:06d}",
                    "ledger_id": ledger_id,
                    "year": str(min(year, 2026)),
                    "activity_type": kind if kind != "funding" else "award",
                    "publishable": "yes",
                    "origin": "cv:example",
                    "venue_country": country,
                    "venue_region": region,
                    "venue_id": venue_id,
                    "funder_id": venue_id if venue_kind == "funder" else "",
                    "venue_kind": venue_kind,
                    "event_link": event_link,
                    "activity_kind": kind,
                    "activity_channel": _CHANNEL.get(kind, "activity"),
                }
            )

    write_csv(path=ledger / "artists.csv", fields=ARTISTS_FIELDS, rows=artists)
    write_csv(path=ledger / "frame_membership.csv", fields=MEMBERSHIP_FIELDS, rows=memberships)
    write_csv(path=ledger / "scope.csv", fields=SCOPE_FIELDS, rows=[])
    write_csv(path=ledger / "activities.csv", fields=ACTIVITIES_FIELDS, rows=ledger_activities)
    write_csv(path=processed / "activities.csv", fields=_PROCESSED_FIELDS, rows=processed_rows)
    write_csv(path=processed / "artist_attributes.csv", fields=_ATTR_FIELDS, rows=attributes)
    venues = []
    for venue_n in range(1, 121):
        country = "KR" if venue_n % 2 == 0 else "US"
        venues.append(
            {
                "venue_id": f"V-{venue_n:04d}",
                "name": f"Venue {venue_n:04d}",
                "aliases": f"Alias {venue_n:04d}",
                "kind": "institution",
                "country": country,
                "kr_region": "seoul" if country == "KR" else "",
            }
        )
    write_csv(path=processed / "venues.csv", fields=_VENUE_FIELDS, rows=venues)
    (processed / "manifest.json").write_text(
        json.dumps(
            {"rules_version": "test-career-1", "inputs_sha256": "a" * 64},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return root / "giye.toml"


def _attribute(ledger_id: str, field: str, value: str, rule: str) -> dict[str, str]:
    return {
        "ledger_id": ledger_id,
        "field": field,
        "value": value,
        "rule": rule,
        "evidence": "",
        "evidence_url": "",
    }


def _draw_row(
    rng: random.Random,
    number: int,
    kind_names: list[str],
    kind_weights: list[int],
    force_kind: str | None,
) -> tuple[str, str, str, str]:
    """One CV row's kind, country, region and venue kind."""
    if force_kind:
        return force_kind, "KR", "seoul", "institution"
    domestic = number % 2 == 1
    if domestic and rng.random() < 0.75:
        kind = "group_exhibition"
    elif not domestic and rng.random() < 0.45:
        kind = "funding"
    else:
        kind = rng.choices(kind_names, weights=kind_weights, k=1)[0]
    if kind == "funding":
        venue_kind = "funder"
    elif rng.random() < 0.10:
        venue_kind = "empty"
    else:
        venue_kind = "institution"
    roll = rng.random()
    if domestic and roll < 0.90:
        country, region = "KR", rng.choices(["seoul", "busan", ""], weights=[80, 15, 5], k=1)[0]
    elif not domestic and roll < 0.80:
        country, region = rng.choice(["US", "DE", "GB", "JP"]), ""
    elif roll < 0.85:
        country, region = "", ""
    else:
        country = "KR" if domestic else rng.choice(["US", "DE", "GB", "JP"])
        region = "seoul" if country == "KR" else ""
    if venue_kind == "empty":
        country, region = "", ""
    return kind, country, region, venue_kind
