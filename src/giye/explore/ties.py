# SPDX-License-Identifier: AGPL-3.0-only
"""Co-presence ties: two people at one institution in one year.

A tie is an unordered pair of person ids, counted once however many
institution-years the two share. Institution means an activity row whose
resolved ``venue_kind`` is ``institution`` and whose ``venue_id`` is set: a
funder is not a place where people meet. See docs/RULES.md (V7–V9, P1, E2)
and docs/EXPLORE.md.

Two definitions:

CV listing (``cv_listing_ties``)
    Rows are publishable (``publishable=yes``), have a numeric year (``isdigit``),
    are not flagged ``year_from_title`` (P1, one person's rows at a time), and
    come from a CV (``origin`` starts with ``cv:``). The population is everyone
    with at least one such row (``cv_population``). This is the paper's
    "CV-listing co-presence".

Roster independent (``roster_independent_ties``)
    Rows come from a CV and have a four-digit year. ``publishable`` and the
    year flags are not read, as in the flock evaluation. A row is dropped when
    it restates one of the same person's roster editions: the edition code ends
    ``-YYYY``, that year equals the row's year, and the edition's event pattern
    (rule E2's table, from the field file and ``[resolve.event_patterns]``)
    matches the normalised title and venue. E2 itself allows one year either
    way; this filter does not, because the row is the programme the person is
    already on, not a neighbouring year. An undated edition, or a code with no
    event pattern, drops nothing. This is the flock evaluation's primary
    outcome and the ties ``giye explore --assignment`` scores against.

Rule layers (``resolve_layers``, ``layer_report``): the institution resolver
runs four times, cumulatively: ``base`` (V7–V9 off), ``V7`` (spelling V7a–d
and the Latin word-bag merge V7e), ``V7+V8`` (subordinate spaces) and
``V7+V8+V9`` (the Hangul–Latin bag merge, the default when every name rule is
on). ``attribute_merges`` replays the full run one merge at
a time. V7a–d rewrite a key before any join is recorded, so their share is the
difference between the post-spelling components and the base run. V7e, V8 and
V9 are then applied in the recorded order; a merge adds the person pairs that
first become ties when its two components join. The replay must end at the
same counts as the layer runs; ``layer_report`` raises when it does not.

Nothing here edits the ledger or writes ``venues.csv``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from giye.config import Config
from giye.normalize.language import LanguageModule
from giye.normalize.rules import norm_text, year_flags
from giye.normalize.venues import BuildResult, UnionFind, build
from giye.resolve.evidence import event_pattern

Pair = tuple[str, str]
Annotations = Mapping[str, Mapping[str, str]]

# Cumulative layers. None means every name rule is on (the default).
LAYERS: tuple[tuple[str, frozenset[str] | None], ...] = (
    ("base", frozenset()),
    ("V7", frozenset({"V7"})),
    ("V7+V8", frozenset({"V7", "V8"})),
    # Not None: None would also apply V4n, V7f and V9u. This ladder is the
    # V7–V9 key merges the replay can reproduce. V4n remaps rows, so a layer
    # that included it would not end where the replay ends.
    ("V7+V8+V9", frozenset({"V7", "V8", "V9"})),
)
# Rules the replay applies one merge at a time, in this order. V7a–d come before them.
MERGE_RULES = ("V7e", "V8", "V9")
# The layer whose tie count each replayed rule must end on.
RULE_LAYER = {"V7e": "V7", "V8": "V7+V8", "V9": "V7+V8+V9"}

_YEAR_SUFFIX = re.compile(r"-(\d{4})$")
_FOUR_DIGITS = re.compile(r"\d{4}")


def is_cv(row: Mapping[str, str]) -> bool:
    """True when the row was taken from a CV (``origin`` starts with ``cv:``)."""
    return (row.get("origin") or "").startswith("cv:")


def usable_rows(activities: Sequence[Mapping[str, str]]) -> list[Mapping[str, str]]:
    """Publishable rows with a numeric year that is not the period a title names (P1 Y2)."""
    by_person: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in activities:
        by_person[row["ledger_id"]].append(row)
    flags: dict[str, list[str]] = {}
    for rows in by_person.values():
        flags.update(year_flags(rows))  # type: ignore[arg-type]
    return [
        row
        for row in activities
        if row.get("publishable") == "yes"
        and str(row.get("year", "")).isdigit()
        and "year_from_title" not in flags.get(row["activity_id"], [])
    ]


def cv_population(activities: Sequence[Mapping[str, str]]) -> set[str]:
    """People with a usable CV row: the population of the CV-listing definition."""
    return {row["ledger_id"] for row in usable_rows(activities) if is_cv(row)}


def pairs_of(groups: Mapping[Any, Iterable[str]]) -> set[Pair]:
    """Unique unordered pairs of people who share one group. A group of one adds nothing."""
    found: set[Pair] = set()
    for members in groups.values():
        ordered = sorted(set(members))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                found.add((left, right))
    return found


def _venue(row: Mapping[str, str], annotations: Annotations | None) -> tuple[str, str]:
    """(venue_kind, venue_id): from the resolver's annotations, else from a processed row."""
    if annotations is None:
        return row.get("venue_kind") or "", row.get("venue_id") or ""
    found = annotations.get(row["activity_id"]) or {}
    return found.get("venue_kind") or "", found.get("venue_id") or ""


