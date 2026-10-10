# SPDX-License-Identifier: AGPL-3.0-only
"""A caller's career, for the length of one request.

What: validates ``CareerEntry`` and derives the same measures the bundle uses
(career age, generation, kind and family shares, country groups, art-tech,
funding, institutions per year, mix, and a band against published quantiles).

Why: the server selects a pre-built cell. It does not score the person and it
does not keep the entries. Bands are labels, not ranks.

How to run: imported by ``python -m giye.mcp serve``. This module is not a
script.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from giye.career.measures import country_group, family_shares_from_kinds, mix_bucket, norm_name
from giye.career.rules import AGE_BANDS, COUNTRY_GROUPS, FAMILY_ORDER, KIND_FAMILY
from giye.explore.rim import generation_of
from giye.normalize.kinds import KINDS

# The reference table stops at the last C11 age. Above that, the career is
# clamped and the answer says the reference does not extend further.
MAX_REFERENCE_AGE = AGE_BANDS[-1][1]

_QUANTILE_NAMES = ("q10", "q25", "q50", "q75", "q90")


class CareerEntry(BaseModel):
    """One line of a career the client has already structured. No document, no name."""

    model_config = ConfigDict(extra="ignore")

    # Bounds are the phase 2 entry contract, not a disclosure rule.
    year: int = Field(ge=1900, le=2100)
    kind: str
    venue: str | None = None
    city: str | None = None
    country: str | None = None
    programme: str | None = None
    role: str | None = None

    @field_validator("venue", "city", "programme", "role", "kind", mode="before")
    @classmethod
    def _blank(cls, value: object) -> object:
        # A blank optional string is the same as omitting it. kind stays required.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("country", mode="before")
    @classmethod
    def _country(cls, value: object) -> str | None:
        if value is None:
            return None
        # A non-string is left for the field type to reject. Pydantic turns a
        # ValueError here into a validation error, but a type mismatch is its job.
        if not isinstance(value, str):
            return value
        text = value.strip().upper()
        if not text:
            return None
        if len(text) != 2 or not text.isalpha():
            raise ValueError("country must be an ISO 3166-1 alpha-2 code")
        return text


@dataclass(frozen=True)
class DerivedCareer:
    """Measures for one request. Not stored on the server."""

    first_year: int | None
    career_age: int | None
    generation: str | None
    beyond_reference: bool
    mix: str
    values: dict[str, float]
    unresolved_countries: int
    notes: tuple[str, ...]


def validate_entries(entries: list[CareerEntry], *, kinds: list[str], programmes: list[str]) -> str | None:
    """A message listing the accepted values, or None when every entry fits the vocabulary."""
    kind_set = set(kinds)
    programme_set = set(programmes)
    for entry in entries:
        if entry.kind not in kind_set:
            return "kind is not in the vocabulary. Accepted kinds: " + ", ".join(kinds)
        if entry.programme and entry.programme not in programme_set:
            return "programme is not in the vocabulary. Accepted programmes: " + ", ".join(programmes)
    return None


def _venue_key(value: str) -> str:
    """NFC, strip, then casefold. Art-tech venue names and institution counts use this."""
    return norm_name(value).casefold()


def _programme_name_keys(programmes: list[dict[str, str]]) -> set[str]:
    keys: set[str] = set()
    for programme in programmes:
        for field in ("name_ko", "name_en"):
            text = programme.get(field) or ""
            if text.strip():
                keys.add(_venue_key(text))
    return keys


def _arttech(entry: CareerEntry, names: set[str]) -> bool:
    """A set programme, or a venue equal to a programme's public name."""
    venue_matches = bool(entry.venue) and _venue_key(entry.venue or "") in names
    return bool(entry.programme) or venue_matches


def derive(
    entries: list[CareerEntry],
    *,
    territory: str,
    programmes: list[dict[str, str]],
    measure_names: list[str],
) -> DerivedCareer:
    """Career age and measures. Private-family years do not start the career (C1 analogue)."""
    public = [entry for entry in entries if KIND_FAMILY.get(entry.kind) != "private"]
    if not public:
        return DerivedCareer(
            first_year=None,
            career_age=None,
            generation=None,
            beyond_reference=False,
            mix="none",
            values={},
            unresolved_countries=0,
            notes=(),
        )
    first_year = min(entry.year for entry in public)
    raw_age = max(entry.year for entry in entries) - first_year
    beyond = raw_age > MAX_REFERENCE_AGE
    age = MAX_REFERENCE_AGE if beyond else max(0, raw_age)
    generation, _start = generation_of(first_year)
    window = [entry for entry in entries if entry.year <= first_year + age]
    values = _values(window, first_year, age, territory, programmes, measure_names)
    family = {
        name.removeprefix("family_share:"): values[name]
        for name in measure_names
        if name.startswith("family_share:")
    }
    # C9 reads the family shares. Kinds that are not in the seven families do not move the bucket.
    mix = mix_bucket({family_name: family.get(family_name, 0.0) for family_name in FAMILY_ORDER}, len(window))
    unresolved = sum(1 for entry in window if not entry.country)
    notes: list[str] = []
    if beyond:
        notes.append("beyond_reference")
    if unresolved:
        notes.append(f"unresolved_countries:{unresolved}")
    return DerivedCareer(
        first_year=first_year,
        career_age=age,
        generation=generation,
        beyond_reference=beyond,
        mix=mix,
        values=values,
        unresolved_countries=unresolved,
        notes=tuple(notes),
    )


