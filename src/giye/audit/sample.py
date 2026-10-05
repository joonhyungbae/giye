# SPDX-License-Identifier: AGPL-3.0-only
"""Draw a judging sheet from a Giye ledger.

Three frames, one command each (``docs/EVALUATION.md``):

- ``cv`` — publishable activities whose ``origin`` is ``cv:<source_id>``,
  stratified by ``activity_type``, with the CV snapshot line the title sits on.
- ``people`` — one row per merge marker on a surviving artist (``merged <id>``
  with the ``merge_evidence`` and ``rule`` ``Ledger.merge`` writes), stratified
  by the coded rule (``E1``–``E4``, ``X1+E*``) or ``uncoded``.
- ``venues`` — each V7e, V8, and V9 line in section 7 of ``venue_audit.md``,
  with up to three activity rows for each spelling named on that line.

``--n`` is the size of the whole sheet, not a quota per stratum. Seats are
Hamilton's largest-remainder method on integer arithmetic, strata ordered by
name when remainders tie. Inside a stratum, items are sorted by ``item_id``
and a partial Fisher–Yates draw uses ``random.Random(seed)`` only (not
``random.sample``, whose algorithm changed in Python 3.11). A stratum that
contributes all of its rows, or none, does not advance the generator, so a
census does not depend on draw order. The same seed and the same ledger
rewrite the same sheet.
"""

from __future__ import annotations

import random
import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from giye.audit.sheet import columns_for, write_sheet
from giye.config import Config, load
from giye.extract.paths import resolve_stored
from giye.ledger.io import read_csv
from giye.ledger.schemas import TABLES
from giye.normalize.rules import norm_text

# First token of a same-person rule, as ``rule_of`` stores it. Anything else is uncoded.
_CODED_RULE = re.compile(r"^(?:X1\+)?E[1-4]$")
_MERGED = re.compile(r"^merged\s+(\S+)$")
_EVIDENCE = re.compile(r"^merge_evidence=(.*)$", re.DOTALL)
_RULE = re.compile(r"^rule=(.*)$", re.DOTALL)
_AUDIT_LINE = re.compile(
    r"^- (?P<rule>V7e|V8|V9) · `(?P<left>.*)`\((?P<nleft>\d+)\) ← `(?P<right>.*)`\((?P<nright>\d+)\)\s*$"
)
_BACKUP = re.compile(r"^(?P<table>.+)-(?P<day>\d{8})-before-merge(?:-(?P<n>\d+))?\.csv$")
# A venue string may continue with a place after one of these. A hall such as
# "… 전시실" does not, so it stays an example of its own spelling.
_VENUE_SPLIT = {",", "/", "|", "·", ";"}
_EXAMPLE_CAP = 3


def sample_sheet(config: Config, kind: str, n: int, seed: int, out: Path) -> list[dict[str, str]]:
    """Write ``out`` and return the rows. ``label`` and ``note`` are empty."""
    if n < 1:
        raise ValueError("--n must be at least 1")
    if kind == "cv":
        rows = _cv_rows(config, n, seed)
    elif kind == "people":
        rows = _people_rows(config, n, seed)
    elif kind == "venues":
        rows = _venue_rows(config, n, seed)
    else:
        raise ValueError(f"kind must be cv, people, or venues (got {kind})")
    write_sheet(out, columns_for(kind), rows)
    return rows


def load_config(path: str | Path) -> Config:
    return load(path)


def _ledger_table(config: Config, table: str) -> list[dict[str, str]]:
    """Rows of one ledger CSV, without the ledger lock.

    ``Ledger.read`` takes an exclusive lock and holds it until the process
    exits. A sample only reads a ledger that another Giye process may already
    have open (a pipeline run, or the test process that just built the demo).
    Waiting on that lock does not end. The sheet is derived and is not written
    back into the ledger.
    """
    filename, _columns = TABLES[table]
    return read_csv(config.ledger / filename)


def stratum_for(rule: str) -> str:
    """``E1``–``E4`` and ``X1+E*`` stay their own strata. Every other token is ``uncoded``."""
    token = (rule or "").strip()
    if _CODED_RULE.fullmatch(token):
        return token
    return "uncoded"