def institution_groups(
    rows: Iterable[Mapping[str, str]],
    annotations: Annotations | None,
    people: set[str] | None = None,
) -> dict[tuple[str, int], set[str]]:
    """(institution entity, year) → people. Rows must already have a numeric year."""
    groups: dict[tuple[str, int], set[str]] = defaultdict(set)
    for row in rows:
        if people is not None and row["ledger_id"] not in people:
            continue
        kind, venue_id = _venue(row, annotations)
        if kind != "institution" or not venue_id:
            continue
        groups[(venue_id, int(row["year"]))].add(row["ledger_id"])
    return groups


def cv_listing_ties(activities: Sequence[Mapping[str, str]], annotations: Annotations | None) -> set[Pair]:
    """CV-listing co-presence among ``cv_population``.

    Returns unordered pairs. Rows are publishable, have a numeric year, are not
    flagged ``year_from_title`` (P1), and come from a CV.
    """
    rows = [row for row in usable_rows(activities) if is_cv(row)]
    return pairs_of(institution_groups(rows, annotations))


def roster_editions(
    memberships: Iterable[Mapping[str, str]],
    patterns: Mapping[str, str],
) -> dict[str, list[tuple[int, re.Pattern[str]]]]:
    """Person → (edition year, compiled event pattern) for each dated edition E2 can name."""
    compiled: dict[str, re.Pattern[str]] = {}
    owned: dict[str, list[tuple[int, re.Pattern[str]]]] = defaultdict(list)
    for row in memberships:
        code = row.get("frame_code") or ""
        year = _YEAR_SUFFIX.search(code)
        text = event_pattern(code, patterns)
        if not year or not text:
            continue
        pattern = compiled.get(text)
        if pattern is None:
            pattern = compiled[text] = re.compile(text, re.IGNORECASE)
        owned[row["ledger_id"]].append((int(year.group(1)), pattern))
    return owned


def restates_roster(year: int, text: str, owned: Sequence[tuple[int, re.Pattern[str]]]) -> bool:
    """True when a CV row in ``year`` names one of the person's own editions of that year."""
    return any(edition_year == year and pattern.search(text) for edition_year, pattern in owned)


def _row_text(row: Mapping[str, str]) -> str:
    """Normalised title and venue. A processed row carries them; a ledger row is normalised here."""
    if "title_norm" in row or "venue_norm" in row:
        return f"{row.get('title_norm') or ''} {row.get('venue_norm') or ''}"
    return f"{norm_text(row.get('title'))} {norm_text(row.get('venue'))}"