def _values(
    window: list[CareerEntry],
    first_year: int,
    age: int,
    territory: str,
    programmes: list[dict[str, str]],
    measure_names: list[str],
) -> dict[str, float]:
    """One float per reference measure. An empty window is a zero vector, not a missing key."""
    kind_counts = {kind: 0 for kind in KINDS}
    for entry in window:
        if entry.kind in kind_counts:
            kind_counts[entry.kind] += 1
    denominator = len(window)
    kind_shares = {kind: (kind_counts[kind] / denominator if denominator else 0.0) for kind in KINDS}
    family = family_shares_from_kinds(kind_shares)
    country_counts = {group: 0 for group in COUNTRY_GROUPS}
    for entry in window:
        country_counts[country_group(entry.country or "", territory)] += 1
    country = {group: (country_counts[group] / denominator if denominator else 0.0) for group in COUNTRY_GROUPS}
    home_rows = [entry for entry in window if country_group(entry.country or "", territory) == "home"]
    # CareerEntry has no region. A home row therefore cannot name a region, so
    # the home denominator is unresolved. Abroad rows are not in that denominator.
    region_names = [name.removeprefix("region_share:") for name in measure_names if name.startswith("region_share:")]
    region = {name: 0.0 for name in region_names}
    if home_rows and "unresolved" in region:
        region["unresolved"] = 1.0
    names = _programme_name_keys(programmes)
    art = sum(1 for entry in window if _arttech(entry, names))
    art_share = art / denominator if denominator else 0.0
    funding_hits = sum(1 for entry in window if entry.kind == "funding")
    funding = funding_hits / denominator if denominator else 0.0
    venues = {
        _venue_key(entry.venue)
        for entry in window
        if entry.venue and entry.year >= first_year and _venue_key(entry.venue)
    }
    per_year = len(venues) / (age + 1)
    computed: dict[str, float] = {}
    for kind in KINDS:
        computed[f"kind_share:{kind}"] = kind_shares[kind]
    for family_name in FAMILY_ORDER:
        computed[f"family_share:{family_name}"] = family[family_name]
    for group in COUNTRY_GROUPS:
        computed[f"country_share:{group}"] = country[group]
    for name, share in region.items():
        computed[f"region_share:{name}"] = share
    computed["arttech_share"] = art_share
    computed["funding_share"] = funding
    computed["institutions_per_year"] = per_year
    return {name: computed.get(name, 0.0) for name in measure_names}


def assign_band(value: float, quantiles: dict[str, str]) -> str:
    """A band label from the quantiles that were published. Equal sits in the upper band.

    Five quantiles, then the quartiles, then the median, then no reference.
    There is no percentile rank and no score.
    """
    present = {name: _quantile(quantiles.get(name, "")) for name in _QUANTILE_NAMES}

    def has(*names: str) -> bool:
        return all(present[name] is not None for name in names)

    if has("q10", "q25", "q50", "q75", "q90"):
        q10, q25, q50, q75, q90 = (present[name] for name in _QUANTILE_NAMES)
        assert q10 is not None and q25 is not None and q50 is not None and q75 is not None and q90 is not None
        if value < q10:
            return "below_q10"
        if value < q25:
            return "q10_q25"
        if value < q50:
            return "q25_q50"
        if value < q75:
            return "q50_q75"
        if value < q90:
            return "q75_q90"
        return "above_q90"
    if has("q25", "q50", "q75"):
        q25, q50, q75 = present["q25"], present["q50"], present["q75"]
        assert q25 is not None and q50 is not None and q75 is not None
        if value < q25:
            return "below_q25"
        if value < q50:
            return "q25_q50"
        if value < q75:
            return "q50_q75"
        return "above_q75"
    if present["q50"] is not None:
        return "below_median" if value < present["q50"] else "above_median"
    return "no_reference"


def _quantile(value: str) -> float | None:
    if not value or value in {"suppressed", ">=90"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None
