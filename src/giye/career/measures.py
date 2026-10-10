# SPDX-License-Identifier: AGPL-3.0-only
"""Per-person career measures at one age (C3–C9).

What: turns one person's ``cv:`` rows into kind shares, family shares, country
and region shares, the art-tech share, the funding share, institutions per
year, and the mix bucket. Also the art-tech venue set (C5) and the clamp at
programme entry (C8).

Why: every table in the bundle is an aggregate of these person-level numbers.
The functions are pure so the rule tests can call them without a bundle.

How to run: imported by ``python -m giye.career build``. This module is not a
script.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from giye.career.rules import COUNTRY_GROUPS, FAMILY_ORDER, KIND_FAMILY, QUANTILES
from giye.export.release import CONCENTRATION, quantile_allowed
from giye.normalize.kinds import KINDS

# A row with no venue text is not a country observation (C4). title_only is a
# work title, not a place.
_NO_VENUE = frozenset({"empty", "title_only"})


@dataclass(frozen=True)
class CvRow:
    """The columns of one ``cv:`` row the measures read. No title, URL, or id."""

    year: int | None
    kind: str
    country: str
    region: str
    venue_id: str
    venue_kind: str


def norm_name(value: str) -> str:
    """NFC and strip. C5 compares venue names to operator names on this form."""
    return unicodedata.normalize("NFC", value or "").strip()


def country_group(country: str, territory: str) -> str:
    """C4. ``home`` only when the country equals a non-empty territory.

    An empty country is ``unresolved``. Anything else is ``abroad``, including
    every non-empty country when the archive has no territory: ``home`` then
    matches nothing.
    """
    text = (country or "").strip().upper()
    if not text:
        return "unresolved"
    home = (territory or "").strip().upper()
    if home and text == home:
        return "home"
    return "abroad"


def career_age_at_entry(entry_year: int, first_year: int) -> tuple[int, bool]:
    """C8. ``(age, clamped)``. A negative difference becomes 0 and ``clamped`` is true."""
    raw = entry_year - first_year
    if raw < 0:
        return 0, True
    return raw, False


def arttech_venue_ids(
    rows: list[dict[str, str]],
    venues: list[dict[str, str]],
    operator_names: set[str],
) -> set[str]:
    """C5. Venue ids that hosted an edition or share a name with an admitted operator.

    ``rows`` are processed activity rows (``event_link`` and ``venue_id``).
    ``venues`` have ``venue_id``, ``name`` and pipe-joined ``aliases``.
    ``operator_names`` are already NFC-stripped. A plain venue, and an empty
    venue id, stay out.
    """
    found: set[str] = set()
    for row in rows:
        venue_id = (row.get("venue_id") or "").strip()
        if venue_id and (row.get("event_link") or "").strip():
            found.add(venue_id)
    if operator_names:
        for venue in venues:
            venue_id = (venue.get("venue_id") or "").strip()
            if not venue_id:
                continue
            names = [norm_name(venue.get("name") or "")]
            names.extend(norm_name(part) for part in (venue.get("aliases") or "").split("|"))
            if any(name and name in operator_names for name in names):
                found.add(venue_id)
    return found


def _in_age(row: CvRow, first_year: int, age: int, *, lower_bound: bool) -> bool:
    """A dated row at or before the age cutoff. C7 also drops years before ``first_year``."""
    if row.year is None:
        return False
    if row.year > first_year + age:
        return False
    return not (lower_bound and row.year < first_year)


def _shares(counts: dict[str, int], keys: tuple[str, ...] | list[str], denominator: int) -> dict[str, float]:
    if denominator <= 0:
        return {key: 0.0 for key in keys}
    return {key: counts.get(key, 0) / denominator for key in keys}


def family_shares_from_kinds(kind_shares: dict[str, float]) -> dict[str, float]:
    """C3f. Sum the kind shares that belong to each family. The kinds partition the families."""
    out = {family: 0.0 for family in FAMILY_ORDER}
    for kind, share in kind_shares.items():
        family = KIND_FAMILY.get(kind)
        if family is not None:
            out[family] += share
    return out


def mix_bucket(family_shares: dict[str, float], n_rows: int) -> str:
    """C9. The leading family when its share is at least one half, else ``mixed`` or ``none``.

    Ties keep the earlier family in ``FAMILY_ORDER`` because the comparison is
    strict: an equal share does not replace the family already chosen.
    """
    if n_rows <= 0:
        return "none"
    best = FAMILY_ORDER[0]
    best_share = -1.0
    for family in FAMILY_ORDER:
        share = family_shares.get(family, 0.0)
        if share > best_share:
            best = family
            best_share = share
    if best_share >= 0.5:
        return best
    return "mixed"


def region_keys(published: list[str]) -> list[str]:
    """Published region names, then the two residual buckets, in a stable order."""
    return list(published) + ["other_region", "unresolved"]


def measures_at(
    rows: tuple[CvRow, ...] | list[CvRow],
    first_year: int,
    age: int,
    *,
    territory: str,
    arttech: set[str],
    regions_published: list[str],
    year_before: int | None = None,
) -> dict[str, float]:
    """Person-level measures cumulative to ``age``, or strictly before ``year_before``.

    ``year_before`` is C8: pre-entry rows use ``year < entry year`` instead of
    the age cutoff. Institutions per year still uses the age span, except
    before entry, where the value is the distinct venue count (no division by
    a career length the entry table does not ask for). An empty denominator is
    0 so every person in a cell has a value for every measure.
    """
    if year_before is None:
        selected = [row for row in rows if _in_age(row, first_year, age, lower_bound=False)]
        institutions = {
            row.venue_id
            for row in rows
            if row.venue_id and _in_age(row, first_year, age, lower_bound=True)
        }
        per_year = len(institutions) / (age + 1)
    else:
        selected = [row for row in rows if row.year is not None and row.year < year_before]
        institutions = {row.venue_id for row in selected if row.venue_id}
        per_year = float(len(institutions))

    kind_counts: dict[str, int] = defaultdict(int)
    for row in selected:
        if row.kind in KIND_FAMILY:
            kind_counts[row.kind] += 1
    kind_shares = _shares(kind_counts, KINDS, len(selected))
    family = family_shares_from_kinds(kind_shares)

    venue_rows = [row for row in selected if row.venue_kind not in _NO_VENUE]
    country_counts: dict[str, int] = defaultdict(int)
    for row in venue_rows:
        country_counts[country_group(row.country, territory)] += 1
    country = _shares(country_counts, COUNTRY_GROUPS, len(venue_rows))

    home_rows = [row for row in venue_rows if country_group(row.country, territory) == "home"]
    region_counts: dict[str, int] = defaultdict(int)
    published = set(regions_published)
    for row in home_rows:
        region = (row.region or "").strip()
        if not region:
            region_counts["unresolved"] += 1
        elif region in published:
            region_counts[region] += 1
        else:
            region_counts["other_region"] += 1
    regions = _shares(region_counts, region_keys(regions_published), len(home_rows))

    with_id = [row for row in selected if row.venue_id]
    art = sum(1 for row in with_id if row.venue_id in arttech)
    art_share = (art / len(with_id)) if with_id else 0.0

    funding_hits = sum(1 for row in selected if row.venue_kind == "funder" or row.kind == "funding")
    funding = (funding_hits / len(selected)) if selected else 0.0

    out: dict[str, float] = {}
    for kind in KINDS:
        out[f"kind_share:{kind}"] = kind_shares[kind]
    for family_name in FAMILY_ORDER:
        out[f"family_share:{family_name}"] = family[family_name]
    for group in COUNTRY_GROUPS:
        out[f"country_share:{group}"] = country[group]
    for key, share in regions.items():
        out[f"region_share:{key}"] = share
    out["arttech_share"] = art_share
    out["funding_share"] = funding
    out["institutions_per_year"] = per_year
    out["institutions_before_entry"] = per_year if year_before is not None else 0.0
    out["n_rows"] = float(len(selected))
    return out


def position_measure_names(regions_published: list[str]) -> list[str]:
    """Column ``measure`` values for ``reference_position``, in write order."""
    names = [f"kind_share:{kind}" for kind in KINDS]
    names.extend(f"family_share:{family}" for family in FAMILY_ORDER)
    names.extend(f"country_share:{group}" for group in COUNTRY_GROUPS)
    names.extend(f"region_share:{key}" for key in region_keys(regions_published))
    names.extend(["arttech_share", "funding_share", "institutions_per_year"])
    return names


def entry_measure_names(regions_published: list[str]) -> list[str]:
    """``programme_entry`` measures. Region shares are not in the phase 1 list."""
    names = ["career_age_at_entry"]
    names.extend(f"kind_share:{kind}" for kind in KINDS)
    names.extend(f"family_share:{family}" for family in FAMILY_ORDER)
    names.extend(f"country_share:{group}" for group in COUNTRY_GROUPS)
    names.extend(["arttech_share", "institutions_before_entry"])
    return names


def trend_measure_names() -> list[str]:
    """Whole-career measures on ``field_trend``."""
    names = [f"family_share:{family}" for family in FAMILY_ORDER]
    names.extend(f"country_share:{group}" for group in COUNTRY_GROUPS)
    names.extend(["arttech_share", "funding_share", "institutions_per_year"])
    return names


def outcome_names() -> list[str]:
    """Next-window outcomes. Families are included beside the 18 kinds."""
    names = [f"kind:{kind}" for kind in KINDS]
    names.extend(f"family:{family}" for family in FAMILY_ORDER)
    names.extend(f"country:{group}" for group in COUNTRY_GROUPS)
    names.extend(["arttech", "funding"])
    return names


def share_measure(name: str) -> bool:
    """True when the measure is a share. Institutions per year is a rate, not a share."""
    return name.startswith(("kind_share:", "family_share:", "country_share:", "region_share:")) or name in {
        "arttech_share",
        "funding_share",
    }


def dominant_value(values: list[float]) -> float | None:
    """The value shared by at least 90 % of ``values``, else None.

    Values are grouped at 8 decimal places so two computations of the same
    fraction land in one bin. The returned number is that bin; the builder
    formats it with D4 when the cell's ``bound`` is ``yes``. Fewer than one
    value is not a concentration.
    """
    if not values:
        return None
    counts: dict[float, int] = defaultdict(int)
    for value in values:
        counts[round(float(value), 8)] += 1
    chosen, top = max(counts.items(), key=lambda item: item[1])
    if top / len(values) >= CONCENTRATION:
        return chosen
    return None


def concentrated(values: list[float]) -> bool:
    """D3 on a list of person-level values. True when one value is at least 90% of them."""
    return dominant_value(values) is not None


def dominant_country(shares: dict[str, float]) -> str:
    """The country group with the largest share. Ties follow ``COUNTRY_GROUPS`` order.

    Used only to build the country-group partition ``apply_disclosure`` checks.
    The published country figures remain the three shares, not this label.
    """
    best = COUNTRY_GROUPS[0]
    best_share = -1.0
    for group in COUNTRY_GROUPS:
        share = shares.get(group, 0.0)
        if share > best_share:
            best = group
            best_share = share
    return best


def quantile_slots(n_people: int, k: int) -> list[tuple[str, float]]:
    """D2. The quantile names ``quantile_allowed`` permits for this ``n_people``."""
    return [(name, q) for name, q in QUANTILES if quantile_allowed(n_people, q, k)]


def published_regions(people_regions: dict[str, set[str]], k: int) -> list[str]:
    """C4r. Region names that at least ``k`` distinct people have on a home row.

    ``people_regions`` maps a region to the set of ledger ids. Empty regions
    are not passed here; they are ``unresolved`` inside the share.
    """
    names = [name for name, ids in people_regions.items() if name and len(ids) >= k]
    return sorted(names)