def roster_independent_rows(
    activities: Iterable[Mapping[str, str]],
    memberships: Iterable[Mapping[str, str]],
    patterns: Mapping[str, str],
) -> tuple[list[Mapping[str, str]], int]:
    """CV rows with a four-digit year that do not restate the person's own roster edition.

    Returns the kept rows and the number of dropped rows. The count is of every
    dropped CV row, whatever its venue: the venue is resolved later.
    """
    owned = roster_editions(memberships, patterns)
    kept: list[Mapping[str, str]] = []
    dropped = 0
    for row in activities:
        if not is_cv(row):
            continue
        year_text = (row.get("year") or "").strip()
        if not _FOUR_DIGITS.fullmatch(year_text):
            continue
        if restates_roster(int(year_text), _row_text(row), owned.get(row["ledger_id"], ())):
            dropped += 1
            continue
        kept.append(row)
    return kept, dropped


def roster_independent_ties(
    activities: Sequence[Mapping[str, str]],
    annotations: Annotations | None,
    memberships: Iterable[Mapping[str, str]],
    patterns: Mapping[str, str],
    people: set[str] | None = None,
) -> set[Pair]:
    """Roster-independent co-presence. Returns unordered pairs.

    A CV row that restates the person's own roster edition in that edition's
    year is already dropped. ``people`` limits both ends of a tie. This is the
    outcome ``giye explore --assignment`` scores against.
    """
    rows, _dropped = roster_independent_rows(activities, memberships, patterns)
    return pairs_of(institution_groups(rows, annotations, people))


def resolve_layers(
    activities: Sequence[Mapping[str, str]],
    lang: LanguageModule | None = None,
    layers: Sequence[tuple[str, frozenset[str] | None]] = LAYERS,
) -> dict[str, BuildResult]:
    """Run the institution resolver once per layer, without writing anything."""
    return {
        name: build(list(activities), name_rules=rules, write=False, lang=lang)  # type: ignore[arg-type]
        for name, rules in layers
    }


def entity_summary(result: BuildResult) -> dict[str, Any]:
    """Entities and entities shared by two or more people, funders included, then by kind."""

    def of_kind(kind: str) -> dict[str, int]:
        rows = [row for row in result.venues if row["kind"] == kind]
        return {
            "entities": len(rows),
            "shared_by_2plus_people": sum(int(row["n_artists"]) >= 2 for row in rows),
        }

    return {
        "entities": result.stats["entities"],
        "shared_by_2plus_people": result.stats["shared_entities"],
        "by_kind": {"institution": of_kind("institution"), "funder": of_kind("funder")},
        "name_rule_merges": result.stats["name_rule_merges"],
    }


def observations(result: BuildResult, rows: Iterable[Mapping[str, str]]) -> list[tuple[str, int, str]]:
    """(person, year, institution key) for each institution row, after V7a–d, before any merge.

    The key is the row's first institution fragment, as the resolver chose it.
    """
    found: list[tuple[str, int, str]] = []
    keys = result.institution_key_by_activity
    for row in rows:
        if result.annotations[row["activity_id"]]["venue_kind"] != "institution":
            continue
        key = keys.get(row["activity_id"]) or ""
        if key:
            found.append((row["ledger_id"], int(row["year"]), key))
    return found