def parse_markers(note: str) -> list[tuple[str, str, str]]:
    """``(dropped ledger id, evidence, rule)`` in the order ``Ledger.merge`` appends them.

    Several dropped ids can share one evidence string: the writer emits every
    ``merged <id>`` and then a single ``merge_evidence`` and ``rule``. An
    ``identity=`` pin between those parts is ignored.
    """
    parts = [part.strip() for part in (note or "").split(";") if part.strip()]
    dropped: list[str] = []
    evidence = ""
    rule = ""
    found: list[tuple[str, str, str]] = []

    def flush() -> None:
        nonlocal dropped, evidence, rule
        for item in dropped:
            found.append((item, evidence, rule))
        dropped, evidence, rule = [], "", ""

    for part in parts:
        merged = _MERGED.match(part)
        if merged:
            if evidence or rule:
                flush()
            dropped.append(merged.group(1))
            continue
        ev = _EVIDENCE.match(part)
        if ev and dropped:
            evidence = ev.group(1).strip()
            continue
        coded = _RULE.match(part)
        if coded and dropped:
            rule = coded.group(1).strip()
            flush()
    flush()
    return found


def allocate(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Hamilton quotas. ``n`` at or above the population returns every item.

    The base is ``n * size // total``. Leftover seats go to the largest
    remainders (``n * size % total``), then the larger stratum, then the
    stratum name. No floating point, so the seats do not depend on rounding.
    """
    total = sum(sizes.values())
    if total <= 0:
        return {}
    if n >= total:
        return dict(sizes)
    names = sorted(sizes)
    base = {name: (n * sizes[name]) // total for name in names}
    left = n - sum(base.values())
    order = sorted(names, key=lambda name: (-((n * sizes[name]) % total), -sizes[name], name))
    for name in order:
        if left <= 0:
            break
        if base[name] < sizes[name]:
            base[name] += 1
            left -= 1
    return base


def _disambiguate(population: list[dict[str, str]]) -> None:
    """A repeated ``item_id`` gets `` #2``, `` #3``, … in population order."""
    counts: dict[str, int] = defaultdict(int)
    for row in population:
        counts[row["item_id"]] += 1
    seen: dict[str, int] = defaultdict(int)
    for row in population:
        base = row["item_id"]
        seen[base] += 1
        if counts[base] > 1:
            row["item_id"] = f"{base} #{seen[base]}"


def draw(rng: random.Random, items: Sequence[dict[str, str]], k: int) -> list[dict[str, str]]:
    """``k`` items without replacement. ``items`` must already be in a stable order.

    Taking all of them, or none, does not read ``rng``.
    """
    if k <= 0:
        return []
    if k >= len(items):
        return list(items)
    pool = list(items)
    picked: list[dict[str, str]] = []
    for _ in range(k):
        index = rng.randrange(len(pool))
        picked.append(pool.pop(index))
    picked.sort(key=lambda row: row["item_id"])
    return picked


def _select(population: list[dict[str, str]], n: int, seed: int) -> list[dict[str, str]]:
    _disambiguate(population)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in population:
        grouped[row["stratum"]].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row["item_id"])
    seats = allocate({name: len(rows) for name, rows in grouped.items()}, n)
    rng = random.Random(seed)
    chosen: list[dict[str, str]] = []
    for name in sorted(grouped):
        chosen.extend(draw(rng, grouped[name], seats.get(name, 0)))
    for row in chosen:
        row["seed"] = str(seed)
        row["label"] = ""
        row["note"] = ""
    chosen.sort(key=lambda row: (row["stratum"], row["item_id"]))
    return chosen


def _cv_rows(config: Config, n: int, seed: int) -> list[dict[str, str]]:
    artists = {row["ledger_id"]: row for row in _ledger_table(config, "artists")}
    texts = _cv_texts(config, _ledger_table(config, "cv_sources"))
    population: list[dict[str, str]] = []
    for activity in _ledger_table(config, "activities"):
        if activity.get("publishable") != "yes":
            continue
        origin = activity.get("origin") or ""
        if not origin.startswith("cv:"):
            continue
        source_id = origin.split(":", 1)[1]
        person = artists.get(activity.get("ledger_id") or "", {})
        activity_type = (activity.get("activity_type") or "").strip() or "other"
        title = activity.get("title") or ""
        year = activity.get("year") or ""
        population.append(
            {
                "item_id": activity["activity_id"],
                "kind": "cv",
                "stratum": activity_type,
                "activity_id": activity["activity_id"],
                "ledger_id": activity.get("ledger_id") or "",
                "gy_id": person.get("gy_id") or "",
                "name_ko": person.get("name_ko") or "",
                "name_en": person.get("name_en") or "",
                "activity_type": activity_type,
                "title": title,
                "venue": activity.get("venue") or "",
                "year": year,
                "role": activity.get("role") or "",
                "source_id": source_id,
                "source_url": activity.get("source_url") or "",
                "excerpt": _excerpt(texts.get(source_id, ""), title, year),
            }
        )
    return _select(population, n, seed)


