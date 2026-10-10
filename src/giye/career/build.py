# SPDX-License-Identifier: AGPL-3.0-only
"""Build the career bundle: seven tables, disclosure, and a manifest.

What: reads one archive and writes ``reference_position``, ``next_window``,
``programme_profile``, ``programme_entry``, ``programme_transitions``,
``field_trend``, ``vocab.json`` and ``manifest.json``. Cells under ``k`` are
suppressed. Shares need ``pct_min`` people. The build fails when a suppressed
cell can be recovered, when an identifier column or value appears, or when a
suppressed edition matches ``--roster-facts``.

Why: protection lives in the build. The file that leaves the machine has to
be safe to read in full (docs/CAREER.md, phase 1 spec).

How to run (the venv interpreter):

  .venv/bin/python -m giye.career build --config <giye.toml> --out <directory>
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from giye.career.measures import (
    CvRow,
    career_age_at_entry,
    country_group,
    dominant_country,
    dominant_value,
    entry_measure_names,
    measures_at,
    mix_bucket,
    outcome_names,
    position_measure_names,
    quantile_slots,
    share_measure,
    trend_measure_names,
)
from giye.career.population import Population, default_current_year, load_population
from giye.career.rules import (
    AGE_BANDS,
    COUNTRY_GROUPS,
    FAMILIES,
    FAMILY_ORDER,
    KIND_FAMILY,
    NOT_COVERED,
    PCT_MIN,
    RULE_IDS,
    K,
    band_start,
    mix_buckets,
    window_open,
)
from giye.career.stats import bootstrap_means, weighted_quantile, wilson
from giye.config import Config, load
from giye.export.release import (
    AGGREGATE_FORBIDDEN,
    BOUND,
    SUPPRESSED,
    Cell,
    ReleaseError,
    apply_disclosure,
    round_to_5,
)
from giye.normalize.kinds import CHANNELS, KINDS

_GY = re.compile(r"^GY-\d{6}$")
_CAND = re.compile(r"^CAND-")
_CV = re.compile(r"^cv:")
_URL = re.compile(r"https?://", re.IGNORECASE)
_PS = re.compile(r"^ps[0-9a-f]{32}$")
_NO_VENUE = frozenset({"empty", "title_only"})

REFERENCE_COLUMNS = [
    "career_age",
    "generation",
    "measure",
    "n_people",
    "q10",
    "q25",
    "q50",
    "q75",
    "q90",
    "w_q10",
    "w_q25",
    "w_q50",
    "w_q75",
    "w_q90",
    "bound",
    "bound_value",
]
NEXT_COLUMNS = [
    "age_band",
    "generation",
    "mix_bucket",
    "outcome",
    "n_people",
    "n_censored",
    "share",
    "share_lo",
    "share_hi",
    "w_share",
    "bound",
    "bound_value",
]
PROFILE_COLUMNS = [
    "programme",
    "name_en",
    "name_ko",
    "access_mode",
    "first_year",
    "last_year",
    "n_editions",
    "n_people",
    "edition_size_q25",
    "edition_size_q50",
    "edition_size_q75",
    "returners_share",
    "team_share",
    "n_people_cv_layer",
]
ENTRY_COLUMNS = [
    "programme",
    "measure",
    "n_people",
    "q10",
    "q25",
    "q50",
    "q75",
    "q90",
    "w_q10",
    "w_q25",
    "w_q50",
    "w_q75",
    "w_q90",
    "bound",
    "bound_value",
]
TRANSITION_COLUMNS = [
    "programme_from",
    "programme_to",
    "n_at_risk",
    "n_movers",
    "gap_q25",
    "gap_q50",
    "gap_q75",
    "n_censored",
]
TREND_COLUMNS = [
    "generation",
    "measure",
    "n_people",
    "mean",
    "mean_lo",
    "mean_hi",
    "w_mean",
    "w_mean_lo",
    "w_mean_hi",
    "bound",
    "bound_value",
]

_QUANTILE_NAMES = ["q10", "q25", "q50", "q75", "q90"]
_COUNT_COLUMNS = frozenset(
    {"n_people", "n_censored", "n_at_risk", "n_movers", "n_editions", "n_people_cv_layer"}
)


class CareerBuildError(Exception):
    """A disclosure or identifier check failed. The message is one line and names the rule."""


def _fmt(value: float, digits: int = 2) -> str:
    """D4. Round to ``digits`` decimals, then drop trailing zeros.

    The default is 2, so ``0.37`` is 37 %. Shares, means, interval bounds and
    quantiles are rounded from the unrounded value; a value that rounds to
    zero is ``0``. Finer decimals would publish more than the rounded count.
    """
    rounded = round(float(value), digits)
    if rounded == 0:
        return "0"
    text = f"{rounded:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _fmt_bounds(
    proportion: float,
    low: float,
    high: float,
    digits: int = 2,
    *,
    outward: bool = False,
) -> tuple[str, str, str]:
    """Format a share and its interval so the written numbers still nest.

    A Wilson interval (``outward``) rounds the lower bound down and the upper
    bound up at two decimals, ``floor(x*100)/100`` and ``ceil(x*100)/100``,
    so rounding cannot put the interval on the wrong side of the share. The
    ``1e-9`` keeps a value that is already on a hundredth from slipping
    because ``x*100`` is not exact in binary. Any remaining gap is closed by
    pulling the bound back to the share.
    """
    if outward:
        low = math.floor(low * 100 + 1e-9) / 100
        high = math.ceil(high * 100 - 1e-9) / 100
    p_text, lo_text, hi_text = _fmt(proportion, digits), _fmt(low, digits), _fmt(high, digits)
    if float(lo_text) > float(p_text):
        lo_text = p_text
    if float(hi_text) < float(p_text):
        hi_text = p_text
    return p_text, lo_text, hi_text


def _bound_pair(values: list[float], digits: int = 2) -> tuple[str, str]:
    """D3. ``("yes", value)`` when one value is at least 90 %, else ``("no", "")``.

    ``value`` is that shared number, formatted the same way as a published share.
    """
    top = dominant_value(values)
    if top is None:
        return "no", ""
    return "yes", _fmt(top, digits)


def _show_count(n: int, k: int) -> str:
    """D1 and D4. Under ``k`` is ``suppressed``, never a rounded stand-in for a small cell."""
    if n < k:
        return SUPPRESSED
    rounded = round_to_5(n)
    if n > 0 and rounded == 0:
        return SUPPRESSED
    return str(rounded)


def _round_count(n: int) -> str:
    """D4 for a count that is not a cell size. A positive count that rounds to 0 is suppressed.

    Rounding a 1 or a 2 down to 0 would publish a zero for a group that is not
    empty, which is the same false statement D1 refuses for ``n_people``.
    """
    if n <= 0:
        return "0"
    rounded = round_to_5(n)
    if rounded == 0:
        return SUPPRESSED
    return str(rounded)


def _empty_quantiles(prefix: str = "") -> dict[str, str]:
    return {f"{prefix}{name}": "" for name in _QUANTILE_NAMES}


def _fill_quantiles(
    values: list[float],
    weights: list[float] | None,
    n_people: int,
    k: int,
    *,
    weighted: bool,
    digits: int = 2,
) -> dict[str, str]:
    """D2. A quantile is written only when ``quantile_allowed`` says so. Never min or max.

    ``digits`` is 2 for a share or a rate and 1 for a year (career age, gap).
    The quantile is computed from the unrounded values and then formatted.
    """
    allowed = {name for name, _q in quantile_slots(n_people, k)}
    out: dict[str, str] = {}
    use_weights = weights if weighted else None
    for name in _QUANTILE_NAMES:
        key = f"w_{name}" if weighted else name
        if name not in allowed or not values:
            out[key] = ""
            continue
        q = next(q for label, q in quantile_slots(n_people, k) if label == name)
        w = use_weights if use_weights is not None else [1.0] * len(values)
        out[key] = _fmt(weighted_quantile(values, w, q), digits)
    return out


def _part_share(
    cells: dict[tuple, Cell],
    key: tuple,
    n_part: int,
    n_people: int,
    k: int,
    pct_min: int,
) -> str:
    """The published share of one side of a returner or team split.

    Under ``pct_min`` or ``k`` the share is suppressed. A D3 bound replaces the
    exact proportion. An incomplete group (the other side suppressed) does not
    publish the residual.
    """
    if n_people < pct_min or n_people < k:
        return SUPPRESSED
    cell = cells[key]
    if cell.share == BOUND or cell.status == "bound":
        return BOUND
    if cell.status != "publish" or cell.share == SUPPRESSED:
        return SUPPRESSED
    if n_people <= 0:
        return SUPPRESSED
    return _fmt(n_part / n_people)


def gate_share(n_people: int, pct_min: int, text: str) -> str:
    """PCT_MIN. A share for ``k <= n < pct_min`` is ``suppressed``; at ``pct_min`` the text is kept.

    ``text`` is the proportion, or the D3 bound, that the cell would otherwise publish.
    """
    if n_people < pct_min:
        return SUPPRESSED
    return text


def _disclose(cells: dict[tuple, Cell], groups: list, totals: list, k: int) -> None:
    """Run D1–D5. A ``ReleaseError`` becomes ``CareerBuildError`` so the exit line names the rule."""
    try:
        apply_disclosure(cells, groups, [], totals, k)
    except ReleaseError as exc:
        raise CareerBuildError(str(exc)) from exc


def _vector(person, age: int, population: Population, *, year_before: int | None = None) -> dict[str, float]:
    return measures_at(
        person.rows,
        person.first_year,
        age,
        territory=population.territory,
        arttech=population.arttech,
        regions_published=population.regions_published,
        year_before=year_before,
    )


def _window_rows(rows: tuple[CvRow, ...], first_year: int, age: int) -> list[CvRow]:
    """CV rows in ``(first_year + age + 1 .. first_year + age + 3)``, inclusive."""
    start = first_year + age + 1
    end = first_year + age + 3
    return [row for row in rows if row.year is not None and start <= row.year <= end]


def _hits(rows: list[CvRow], outcome: str, territory: str, arttech: set[str]) -> bool:
    if outcome.startswith("kind:"):
        kind = outcome.split(":", 1)[1]
        return any(row.kind == kind for row in rows)
    if outcome.startswith("family:"):
        family = outcome.split(":", 1)[1]
        return any(KIND_FAMILY.get(row.kind) == family for row in rows)
    if outcome.startswith("country:"):
        group = outcome.split(":", 1)[1]
        return any(
            row.venue_kind not in _NO_VENUE and country_group(row.country, territory) == group for row in rows
        )
    if outcome == "arttech":
        return any(row.venue_id in arttech for row in rows if row.venue_id)
    if outcome == "funding":
        return any(row.venue_kind == "funder" or row.kind == "funding" for row in rows)
    return False


def _reference(population: Population, k: int, current_year: int, weighted: bool) -> tuple[list[dict], int, int, int]:
    measures = position_measure_names(population.regions_published)
    by_gen: dict[str, list] = defaultdict(list)
    censored = 0
    for person in population.cv_ready:
        by_gen[person.generation].append(person)
        if person.first_year is not None and person.first_year + 30 > current_year:
            censored += 1
    generations = sorted(by_gen)
    # One cell per (age, generation). D1 only: generations overlap across ages,
    # so they are not a partition the spec puts in ``totals``.
    rows: list[dict] = []
    suppressed = 0
    # C12. Each person-age that has been reached but has no dated row in the window.
    no_rows = 0
    for age in range(31):
        for generation in generations:
            reached = [person for person in by_gen[generation] if person.first_year + age <= current_year]
            members = []
            vectors = []
            for person in reached:
                vector = _vector(person, age, population)
                # C12. year <= first_year + age. A zero vector is not a measurement.
                if vector["n_rows"] < 1:
                    no_rows += 1
                    continue
                members.append(person)
                vectors.append(vector)
            n = len(members)
            count = _show_count(n, k)
            if count == SUPPRESSED:
                suppressed += len(measures)
                for measure in measures:
                    rows.append(
                        {
                            "career_age": str(age),
                            "generation": generation,
                            "measure": measure,
                            "n_people": SUPPRESSED,
                            "bound": "",
                            "bound_value": "",
                            **_empty_quantiles(),
                            **_empty_quantiles("w_"),
                        }
                    )
                continue
            weights = [person.weight for person in members]
            for measure in measures:
                values = [vector[measure] for vector in vectors]
                bound, bound_value = _bound_pair(values)
                if bound == "yes":
                    quantiles = _empty_quantiles()
                    w_quantiles = _empty_quantiles("w_")
                else:
                    quantiles = _fill_quantiles(values, weights, n, k, weighted=False)
                    w_quantiles = (
                        _fill_quantiles(values, weights, n, k, weighted=True) if weighted else _empty_quantiles("w_")
                    )
                rows.append(
                    {
                        "career_age": str(age),
                        "generation": generation,
                        "measure": measure,
                        "n_people": count,
                        "bound": bound,
                        "bound_value": bound_value,
                        **quantiles,
                        **w_quantiles,
                    }
                )
    rows.sort(key=lambda row: (int(row["career_age"]), row["generation"], row["measure"]))
    return rows, suppressed, censored, no_rows


def _next_window(
    population: Population, k: int, pct_min: int, current_year: int, weighted: bool
) -> tuple[list[dict], int, int, int]:
    by_gen: dict[str, list] = defaultdict(list)
    for person in population.cv_ready:
        by_gen[person.generation].append(person)
    generations = sorted(by_gen)
    buckets = mix_buckets()
    outcomes = outcome_names()
    # Disclosure cells: uncensored people, partitioned by mix bucket inside each
    # (band, generation). Country group is a second partition, passed as a
    # group so D3 can see a 90% country, not as a published table.
    cells: dict[tuple, Cell] = {}
    groups: list = []
    totals: list = []
    held: dict[tuple, dict] = {}
    censored_people = 0
    # C12. Each person-band with no dated row at the band start.
    no_rows = 0
    for person in population.cv_ready:
        if person.first_year is not None and not window_open(person.first_year, band_start("21-30"), current_year):
            censored_people += 1
    for start, _end, label in AGE_BANDS:
        for generation in generations:
            grouped = {bucket: {"in": [], "out": []} for bucket in buckets}
            country_ids: dict[str, list] = {group: [] for group in COUNTRY_GROUPS}
            for person in by_gen[generation]:
                vector = _vector(person, start, population)
                # C12. The cell window is year <= first_year + band start.
                if vector["n_rows"] < 1:
                    no_rows += 1
                    continue
                families = {family: vector[f"family_share:{family}"] for family in FAMILY_ORDER}
                bucket = mix_bucket(families, int(vector["n_rows"]))
                if window_open(person.first_year, start, current_year):
                    grouped[bucket]["in"].append(person)
                    shares = {group: vector[f"country_share:{group}"] for group in COUNTRY_GROUPS}
                    country_ids[dominant_country(shares)].append(person)
                else:
                    grouped[bucket]["out"].append(person)
            mix_keys = []
            for bucket in buckets:
                key = ("mix", label, generation, bucket)
                cells[key] = Cell(n_people=len(grouped[bucket]["in"]))
                mix_keys.append(key)
                held[key] = grouped[bucket]
            totals.append((mix_keys, "people"))
            country_keys = []
            for group in COUNTRY_GROUPS:
                key = ("country", label, generation, group)
                cells[key] = Cell(n_people=len(country_ids[group]))
                country_keys.append(key)
            groups.append(("people", country_keys))
    _disclose(cells, groups, totals, k)

    rows: list[dict] = []
    suppressed = 0
    for start, _end, label in AGE_BANDS:
        for generation in generations:
            for bucket in buckets:
                key = ("mix", label, generation, bucket)
                members = held[key]["in"]
                censored_n = len(held[key]["out"])
                if not members and censored_n == 0:
                    continue
                status = cells[key].status
                n = len(members)
                count = SUPPRESSED if status != "publish" else _show_count(n, k)
                if count == SUPPRESSED:
                    suppressed += len(outcomes)
                censored_text = _round_count(censored_n)
                window = {
                    person.ledger_id: _window_rows(person.rows, person.first_year, start) for person in members
                }
                for outcome in outcomes:
                    flags = []
                    for person in members:
                        hit = _hits(window[person.ledger_id], outcome, population.territory, population.arttech)
                        flags.append(1.0 if hit else 0.0)
                    share = share_lo = share_hi = w_share = ""
                    bound = ""
                    bound_value = ""
                    top = dominant_value(flags)
                    if count == SUPPRESSED:
                        share = share_lo = share_hi = SUPPRESSED
                        w_share = "" if not weighted else SUPPRESSED
                    elif top is not None and n >= pct_min:
                        bound = "yes"
                        bound_value = _fmt(top)
                        share = gate_share(n, pct_min, BOUND)
                        w_share = BOUND if weighted else ""
                    elif n < pct_min:
                        share = share_lo = share_hi = gate_share(n, pct_min, "0")
                        w_share = "" if not weighted else SUPPRESSED
                    else:
                        successes = int(sum(flags))
                        proportion, low, high = wilson(successes, n)
                        share, share_lo, share_hi = _fmt_bounds(proportion, low, high, outward=True)
                        share = gate_share(n, pct_min, share)
                        share_lo = gate_share(n, pct_min, share_lo)
                        share_hi = gate_share(n, pct_min, share_hi)
                        bound = "no"
                        if weighted and members:
                            w_total = sum(person.weight for person in members)
                            w_hit = sum(person.weight for person, flag in zip(members, flags) if flag)
                            w_share = gate_share(n, pct_min, _fmt(w_hit / w_total) if w_total > 0 else "0")
                    rows.append(
                        {
                            "age_band": label,
                            "generation": generation,
                            "mix_bucket": bucket,
                            "outcome": outcome,
                            "n_people": count,
                            "n_censored": censored_text,
                            "share": share,
                            "share_lo": share_lo,
                            "share_hi": share_hi,
                            "w_share": w_share,
                            "bound": bound,
                            "bound_value": bound_value,
                        }
                    )
    band_order = {label: index for index, (_s, _e, label) in enumerate(AGE_BANDS)}
    rows.sort(key=lambda row: (band_order[row["age_band"]], row["generation"], row["mix_bucket"], row["outcome"]))
    return rows, suppressed, censored_people, no_rows


def _profile(
    population: Population, k: int, pct_min: int
) -> tuple[list[dict], int, dict[tuple[str, int], int]]:
    edition_members: dict[tuple[str, int], set[str]] = defaultdict(set)
    for person in population.people.values():
        for programme, year in person.editions:
            edition_members[(programme, year)].add(person.ledger_id)
    by_programme: dict[str, set[int]] = defaultdict(set)
    for programme, year in edition_members:
        by_programme[programme].add(year)

    cells: dict[tuple, Cell] = {}
    groups: list = []
    info: dict[str, dict] = {}
    for programme in sorted(by_programme):
        years = sorted(by_programme[programme])
        people = []
        for person in population.people.values():
            theirs = [year for code, year in person.editions if code == programme]
            if theirs:
                people.append((person, theirs))
        n_return = sum(1 for _person, theirs in people if len(theirs) >= 2)
        n_team = sum(1 for person, _theirs in people if person.team)
        n = len(people)
        cells[("returner", programme)] = Cell(n_people=n_return)
        cells[("other", programme)] = Cell(n_people=n - n_return)
        cells[("team", programme)] = Cell(n_people=n_team)
        cells[("nonteam", programme)] = Cell(n_people=n - n_team)
        groups.append(("people", [("returner", programme), ("other", programme)]))
        groups.append(("people", [("team", programme), ("nonteam", programme)]))
        info[programme] = {
            "years": years,
            "people": people,
            "sizes": [len(edition_members[(programme, year)]) for year in years],
        }
    if cells:
        _disclose(cells, groups, [], k)

    rows: list[dict] = []
    suppressed = 0
    true_editions = {key: len(ids) for key, ids in edition_members.items()}
    for programme in sorted(info):
        frame = population.programmes.get(programme)
        years = info[programme]["years"]
        people = info[programme]["people"]
        sizes = info[programme]["sizes"]
        n = len(people)
        count = _show_count(n, k)
        if count == SUPPRESSED:
            suppressed += 1
        n_editions = len(years)
        if n_editions < k:
            q25 = q50 = q75 = SUPPRESSED
        else:
            edition_q = _fill_quantiles([float(size) for size in sizes], None, n_editions, k, weighted=False)
            q25 = edition_q["q25"] or SUPPRESSED
            q50 = edition_q["q50"] or SUPPRESSED
            q75 = edition_q["q75"] or SUPPRESSED
            # D2 leaves a quantile blank when the edition count is too small.
            # The spec asks for the word suppressed, not an empty cell, when
            # fewer than k editions exist. Above k, a blank from D2 is also
            # written suppressed so the column is never a missing quantile
            # that a reader could treat as zero.
            if not edition_q["q25"]:
                q25 = SUPPRESSED
            if not edition_q["q50"]:
                q50 = SUPPRESSED
            if not edition_q["q75"]:
                q75 = SUPPRESSED
        n_cv = sum(1 for person, _theirs in people if person.cv)
        n_return = sum(1 for _person, theirs in people if len(theirs) >= 2)
        n_team = sum(1 for person, _theirs in people if person.team)
        rows.append(
            {
                "programme": programme,
                "name_en": frame.name_en if frame else "",
                "name_ko": frame.name_ko if frame else "",
                "access_mode": frame.access_mode if frame else "",
                "first_year": str(years[0]),
                "last_year": str(years[-1]),
                "n_editions": _round_count(n_editions) if n_editions else "0",
                "n_people": count,
                "edition_size_q25": q25,
                "edition_size_q50": q50,
                "edition_size_q75": q75,
                "returners_share": _part_share(cells, ("returner", programme), n_return, n, k, pct_min),
                "team_share": _part_share(cells, ("team", programme), n_team, n, k, pct_min),
                "n_people_cv_layer": _show_count(n_cv, k),
            }
        )
    rows.sort(key=lambda row: row["programme"])
    return rows, suppressed, true_editions


def _entry(population: Population, k: int, weighted: bool) -> tuple[list[dict], int, int, int, int]:
    """Returns rows, suppressed rows, clamped entries, censored people (none), and C12 omissions."""
    measures = entry_measure_names(population.regions_published)
    by_programme: dict[str, list] = defaultdict(list)
    clamped = 0
    # C12. Each person-programme whose pre-entry window (year < entry year) is empty.
    no_rows = 0
    for person in population.cv_ready:
        firsts: dict[str, int] = {}
        for programme, year in person.editions:
            if programme not in firsts or year < firsts[programme]:
                firsts[programme] = year
        for programme, year in firsts.items():
            age, was_clamped = career_age_at_entry(year, person.first_year)
            if was_clamped:
                clamped += 1
            vector = _vector(person, age, population, year_before=year)
            if vector["n_rows"] < 1:
                no_rows += 1
                continue
            by_programme[programme].append((person, year, age, vector))
    rows: list[dict] = []
    suppressed = 0
    for programme in sorted(by_programme):
        members = by_programme[programme]
        n = len(members)
        count = _show_count(n, k)
        if count == SUPPRESSED:
            suppressed += len(measures)
            for measure in measures:
                rows.append(
                    {
                        "programme": programme,
                        "measure": measure,
                        "n_people": SUPPRESSED,
                        "bound": "",
                        "bound_value": "",
                        **_empty_quantiles(),
                        **_empty_quantiles("w_"),
                    }
                )
            continue
        vectors = []
        ages = []
        weights = []
        for person, _year, age, vector in members:
            vectors.append(vector)
            ages.append(float(age))
            weights.append(person.weight)
        for measure in measures:
            # Career age is in years: one decimal. Shares and rates stay at two.
            digits = 1 if measure == "career_age_at_entry" else 2
            values = ages if measure == "career_age_at_entry" else [vector[measure] for vector in vectors]
            bound, bound_value = _bound_pair(values, digits)
            if bound == "yes":
                quantiles = _empty_quantiles()
                w_quantiles = _empty_quantiles("w_")
            else:
                quantiles = _fill_quantiles(values, weights, n, k, weighted=False, digits=digits)
                w_quantiles = _empty_quantiles("w_")
                if weighted:
                    w_quantiles = _fill_quantiles(values, weights, n, k, weighted=True, digits=digits)
            rows.append(
                {
                    "programme": programme,
                    "measure": measure,
                    "n_people": count,
                    "bound": bound,
                    "bound_value": bound_value,
                    **quantiles,
                    **w_quantiles,
                }
            )
    rows.sort(key=lambda row: (row["programme"], row["measure"]))
    return rows, suppressed, clamped, 0, no_rows


def _transitions(
    population: Population, k: int, current_year: int
) -> tuple[list[dict], int, int, list[dict]]:
    programmes = sorted({programme for person in population.people.values() for programme, _year in person.editions})
    first_of: dict[str, dict[str, int]] = {}
    for person in population.people.values():
        earliest: dict[str, int] = {}
        for programme, year in person.editions:
            if programme not in earliest or year < earliest[programme]:
                earliest[programme] = year
        first_of[person.ledger_id] = earliest

    rows: list[dict] = []
    suppressed = 0
    censored_pairs = 0
    true_rows: list[dict] = []
    for source in programmes:
        for target in programmes:
            if source == target:
                continue
            at_risk: list[tuple] = []
            censored = 0
            for person in population.people.values():
                earliest = first_of[person.ledger_id]
                if source not in earliest:
                    continue
                if earliest[source] <= current_year - 1:
                    at_risk.append(person)
                else:
                    censored += 1
                    censored_pairs += 1
            movers = []
            gaps = []
            for person in at_risk:
                earliest = first_of[person.ledger_id]
                if target in earliest and earliest[target] > earliest[source]:
                    movers.append(person)
                    gaps.append(float(earliest[target] - earliest[source]))
            true_rows.append(
                {
                    "programme_from": source,
                    "programme_to": target,
                    "n_at_risk": len(at_risk),
                    "n_movers": len(movers),
                }
            )
            if len(at_risk) < k:
                suppressed += 1
                rows.append(
                    {
                        "programme_from": source,
                        "programme_to": target,
                        "n_at_risk": SUPPRESSED,
                        "n_movers": SUPPRESSED,
                        "gap_q25": "",
                        "gap_q50": "",
                        "gap_q75": "",
                        "n_censored": _round_count(censored),
                    }
                )
                continue
            mover_count = _show_count(len(movers), k)
            if mover_count == SUPPRESSED:
                gaps_out = {"q25": "", "q50": "", "q75": ""}
            else:
                filled = _fill_quantiles(gaps, None, len(movers), k, weighted=False, digits=1)
                gaps_out = {name: filled[name] for name in ("q25", "q50", "q75")}
            rows.append(
                {
                    "programme_from": source,
                    "programme_to": target,
                    "n_at_risk": _show_count(len(at_risk), k),
                    "n_movers": mover_count,
                    "gap_q25": gaps_out["q25"],
                    "gap_q50": gaps_out["q50"],
                    "gap_q75": gaps_out["q75"],
                    "n_censored": _round_count(censored),
                }
            )
    rows.sort(key=lambda row: (row["programme_from"], row["programme_to"]))
    return rows, suppressed, censored_pairs, true_rows


def _trend(
    population: Population, k: int, pct_min: int, current_year: int, weighted: bool, rng: random.Random
) -> tuple[list[dict], int, int]:
    by_gen: dict[str, list] = defaultdict(list)
    censored = 0
    for person in population.cv_ready:
        if person.first_year > current_year:
            censored += 1
            continue
        by_gen[person.generation].append(person)
    cells = {generation: Cell(n_people=len(members)) for generation, members in by_gen.items()}
    groups: list = []
    if by_gen:
        # Country-group partition of the people who enter the trend, one group
        # per generation so a 90% country is visible to D3.
        for generation, members in by_gen.items():
            counts = {group: 0 for group in COUNTRY_GROUPS}
            for person in members:
                vector = _vector(person, current_year - person.first_year, population)
                counts[dominant_country({group: vector[f"country_share:{group}"] for group in COUNTRY_GROUPS})] += 1
            keys = []
            for group in COUNTRY_GROUPS:
                key = ("country", generation, group)
                cells[key] = Cell(n_people=counts[group])
                keys.append(key)
            groups.append(("people", keys))
        _disclose(cells, groups, [(list(by_gen), "people")], k)
    measures = trend_measure_names()
    rows: list[dict] = []
    suppressed = 0
    for generation in sorted(by_gen):
        members = by_gen[generation]
        n = len(members)
        status = cells[generation].status
        count = SUPPRESSED if status != "publish" else _show_count(n, k)
        if count == SUPPRESSED:
            suppressed += len(measures)
            for measure in measures:
                rows.append(
                    {
                        "generation": generation,
                        "measure": measure,
                        "n_people": SUPPRESSED,
                        "mean": "",
                        "mean_lo": "",
                        "mean_hi": "",
                        "w_mean": "",
                        "w_mean_lo": "",
                        "w_mean_hi": "",
                        "bound": "",
                        "bound_value": "",
                    }
                )
            continue
        vectors = [_vector(person, current_year - person.first_year, population) for person in members]
        weights = [person.weight for person in members]
        for measure in measures:
            values = [vector[measure] for vector in vectors]
            is_share = share_measure(measure)
            bound, bound_value = _bound_pair(values)
            if bound == "yes":
                rows.append(
                    {
                        "generation": generation,
                        "measure": measure,
                        "n_people": count,
                        "mean": "",
                        "mean_lo": "",
                        "mean_hi": "",
                        "w_mean": "",
                        "w_mean_lo": "",
                        "w_mean_hi": "",
                        "bound": "yes",
                        "bound_value": bound_value,
                    }
                )
                continue
            if is_share and n < pct_min:
                rows.append(
                    {
                        "generation": generation,
                        "measure": measure,
                        "n_people": count,
                        "mean": SUPPRESSED,
                        "mean_lo": SUPPRESSED,
                        "mean_hi": SUPPRESSED,
                        "w_mean": SUPPRESSED if weighted else "",
                        "w_mean_lo": SUPPRESSED if weighted else "",
                        "w_mean_hi": SUPPRESSED if weighted else "",
                        "bound": "no",
                        "bound_value": "",
                    }
                )
                continue
            mean, lo, hi, w_mean, w_lo, w_hi = bootstrap_means(values, weights, rng)
            mean_text, lo_text, hi_text = _fmt_bounds(mean, lo, hi)
            if weighted:
                w_mean_text, w_lo_text, w_hi_text = _fmt_bounds(w_mean, w_lo, w_hi)
            else:
                w_mean_text = w_lo_text = w_hi_text = ""
            rows.append(
                {
                    "generation": generation,
                    "measure": measure,
                    "n_people": count,
                    "mean": mean_text,
                    "mean_lo": lo_text,
                    "mean_hi": hi_text,
                    "w_mean": w_mean_text,
                    "w_mean_lo": w_lo_text,
                    "w_mean_hi": w_hi_text,
                    "bound": "no",
                    "bound_value": "",
                }
            )
    rows.sort(key=lambda row: (row["generation"], row["measure"]))
    return rows, suppressed, censored


def _vocab(population: Population) -> dict:
    programmes = [
        {"code": programme.code, "name_en": programme.name_en, "name_ko": programme.name_ko}
        for programme in sorted(population.programmes.values(), key=lambda item: item.code)
    ]
    generations = sorted({person.generation for person in population.cv_ready if person.generation})
    return {
        "kinds": list(KINDS),
        "families": {family: list(kinds) for family, kinds in FAMILIES.items()},
        "channels": list(CHANNELS),
        "country_groups": list(COUNTRY_GROUPS),
        "regions_published": list(population.regions_published),
        "programmes": programmes,
        "generations": generations,
        "age_bands": [label for _start, _end, label in AGE_BANDS],
        "mix_buckets": list(mix_buckets()),
        "measures": {
            "reference_position": position_measure_names(population.regions_published),
            "next_window": outcome_names(),
            "programme_entry": entry_measure_names(population.regions_published),
            "field_trend": trend_measure_names(),
        },
        "rule_ids": list(RULE_IDS),
        "not_covered": list(NOT_COVERED),
    }


def _bad_value(value: str) -> bool:
    if _GY.fullmatch(value) or _PS.fullmatch(value):
        return True
    if _CAND.match(value) or _CV.match(value):
        return True
    return bool(_URL.search(value))


def _scan_table(name: str, columns: list[str], rows: list[dict], k: int) -> None:
    """D5 identifier and under-k scans on one table. Raises ``CareerBuildError``."""
    # name_ko and name_en are in AGGREGATE_FORBIDDEN because a person table must
    # not grow them. programme_profile is required to carry the programme's own
    # names (a public body, not a person). Those two columns are the exception.
    forbidden = (AGGREGATE_FORBIDDEN | {"ledger_id"}) - {"name_ko", "name_en"}
    bad_columns = sorted(forbidden & set(columns))
    if bad_columns:
        raise CareerBuildError("D5: an aggregate column could hold an identifier: " + ", ".join(bad_columns))
    for row in rows:
        for column in columns:
            value = row.get(column, "")
            if _bad_value(value):
                raise CareerBuildError(f"D5: an aggregate value could hold an identifier in {name}")
            if column == "n_people" and value not in {SUPPRESSED, BOUND, ""}:
                number = int(value)
                if number < k:
                    raise CareerBuildError("D1: a cell under k would be published")
            if column in _COUNT_COLUMNS and value not in {SUPPRESSED, BOUND, ""}:
                number = int(value)
                if number % 5 != 0:
                    raise CareerBuildError("D4: a published count is not a multiple of 5")


def _roster_check(
    path: Path,
    true_editions: dict[tuple[str, int], int],
    true_transitions: list[dict],
    k: int,
    current_year: int,
) -> list[dict]:
    """Suppressed edition and transition counts that ``roster_facts`` already shows.

    An edition is suppressed when its non-staff size is under ``k`` (the size
    is not published). A transition row is suppressed when ``n_at_risk`` is
    under ``k``; ``n_movers`` is suppressed on its own when it is under ``k``.
    """
    by_person: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            gy = (row.get("gy_id") or "").strip()
            programme = (row.get("programme") or "").strip()
            year_text = (row.get("year") or "").strip()
            if not gy or not programme or not re.fullmatch(r"\d{4}", year_text):
                continue
            by_person[gy][programme].add(int(year_text))
    edition_derived: dict[tuple[str, str], set[str]] = defaultdict(set)
    for gy, programmes in by_person.items():
        for programme, years in programmes.items():
            for year in years:
                edition_derived[(programme, str(year))].add(gy)
    found: list[dict] = []
    for (programme, year), size in sorted(true_editions.items()):
        if size <= 0 or size >= k:
            continue
        derived = len(edition_derived.get((programme, str(year)), ()))
        if derived == size:
            found.append({"table": "programme_profile", "programme": programme, "year": str(year), "n_people": size})
    for row in true_transitions:
        source, target = row["programme_from"], row["programme_to"]
        at_risk = 0
        movers = 0
        for programmes in by_person.values():
            source_years = programmes.get(source)
            if not source_years:
                continue
            first = min(source_years)
            if first <= current_year - 1:
                at_risk += 1
                target_years = programmes.get(target)
                if target_years and min(target_years) > first:
                    movers += 1
        if row["n_at_risk"] < k and at_risk == row["n_at_risk"] and row["n_at_risk"] > 0:
            found.append(
                {
                    "table": "programme_transitions",
                    "programme_from": source,
                    "programme_to": target,
                    "n_people": row["n_at_risk"],
                }
            )
        if row["n_movers"] < k and movers == row["n_movers"] and row["n_movers"] > 0:
            found.append(
                {
                    "table": "programme_transitions",
                    "programme_from": source,
                    "programme_to": target,
                    "n_people": row["n_movers"],
                    "which": "n_movers",
                }
            )
    return found


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _read_weights(path: Path) -> dict[str, float]:
    weights: dict[str, float] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ledger_id = (row.get("ledger_id") or "").strip()
            if ledger_id:
                weights[ledger_id] = float(row.get("weight") or "1")
    return weights


def _ledger_version(config: Config) -> dict[str, str]:
    manifest_path = config.processed / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "rules_version": str(document.get("rules_version") or ""),
        "inputs_sha256": str(document.get("inputs_sha256") or ""),
    }


def build(
    config: Config,
    out: str | Path,
    *,
    weights: str | Path | None = None,
    k: int = K,
    pct_min: int = PCT_MIN,
    roster_facts: str | Path | None = None,
    seed: int = 20261010,
    current_year: int | None = None,
) -> dict:
    """Write the bundle to ``out`` and return the manifest.

    Raises ``CareerBuildError`` after the files are written when the roster-facts
    check fires, so the manifest records ``d5_roster_facts_recoverable``. Other
    disclosure failures are raised before the directory is replaced.
    """
    weight_path = Path(weights) if weights else None
    weight_map = _read_weights(weight_path) if weight_path else None
    population = load_population(config, weight_map, k=k)
    cap = datetime.now(timezone.utc).year
    year = current_year if current_year is not None else default_current_year(config, cap)
    weighted = weight_map is not None
    rng = random.Random(seed)

    reference, reference_suppressed, reference_censored, reference_empty = _reference(population, k, year, weighted)
    window, window_suppressed, window_censored, window_empty = _next_window(population, k, pct_min, year, weighted)
    profile, profile_suppressed, true_editions = _profile(population, k, pct_min)
    entry, entry_suppressed, clamped, entry_censored, entry_empty = _entry(population, k, weighted)
    transitions, transition_suppressed, transition_censored, true_transitions = _transitions(population, k, year)
    trend, trend_suppressed, trend_censored = _trend(population, k, pct_min, year, weighted, rng)

    tables = {
        "reference_position.csv": (REFERENCE_COLUMNS, reference, "career_age x generation x measure", "cv"),
        "next_window.csv": (NEXT_COLUMNS, window, "age_band x generation x mix_bucket x outcome", "cv"),
        "programme_profile.csv": (PROFILE_COLUMNS, profile, "programme", "roster"),
        "programme_entry.csv": (ENTRY_COLUMNS, entry, "programme x measure", "both"),
        "programme_transitions.csv": (TRANSITION_COLUMNS, transitions, "programme x programme", "roster"),
        "field_trend.csv": (TREND_COLUMNS, trend, "generation x measure", "cv"),
    }
    for name, (columns, rows, _grain, _layer) in tables.items():
        _scan_table(name, columns, rows, k)

    recoverable: list[dict] = []
    roster_state = "skipped"
    if roster_facts:
        roster_state = "passed"
        recoverable = _roster_check(Path(roster_facts), true_editions, true_transitions, k, year)
        if recoverable:
            roster_state = "failed"

    vocab = _vocab(population)
    manifest = {
        "bundle_format": 1,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ledger_version": _ledger_version(config),
        "k": k,
        "pct_min": pct_min,
        "seed": seed,
        "current_year": year,
        "territory": population.territory,
        "weights": hashlib.sha256(weight_path.read_bytes()).hexdigest() if weight_path else "none",
        "rule_ids": list(RULE_IDS),
        "roster_facts_check": roster_state,
        "counts": {
            "published": population.published_n,
            "cv_layer": population.cv_layer_n,
            "cv_no_first_year": population.cv_no_first_year,
            "unweighted_people": population.unweighted_people,
            "entry_before_first_year": clamped,
            "no_rows_in_window": {
                "reference_position": reference_empty,
                "next_window": window_empty,
                "programme_entry": entry_empty,
            },
            "censored": {
                "reference_position": reference_censored,
                "next_window": window_censored,
                "programme_profile": 0,
                "programme_entry": entry_censored,
                "programme_transitions": transition_censored,
                "field_trend": trend_censored,
            },
            "suppressed_cells": {
                "reference_position": reference_suppressed,
                "next_window": window_suppressed,
                "programme_profile": profile_suppressed,
                "programme_entry": entry_suppressed,
                "programme_transitions": transition_suppressed,
                "field_trend": trend_suppressed,
            },
        },
        "tables": {
            name: {"grain": grain, "layer": layer, "columns": columns, "rows": len(rows)}
            for name, (columns, rows, grain, layer) in tables.items()
        },
    }
    if recoverable:
        manifest["d5_roster_facts_recoverable"] = recoverable

    destination = Path(out)
    destination.mkdir(parents=True, exist_ok=True)
    for name, (columns, rows, _grain, _layer) in tables.items():
        _write_csv(destination / name, columns, rows)
    vocab_path = destination / "vocab.json"
    vocab_path.write_text(json.dumps(vocab, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _scan_json(vocab_path)
    if recoverable:
        raise CareerBuildError("D5: a suppressed cell equals a roster_facts count")
    return manifest


def _scan_json(path: Path) -> None:
    """Identifier scan for ``vocab.json``. A URL or a person id fails the build."""

    def walk(value: object) -> None:
        if isinstance(value, str):
            if _bad_value(value):
                raise CareerBuildError(f"D5: an aggregate value could hold an identifier in {path.name}")
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in (AGGREGATE_FORBIDDEN - {"name_ko", "name_en"}) or key == "ledger_id":
                    raise CareerBuildError("D5: an aggregate column could hold an identifier: " + key)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(json.loads(path.read_text(encoding="utf-8")))


def build_from_config(
    config_path: str | Path,
    out: str | Path,
    **kwargs: object,
) -> dict:
    """Load ``config_path`` and build. The CLI uses this."""
    return build(load(config_path), out, **kwargs)  # type: ignore[arg-type]