def attribute_merges(
    obs: Sequence[tuple[str, int, str]],
    root_before: Mapping[str, str],
    merges: Sequence[tuple[str, str, str]],
    prior: set[Pair] | frozenset[Pair] = frozenset(),
) -> dict[str, Any]:
    """Replay V7e, V8 and V9 on the post-spelling components, one merge at a time.

    ``prior`` holds ties that do not come from these merges (for a network that
    also counts roster editions, those pairs). A merge "added ties" when at least
    one person pair becomes a tie at the moment its two components join.
    """
    union_find = UnionFind(set(root_before) | set(root_before.values()))
    union_find.parent = dict(root_before)
    for root in set(union_find.parent.values()):
        union_find.parent.setdefault(root, root)

    people: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for person, year, key in obs:
        people[union_find.find(key)][year].add(person)

    ties = set(prior)
    for by_year in people.values():
        ties |= pairs_of(by_year)
    at_start = len(ties)

    per_rule = {rule: {"merges": 0, "merges_that_added_ties": 0, "ties_added": 0} for rule in MERGE_RULES}
    for rule, _kept, _joined in merges:
        if rule not in per_rule:
            continue
        per_rule[rule]["merges"] += 1
    for rule, kept, joined in merges:
        if rule not in per_rule:
            continue
        left_root, right_root = union_find.find(kept), union_find.find(joined)
        if left_root == right_root:
            continue
        # One person can sit on both components (two spellings in one year). The
        # cross product then offers the same pair twice; it is counted once.
        new_pairs: set[Pair] = set()
        left_years = people.get(left_root, {})
        right_years = people.get(right_root, {})
        for year in set(left_years) | set(right_years):
            left_members = left_years.get(year)
            right_members = right_years.get(year)
            if not left_members or not right_members:
                continue
            for person in left_members:
                for other in right_members:
                    if person == other:
                        continue
                    pair = (person, other) if person < other else (other, person)
                    if pair not in ties:
                        new_pairs.add(pair)
        if new_pairs:
            per_rule[rule]["merges_that_added_ties"] += 1
            per_rule[rule]["ties_added"] += len(new_pairs)
            ties |= new_pairs
        union_find.union(kept, joined)
        destination = union_find.find(kept)
        source = right_root if destination == left_root else left_root
        moved = people.pop(source, {})
        target = people[destination]
        for year, members in moved.items():
            target[year].update(members)

    running = at_start
    ends: dict[str, int] = {}
    for rule in MERGE_RULES:
        running += per_rule[rule]["ties_added"]
        ends[rule] = running
    return {
        "ties_after_spelling_before_merges": at_start - len(prior),
        "ties_including_prior": at_start,
        "per_rule": per_rule,
        "ties_after_rule_including_prior": ends,
        "ties_at_end_including_prior": len(ties),
    }