def _cv_texts(config: Config, sources: list[dict[str, str]]) -> dict[str, str]:
    """source id → snapshot text. A missing file is an empty excerpt, not a failed sample."""
    found: dict[str, str] = {}
    for source in sources:
        source_id = source.get("source_id") or ""
        stored = source.get("snapshot_path") or ""
        if not source_id or not stored:
            continue
        path = resolve_stored(config, stored + ".txt")
        if path.is_file():
            found[source_id] = path.read_text(encoding="utf-8")
    return found


def _excerpt(text: str, title: str, year: str) -> str:
    """The CV line that contains the title, plus the neighbouring lines.

    The year has to be on that line when any such line exists, so a one-word
    title does not attach to an earlier mention. No hit leaves the excerpt
    empty: the coder still sees the extracted fields and can mark cannot tell.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    raw = " ".join((title or "").split())
    bare = re.sub(r"[〈〉《》«»<>「」『』\"'`]", "", raw).strip()
    needles = []
    for needle in (raw, bare):
        if len(needle) >= 1 and needle not in needles:
            needles.append(needle)
    if not needles:
        return ""
    year_text = str(year or "").strip()

    def hits(require_year: bool) -> list[int]:
        found = []
        for index, line in enumerate(lines):
            if require_year and year_text and year_text not in line:
                continue
            if any(needle in line for needle in needles):
                found.append(index)
        return found

    found = hits(True) if year_text else []
    if not found:
        found = hits(False)
    if not found:
        return ""
    index = found[0]
    window = lines[max(0, index - 1) : index + 2]
    excerpt = "\n".join(window)
    if len(excerpt) > 1200:
        return excerpt[:1200]
    return excerpt


def _people_rows(config: Config, n: int, seed: int) -> list[dict[str, str]]:
    artists = {row["ledger_id"]: row for row in _ledger_table(config, "artists")}
    memberships = _rosters(_ledger_table(config, "frame_membership"))
    backups = _backup_index(config.work / "backups")
    population: list[dict[str, str]] = []
    for kept_id, artist in artists.items():
        for dropped_id, evidence, rule in parse_markers(artist.get("reviewer_note") or ""):
            stratum = stratum_for(rule)
            kept_row, dropped_row, kept_rosters, dropped_rosters = _pair_records(
                kept_id, dropped_id, artist, memberships, backups
            )
            population.append(
                {
                    "item_id": f"{kept_id}/{dropped_id}",
                    "kind": "people",
                    "stratum": stratum,
                    "kept_ledger_id": kept_id,
                    "kept_gy_id": kept_row.get("gy_id") or "",
                    "kept_name_ko": kept_row.get("name_ko") or "",
                    "kept_name_en": kept_row.get("name_en") or "",
                    "kept_rosters": kept_rosters,
                    "dropped_ledger_id": dropped_id,
                    "dropped_gy_id": dropped_row.get("gy_id") or "",
                    "dropped_name_ko": dropped_row.get("name_ko") or "",
                    "dropped_name_en": dropped_row.get("name_en") or "",
                    "dropped_rosters": dropped_rosters,
                    "evidence": evidence,
                    "rule": rule,
                }
            )
    return _select(population, n, seed)


def _rosters(rows: list[dict[str, str]]) -> dict[str, str]:
    frames: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        code = (row.get("frame_code") or "").strip()
        if code:
            frames[row.get("ledger_id") or ""].add(code)
    return {ledger_id: " | ".join(sorted(codes)) for ledger_id, codes in frames.items()}


def _backup_index(directory: Path) -> dict[str, list[tuple[tuple[str, int], list[dict[str, str]]]]]:
    """Table name → backups in write order. The key is ``(UTC day, sequence)``."""
    grouped: dict[str, list[tuple[tuple[str, int], list[dict[str, str]]]]] = defaultdict(list)
    if not directory.is_dir():
        return grouped
    for path in directory.iterdir():
        match = _BACKUP.match(path.name)
        if not match or not path.is_file():
            continue
        key = (match.group("day"), int(match.group("n") or "1"))
        grouped[match.group("table")].append((key, read_csv(path)))
    for rows in grouped.values():
        rows.sort(key=lambda item: item[0])
    return grouped


def _pair_records(
    kept_id: str,
    dropped_id: str,
    survivor: dict[str, str],
    memberships: dict[str, str],
    backups: dict[str, list[tuple[tuple[str, int], list[dict[str, str]]]]],
) -> tuple[dict[str, str], dict[str, str], str, str]:
    """Names and rosters from the latest pre-merge backup that still has ``dropped_id``.

    ``Ledger.merge`` removes the dropped row, so the live table only has the
    survivor. The backup taken before that write still has both rows and both
    roster lists. When no backup remains, the dropped name and rosters are
    empty and the kept side is the survivor as stored now (rosters already
    combined).
    """
    artist_backups = backups.get("artists", [])
    chosen_key: tuple[str, int] | None = None
    kept_row = survivor
    dropped_row: dict[str, str] = {}
    for key, rows in artist_backups:
        by_id = {row.get("ledger_id") or "": row for row in rows}
        if dropped_id in by_id:
            chosen_key = key
            dropped_row = by_id[dropped_id]
            kept_row = by_id.get(kept_id, survivor)
    if chosen_key is None:
        return survivor, {}, memberships.get(kept_id, ""), ""
    found = _rosters_at(backups.get("frame_membership", []), chosen_key, kept_id, dropped_id)
    if found is None:
        return kept_row, dropped_row, memberships.get(kept_id, ""), ""
    return kept_row, dropped_row, found[0], found[1]


def _rosters_at(
    backups: list[tuple[tuple[str, int], list[dict[str, str]]]],
    key: tuple[str, int],
    kept_id: str,
    dropped_id: str,
) -> tuple[str, str] | None:
    """Rosters in the membership backup with the same key, or None when that file is gone."""
    rows: list[dict[str, str]] | None = None
    for backup_key, table in backups:
        if backup_key == key:
            rows = table
    if rows is None:
        return None
    rosters = _rosters(rows)
    return rosters.get(kept_id, ""), rosters.get(dropped_id, "")


def _venue_rows(config: Config, n: int, seed: int) -> list[dict[str, str]]:
    audit_path = config.processed / "venue_audit.md"
    if not audit_path.is_file():
        raise FileNotFoundError(f"no venue audit at {audit_path} (run normalize first)")
    merges = parse_audit_merges(audit_path.read_text(encoding="utf-8"))
    activities = _ledger_table(config, "activities")
    population: list[dict[str, str]] = []
    bases: list[str] = []
    drafts: list[dict[str, str]] = []
    for merge in merges:
        base = f"{merge['rule']} :: {merge['left']} <- {merge['right']}"
        bases.append(base)
        drafts.append(
            {
                "kind": "venues",
                "stratum": merge["rule"],
                "rule": merge["rule"],
                "kept_spelling": merge["left"],
                "kept_row_count": merge["n_left"],
                "joined_spelling": merge["right"],
                "joined_row_count": merge["n_right"],
                "kept_examples": _examples(activities, merge["left"]),
                "joined_examples": _examples(activities, merge["right"]),
            }
        )
    counts: dict[str, int] = defaultdict(int)
    for base in bases:
        counts[base] += 1
    seen: dict[str, int] = defaultdict(int)
    for base, row in zip(bases, drafts):
        seen[base] += 1
        row["item_id"] = base if counts[base] == 1 else f"{base} #{seen[base]}"
        population.append(row)
    return _select(population, n, seed)


def parse_audit_merges(text: str) -> list[dict[str, str]]:
    """V7e, V8, and V9 joins from section 7. Other rules in that section are not cases."""
    marker = "## 7. Merges"
    if marker not in text:
        raise ValueError("venue audit has no '## 7. Merges' section")
    section = text.split(marker, 1)[1]
    following = re.search(r"\n## ", section)
    if following:
        section = section[: following.start()]
    merges = []
    for line in section.splitlines():
        match = _AUDIT_LINE.match(line.strip())
        if not match:
            continue
        merges.append(
            {
                "rule": match.group("rule"),
                "left": match.group("left"),
                "n_left": match.group("nleft"),
                "right": match.group("right"),
                "n_right": match.group("nright"),
            }
        )
    return merges


def _spelling_matches(venue: str, spelling: str) -> bool:
    left = norm_text(venue)
    right = norm_text(spelling)
    if not left or not right or right == "(blank)":
        return False
    if left == right:
        return True
    return left.startswith(right) and left[len(right) : len(right) + 1] in _VENUE_SPLIT


def _examples(activities: list[dict[str, str]], spelling: str) -> str:
    """Up to three rows that use ``spelling``, earliest year first, then activity id."""
    matched = [row for row in activities if _spelling_matches(row.get("venue") or "", spelling)]
    matched.sort(key=lambda row: ((row.get("year") or "") == "", row.get("year") or "", row.get("activity_id") or ""))
    parts = []
    for row in matched[:_EXAMPLE_CAP]:
        title = " ".join((row.get("title") or "").split())
        venue = " ".join((row.get("venue") or "").split())
        parts.append(
            f"{row.get('year') or ''} | {title} | {venue} | {row.get('ledger_id') or ''} | {row.get('activity_id') or ''}"
        )
    return " || ".join(parts)