def layer_report(
    activities: Sequence[Mapping[str, str]],
    builds: Mapping[str, BuildResult],
    memberships: Iterable[Mapping[str, str]] | None = None,
    patterns: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Entities and CV-listing ties per layer, the per-rule attribution, and the replay check.

    ``builds`` is ``resolve_layers`` output (all four layers). With
    ``memberships`` and ``patterns`` the roster-independent count per layer is
    added. Raises ``RuntimeError`` when the replay does not end on the layer counts.
    """
    missing = [name for name, _rules in LAYERS if name not in builds]
    if missing:
        raise ValueError(f"layer_report needs every layer; missing {missing}")
    cv_rows = [row for row in usable_rows(activities) if is_cv(row)]
    indep_rows: list[Mapping[str, str]] | None = None
    if memberships is not None and patterns is not None:
        indep_rows, _dropped = roster_independent_rows(activities, memberships, patterns)
    entities: dict[str, Any] = {}
    ties: dict[str, dict[str, int]] = {}
    base_pairs: set[Pair] = set()
    for name, _rules in LAYERS:
        result = builds[name]
        entities[name] = entity_summary(result)
        pairs = pairs_of(institution_groups(cv_rows, result.annotations))
        if name == "base":
            base_pairs = pairs
        ties[name] = {"cv_listing": len(pairs)}
        if indep_rows is not None:
            ties[name]["roster_independent"] = len(pairs_of(institution_groups(indep_rows, result.annotations)))

    full = builds["V7+V8+V9"]
    replay = attribute_merges(observations(full, cv_rows), full.root_before_name_rules, full.name_rule_merges)
    mismatches = [
        f"after {rule}: replay {replay['ties_after_rule_including_prior'][rule]} != layer {ties[layer]['cv_listing']}"
        for rule, layer in RULE_LAYER.items()
        if replay["ties_after_rule_including_prior"][rule] != ties[layer]["cv_listing"]
    ]
    if mismatches:
        raise RuntimeError("merge replay does not match the layer runs: " + "; ".join(mismatches))
    report: dict[str, Any] = {
        "n_people": len({row["ledger_id"] for row in cv_rows}),
        "entities": entities,
        "ties": ties,
        "attribution": {
            "V7a-d": replay["ties_including_prior"] - len(base_pairs),
            **{rule: dict(block) for rule, block in replay["per_rule"].items()},
        },
    }
    if indep_rows is not None:
        # The same replay on the evaluation outcome, so its rise can be attributed rule by rule too.
        indep = attribute_merges(observations(full, indep_rows), full.root_before_name_rules, full.name_rule_merges)
        wrong = [
            rule for rule, layer in RULE_LAYER.items()
            if indep["ties_after_rule_including_prior"][rule] != ties[layer]["roster_independent"]
        ]
        if wrong:
            raise RuntimeError(f"roster-independent replay does not match the layer runs after {wrong}")
        report["n_people_roster_independent"] = len({row["ledger_id"] for row in indep_rows})
        report["attribution_roster_independent"] = {
            "V7a-d": indep["ties_including_prior"] - ties["base"]["roster_independent"],
            **{rule: dict(block) for rule, block in indep["per_rule"].items()},
        }
    return report


KINDS = ("roster-independent", "cv-listing")


def _inputs(config: Config) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, str]]:
    """Ledger activities, memberships, and the E2 event-pattern table.

    A person hidden by request is left out (a hide request stops processing).
    """
    from giye.ledger.io import read_csv
    from giye.ledger.ledger import without_hidden
    from giye.resolve.evidence import pattern_table

    artists = read_csv(config.ledger / "artists.csv")
    activities = without_hidden(read_csv(config.ledger / "activities.csv"), artists)
    memberships = without_hidden(read_csv(config.ledger / "frame_membership.csv"), artists)
    patterns = pattern_table(config.field_config.event_patterns, config.event_patterns)
    return activities, memberships, patterns


def ties_for_config(config: Config, kind: str = "roster-independent") -> set[Pair]:
    """Ties of one definition for an archive, with the configured language and name rules."""
    from giye.normalize.language import language_for
    from giye.normalize.service import parse_name_rules

    if kind not in KINDS:
        raise ValueError(f"unknown tie kind {kind!r}; expected one of {KINDS}")
    activities, memberships, patterns = _inputs(config)
    rules = parse_name_rules(config.venue_name_rules or None)
    result = build(activities, name_rules=rules, write=False, lang=language_for(config))
    if kind == "cv-listing":
        return cv_listing_ties(activities, result.annotations)
    return roster_independent_ties(activities, result.annotations, memberships, patterns)


def layers_for_config(config: Config) -> dict[str, Any]:
    """``layer_report`` for an archive: four resolver runs, both definitions, the attribution."""
    from giye.normalize.language import language_for

    activities, memberships, patterns = _inputs(config)
    builds = resolve_layers(activities, language_for(config))
    return layer_report(activities, builds, memberships, patterns)


def format_layers(report: Mapping[str, Any]) -> str:
    """Tab-separated per-layer counts, then one line per rule of the attribution."""
    lines = [f"people\t{report['n_people']}", "layer\tentities\tshared\tcv_listing\troster_independent"]
    for name, _rules in LAYERS:
        entity = report["entities"][name]
        tie = report["ties"][name]
        lines.append(
            f"{name}\t{entity['entities']}\t{entity['shared_by_2plus_people']}\t"
            f"{tie['cv_listing']}\t{tie.get('roster_independent', '-')}"
        )
    attribution = report["attribution"]
    lines.append(f"added\tV7a-d\t{attribution['V7a-d']}")
    for rule in MERGE_RULES:
        block = attribution[rule]
        lines.append(
            f"added\t{rule}\t{block['ties_added']}\t"
            f"merges={block['merges']}\tmerges_that_added_ties={block['merges_that_added_ties']}"
        )
    indep = report.get("attribution_roster_independent")
    if indep:
        lines.append(f"people_roster_independent\t{report['n_people_roster_independent']}")
        lines.append(f"added_roster_independent\tV7a-d\t{indep['V7a-d']}")
        for rule in MERGE_RULES:
            block = indep[rule]
            lines.append(
                f"added_roster_independent\t{rule}\t{block['ties_added']}\t"
                f"merges={block['merges']}\tmerges_that_added_ties={block['merges_that_added_ties']}"
            )
    return "\n".join(lines)
