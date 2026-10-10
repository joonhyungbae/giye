# SPDX-License-Identifier: AGPL-3.0-only
"""Build the dataset release for a data paper.

What: writes ``<data>/release/<version>/`` as two deposits. ``open/`` is the
public record (roster facts without names, admitted programmes,
disclosure-controlled aggregates, institutions of at least ``k`` people, the
datasheet, codebook, data-use agreement, citation, licence and manifest).
``restricted/`` is the access-on-request record (pseudonymised people,
activity-channel rows, and ``roster_names.csv``). ``zenodo/`` holds deposit
metadata and is not part of either record. ``open/stats.json`` is the count
file a paper cites. The pseudonym map is written to
``<data>/work/release/<version>_key.csv``, never inside the release directory.

Why: the open tier is what can be deposited. Person-level CV rows leave the
machine only under a data-use agreement. Who is included is the same set the
site publishes (``published_ids`` and ``giye.export.privacy``). Aggregates
follow disclosure rules D1–D5 in docs/CAREER.md.

How to run (the venv interpreter):
  .venv/bin/giye release --config deploy/giye.production.toml --version 2026-10-10

The build raises ``ReleaseError`` (exit 2) when a cell under ``k`` would carry
a value, a suppressed cell is recoverable from another published aggregate or
from a published total, a restricted file holds a ledger name, ``gy_id``, URL
or title, an open file holds a CV row, a person hidden by request appears
anywhere in the release, or an open file carries a published person's name
token (REL-URL).
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import math
import random
import re
import secrets
import shutil
import subprocess
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from giye.collect.frames import is_admitted
from giye.config import Config, GiyeError, ReleaseSettings
from giye.explore.rim import BIN_WIDTH, STAFF_ROLE, UNDATED, code_year, generation_of
from giye.export.privacy import privacy_for
from giye.ledger.io import read_csv, write_csv, write_text_atomic
from giye.ledger.schemas import split_pipe
from giye.normalize.kinds import KINDS
from giye.normalize.rules import load_snippet_classes, published_ids, record_depth

# D1–D5. k and the 90% bound are the CAREER.md figures; k is also [release].k.
CONCENTRATION = 0.90
SUPPRESSED = "suppressed"
BOUND = ">=90"
PSEUDONYM_RE = re.compile(r"^ps[0-9a-f]{32}$")
YEAR_RE = re.compile(r"^\d{4}$")
CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CV_ORIGIN_RE = re.compile(r"cv:\S+")
URL_RE = re.compile(r"https?://", re.IGNORECASE)

RULE_IDS = (
    "D1",
    "D2",
    "D3",
    "D4",
    "D5",
    "P6",
    "K1",
    "R1",
    "R2",
    "F2",
    "published_ids",
    "REL-URL",
)

# Aggregate tables must not grow a column that could hold a person identifier.
AGGREGATE_FORBIDDEN = frozenset(
    {"gy_id", "ledger_id", "name_ko", "name_en", "name", "title", "source_url", "url", "origin", "aliases"}
)

OPEN_FILES = (
    "roster_facts.csv",
    "programmes.csv",
    "edition_year.csv",
    "entry_generation.csv",
    "activity_kind_year.csv",
    "venue_country_period.csv",
    "record_depth.csv",
    "institutions.csv",
)


class ReleaseError(GiyeError):
    """The release would break a disclosure or privacy check. The message is the line."""


@dataclass
class Cell:
    """One aggregate cell before it is written.

    ``status`` is ``publish`` (rounded count), ``suppress`` (no count; D1 or
    D5) or ``bound`` (D3: the group is at least 90% this value, so the exact
    count is not written). ``n_rows`` is set on CV-row tables only.
    """

    n_people: int
    n_rows: int | None = None
    status: str = "publish"
    share: str = ""

    def measure(self, which: str) -> int:
        if which == "people":
            return self.n_people
        if self.n_rows is None:
            raise ReleaseError("a row-count check was applied to a people table")
        return self.n_rows


def quantile_allowed(n_people: int, q: float, k: int) -> bool:
    """D2. True when at least ``k`` people lie on each side of quantile ``q``.

    ``n >= k / min(q, 1-q)``: the median needs 20, the quartiles 40, P10 and
    P90 100, at ``k`` 10. Minimum and maximum are never a quantile here
    (``q`` of 0 or 1 is refused). These release tables publish counts, not
    quantiles; the builder calls this so a quantile cannot be attached later
    without the threshold. The comparison is multiplied through and allows
    ``1e-9`` because ``1 - q`` is not exact in binary, which otherwise refuses
    P90 at exactly 100.
    """
    if q <= 0 or q >= 1:
        return False
    return n_people * min(q, 1.0 - q) + 1e-9 >= k


def round_to_5(n: int) -> int:
    """D4. Nearest multiple of 5.

    An integer is never exactly halfway between two multiples of 5, so this
    does not depend on half-even rounding.
    """
    return int(5 * round(n / 5))


def _round_half_up(value: float) -> int:
    """A percentage to a whole number. Halves go up (away from zero)."""
    return math.floor(value + 0.5)


def _primary(cells: dict[tuple, Cell], k: int) -> None:
    """D1. A cell with fewer than ``k`` people is suppressed, not published as a count."""
    for cell in cells.values():
        cell.status = "suppress" if cell.n_people < k else "publish"


def _apply_d3(cells: dict[tuple, Cell], groups: Sequence[tuple[str, list[tuple]]], k: int) -> None:
    """D3. A group that is at least 90% one value does not publish that exact share.

    Complementary cells are suppressed even when they have at least ``k``
    people: leaving them numeric would give the exact split. When the group
    has no complement (everyone is in the one cell), the rounded count stays
    and only the share is replaced by the bound. A dominant cell under ``k``
    is already suppressed and is not relabelled as a bound.
    """
    for unit, ids in groups:
        present = [cells[i] for i in ids]
        total = sum(cell.measure(unit) for cell in present)
        if total <= 0:
            continue
        dominant = max(present, key=lambda cell: (cell.measure(unit), -cell.n_people))
        if dominant.measure(unit) / total < CONCENTRATION or dominant.n_people < k:
            continue
        others = [cell for cell in present if cell is not dominant and cell.measure(unit) > 0]
        dominant.share = BOUND
        if not others:
            continue
        dominant.status = "bound"
        for cell in others:
            cell.status = "suppress"


def _unknowns(cells: Mapping[tuple, Cell], ids: Sequence[tuple]) -> list[tuple]:
    return [i for i in ids if cells[i].status != "publish"]


def _knowns(cells: Mapping[tuple, Cell], ids: Sequence[tuple]) -> list[tuple]:
    return [i for i in ids if cells[i].status == "publish"]


def _smallest(cells: Mapping[tuple, Cell], ids: Sequence[tuple], measure: str) -> tuple:
    return min(ids, key=lambda i: (cells[i].measure(measure), cells[i].n_people, i))


def _sums_match(cells: Mapping[tuple, Cell], left: Sequence[tuple], right: Sequence[tuple], measure: str) -> None:
    """The two partitions have to be the same population, or the check is not about those tables."""
    left_sum = sum(cells[i].measure(measure) for i in left)
    right_sum = sum(cells[i].measure(measure) for i in right)
    if left_sum != right_sum:
        raise ReleaseError(
            f"D5: overlapping tables do not add up ({left_sum} and {right_sum} on {measure})"
        )


def apply_disclosure(
    cells: dict[tuple, Cell],
    groups: Sequence[tuple[str, list[tuple]]],
    pairs: Sequence[tuple[list[tuple], list[tuple], str]],
    totals: Sequence[tuple[list[tuple], str]],
    k: int,
) -> None:
    """D1–D5 for one build of the aggregate cells.

    ``pairs`` are two partitions of one population (first-timers against entry
    generation; CV rows by kind and year against CV rows by country and
    period). ``totals`` are partitions whose sum is published as a headline
    count (record depth sums to the people count). A suppressed cell that is
    the only unknown in such an equation is recoverable, so the smallest
    published cell in that equation is suppressed as well (D5) until every
    equation has either no unknown or at least two. Cells only ever move from
    published to suppressed, so this stops.

    Shares (D4) are written only when every cell of the group is published.
    Otherwise the residual of the shares would recover a suppressed cell.
    """
    for _left, _right, measure in pairs:
        _sums_match(cells, _left, _right, measure)
    _primary(cells, k)
    _apply_d3(cells, groups, k)
    guard = 0
    while True:
        guard += 1
        if guard > 100000:
            raise ReleaseError("D5: secondary suppression did not settle")
        victim: tuple | None = None
        for left, right, measure in pairs:
            left_unknown = _unknowns(cells, left)
            right_unknown = _unknowns(cells, right)
            if len(left_unknown) == 1 and not right_unknown and _knowns(cells, right):
                victim = _smallest(cells, _knowns(cells, right), measure)
                break
            if len(right_unknown) == 1 and not left_unknown and _knowns(cells, left):
                victim = _smallest(cells, _knowns(cells, left), measure)
                break
        if victim is None:
            for ids, measure in totals:
                unknown = _unknowns(cells, ids)
                known = _knowns(cells, ids)
                if len(unknown) == 1 and known:
                    victim = _smallest(cells, known, measure)
                    break
        if victim is None:
            break
        cells[victim].status = "suppress"
    for left, right, _measure in pairs:
        left_unknown = _unknowns(cells, left)
        right_unknown = _unknowns(cells, right)
        if (len(left_unknown) == 1 and not right_unknown) or (len(right_unknown) == 1 and not left_unknown):
            raise ReleaseError("D5: a suppressed cell is recoverable from an overlapping table")
    for ids, _measure in totals:
        unknown = _unknowns(cells, ids)
        known = _knowns(cells, ids)
        if len(unknown) == 1 and known:
            raise ReleaseError("D5: a suppressed cell is recoverable from a published total")
    _assign_shares(cells, groups)
    for cell in cells.values():
        if cell.status == "publish" and cell.n_people < k:
            raise ReleaseError("D1: a cell under k would be published")


def _assign_shares(cells: dict[tuple, Cell], groups: Sequence[tuple[str, list[tuple]]]) -> None:
    for unit, ids in groups:
        present = [cells[i] for i in ids]
        incomplete = any(cell.status != "publish" for cell in present)
        total = sum(cell.measure(unit) for cell in present)
        for cell in present:
            if cell.share == BOUND and cell.status == "publish":
                continue
            if cell.status == "bound":
                cell.share = BOUND
            elif incomplete or total <= 0:
                cell.share = SUPPRESSED
            else:
                cell.share = str(_round_half_up(100 * cell.measure(unit) / total))


def _show_count(cell: Cell, which: str) -> str:
    """The cell as written. A suppressed or bound cell is never the digit 0."""
    if cell.status == "suppress":
        return SUPPRESSED
    if cell.status == "bound":
        return BOUND
    n = cell.measure(which)
    rounded = round_to_5(n)
    if n > 0 and rounded == 0:
        return SUPPRESSED
    return str(rounded)


def _show_flag(cell: Cell) -> str:
    if cell.status == "suppress":
        return "yes"
    if cell.status == "bound":
        return "bound"
    return "no"


def _show_share(cell: Cell) -> str:
    if cell.share:
        return cell.share
    if cell.status != "publish":
        return SUPPRESSED
    return ""


@dataclass
class _Population:
    """Published people and the roster editions the release is allowed to use."""

    artists: dict[str, dict[str, str]]
    published: set[str]
    # ledger id → admitted membership rows
    memberships: dict[str, list[dict[str, str]]]
    edition_of: object
    roles: dict[tuple[str, str], str]
    frames: dict[str, object]
    admitted_codes: set[str]


def _load_population(config: Config) -> tuple[_Population, object]:
    """The site's published set, and the privacy object the scans reuse.

    ``published_ids`` already drops ``scope=out``, a missing source, a missing
    collection date, and any status other than empty, ``PUBLISHED`` or
    ``STAGED`` (so ``HIDDEN_BY_REQUEST`` is out). Hidden ledger ids are
    removed again so a merge note that points at a hidden person cannot leak
    back in.
    """
    from giye.config import checked_frames
    from giye.normalize.rules import admitted_memberships
    from giye.publish.snapshot import _edition_resolver, _frames_document

    privacy = privacy_for(config)
    registry = checked_frames(config)
    frame_rows = list(_frames_document(config.frames).get("frames") or [])
    decisions = {frame.code: frame.eligibility.decision for frame in registry.frames}
    edition_of = _edition_resolver(frame_rows, config.field_config)
    artists = {row["ledger_id"]: row for row in read_csv(config.ledger / "artists.csv") if row.get("ledger_id")}
    # The same call the site build makes, so a person cannot be in the release and not on the site.
    published = published_ids(
        list(artists.values()),
        read_csv(config.ledger / "frame_membership.csv"),
        read_csv(config.ledger / "scope.csv"),
        frame_rows,
        edition_of,
        decisions,
    )
    published -= privacy.hidden_ids
    published = {ledger_id for ledger_id in published if artists.get(ledger_id, {}).get("status") != "HIDDEN_BY_REQUEST"}
    admitted = admitted_memberships(read_csv(config.ledger / "frame_membership.csv"), decisions, edition_of)
    by_person: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in admitted:
        if row.get("ledger_id") in published:
            by_person[row["ledger_id"]].append(row)
    roles: dict[tuple[str, str], str] = {}
    for row in read_csv(config.ledger / "activities.csv"):
        origin = row.get("origin") or ""
        if row.get("role") and origin and not origin.startswith("cv:"):
            roles.setdefault((row.get("ledger_id") or "", origin), row["role"])
    frames = {frame.code: frame for frame in registry.frames}
    admitted_codes = {code for code, frame in frames.items() if is_admitted(frame.eligibility.decision)}
    population = _Population(
        artists=artists,
        published=published,
        memberships=by_person,
        edition_of=edition_of,
        roles=roles,
        frames=frames,
        admitted_codes=admitted_codes,
    )
    return population, privacy


def _resolve(population: _Population, frame_code: str) -> tuple[str, str]:
    """Membership code → ``(programme code, year or "")``."""
    resolved = population.edition_of(frame_code)  # type: ignore[operator]
    if resolved:
        programme, year = resolved
        return str(programme), str(year or "")
    year = code_year(frame_code)
    programme = re.sub(r"-\d{4}$", "", frame_code)
    return programme, str(year) if year is not None else ""


def _is_staff(population: _Population, ledger_id: str, frame_code: str) -> bool:
    """R1. Running or judging an edition is not entering it. The role test is the rim's."""
    role = population.roles.get((ledger_id, frame_code), "")
    return bool(role and STAFF_ROLE.search(role))


def _period(year: str) -> str:
    if not YEAR_RE.fullmatch(year or ""):
        return "undated"
    _code, start = generation_of(int(year))
    return f"{start}-{start + BIN_WIDTH - 1}"


def _country_group(country: str) -> str:
    if not (country or "").strip():
        return "unresolved"
    if country.strip().upper() == "KR":
        return "KR"
    return "abroad"


def _depths(config: Config, population: _Population) -> dict[str, str]:
    """P6 level for every published person. The processed attribute wins when it is there.

    A person the attribute table missed is computed with the same function the
    normaliser uses, so the record-depth table still partitions the release.
    The evidence string is not kept: it names source ids.
    """
    attributes = read_csv(config.processed / "artist_attributes.csv")
    found = {
        row["ledger_id"]: row["value"]
        for row in attributes
        if row.get("field") == "record_depth" and row.get("ledger_id") in population.published
    }
    missing = population.published - set(found)
    if not missing:
        return found
    by_artist: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(config.ledger / "activities.csv"):
        if row.get("ledger_id") in missing:
            by_artist[row["ledger_id"]].append(row)
    links: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(config.ledger / "links.csv"):
        if row.get("ledger_id") in missing:
            links[row["ledger_id"]].append(row)
    sources: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(config.ledger / "cv_sources.csv"):
        if row.get("ledger_id") in missing:
            sources[row["ledger_id"]].append(row)
    mediums: dict[str, list[str]] = defaultdict(list)
    for row in attributes:
        if row.get("field") == "medium" and row.get("ledger_id") in missing and row.get("value"):
            mediums[row["ledger_id"]].append(row["value"])
    snippet_path = config.work / "tendency" / "snippets.jsonl"
    snippets = load_snippet_classes(snippet_path) if snippet_path.is_file() else {}
    for ledger_id in sorted(missing):
        value, _evidence = record_depth(
            activities=by_artist.get(ledger_id, []),
            links=links.get(ledger_id, []),
            cv_sources=sources.get(ledger_id, []),
            medium_values=mediums.get(ledger_id, []),
            snippet_classes=snippets.get(ledger_id, []),
            memberships=[row.get("frame_code") or "" for row in population.memberships.get(ledger_id, [])],
        )
        found[ledger_id] = value
    return found


def _alnum_norm(value: str) -> str:
    """Percent-decode, lower-case, and keep letters and digits. REL-URL."""
    text = unquote(unicodedata.normalize("NFC", value or ""))
    return "".join(character for character in text.lower() if character.isalnum())


def _hangul_syllables(value: str) -> int:
    """Hangul syllable blocks in ``value``. One precomposed block is one syllable."""
    text = unicodedata.normalize("NFC", value or "")
    return sum(1 for character in text if "\uac00" <= character <= "\ud7a3")


def _latin_letters(token: str) -> int:
    return sum(1 for character in token if "a" <= character <= "z")


def name_tokens(raw: str) -> set[str]:
    """Normalised forms of one name that REL-URL looks for.

    A Hangul name of two or more syllables, and a Latin name of four or more
    letters after normalisation. The Latin name is also taken with its
    whitespace-separated parts reversed, so a slug ``kim-haneul`` meets
    ``Haneul Kim``.
    """
    raw = (raw or "").strip()
    if not raw:
        return set()
    token = _alnum_norm(raw)
    found: set[str] = set()
    if _hangul_syllables(raw) >= 2 and token:
        found.add(token)
    if _latin_letters(token) >= 4:
        found.add(token)
        parts = raw.split()
        if len(parts) >= 2:
            reversed_name = _alnum_norm(" ".join(reversed(parts)))
            if _latin_letters(reversed_name) >= 4:
                found.add(reversed_name)
    return found


def _person_name_tokens(artist: Mapping[str, str]) -> set[str]:
    found: set[str] = set()
    for raw in (artist.get("name_ko"), artist.get("name_en"), *split_pipe(artist.get("aliases"))):
        found |= name_tokens(raw or "")
    return found


def url_embeds_name(url: str, tokens: set[str]) -> bool:
    """True when the smashed URL contains one of ``tokens``."""
    smashed = _alnum_norm(url)
    return any(token and token in smashed for token in tokens)


# A slash or a dot splits a URL. A hyphen or underscore does not: ``kim-haneul`` is one name.
_COMPONENT_RE = re.compile(r"[0-9a-z\uac00-\ud7a3_-]+")
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s)>\"]+")
def _author_credit_re(given: str, family: str, repository_url: str) -> re.Pattern[str]:
    """Lines that name the configured creator, not a person on the roster.

    ``family-names`` and ``given-names`` are Citation File Format keys. The
    given name, family name and repository URL come from ``[release]``.
    """
    parts = [r"family-names\s*:", r"given-names\s*:"]
    for value in (given, family, repository_url):
        text = (value or "").strip()
        if not text:
            continue
        parts.append(re.escape(text))
        if "://" in text:
            bare = text.split("://", 1)[1]
            if bare:
                parts.append(re.escape(bare))
    return re.compile("|".join(parts), re.IGNORECASE)


def _url_components(url: str) -> set[str]:
    """Host labels and path segments with hyphens and underscores removed.

    ``program/art`` stays two pieces, so it does not become ``mart``.
    ``kim-haneul`` is one piece.
    """
    text = unquote(unicodedata.normalize("NFC", url or "")).lower()
    found = set()
    for part in _COMPONENT_RE.findall(text):
        token = "".join(character for character in part if character.isalnum())
        if token:
            found.add(token)
    return found


def _prose_forms(value: str) -> set[str]:
    """Word spans that can equal a whole name token.

    A single Latin word of four letters is not a span. That token collides
    with ordinary English in the datasheet: one published name normalises to
    ``date``. A longer word, a Hangul word of two or more syllables, and a
    span of up to six adjacent words are included. REL-URL still treats four
    Latin letters as a name inside a source URL.
    """
    words = re.findall(r"[0-9A-Za-z\uac00-\ud7a3]+", unicodedata.normalize("NFC", value or ""))
    norms = [token for token in (_alnum_norm(word) for word in words) if token]
    forms: set[str] = set()
    for index, token in enumerate(norms):
        if _latin_letters(token) >= 5 or _hangul_syllables(token) >= 2:
            forms.add(token)
        joined = token
        for nxt in norms[index + 1 : index + 6]:
            joined += nxt
            forms.add(joined)
    return forms


def _open_value_forms(value: str) -> set[str]:
    """Normalised pieces of one open cell or document in which a name token can sit."""
    if (value or "").lower().startswith(("http://", "https://")):
        return _url_components(value)
    forms = _prose_forms(value)
    for url in _URL_IN_TEXT_RE.findall(value or ""):
        forms.update(_url_components(url))
    return forms


def _strip_author_credit(text: str, pattern: re.Pattern[str]) -> str:
    """Drop citation lines. The configured creator is the one name the open record may show."""
    kept = [line for line in text.splitlines() if not pattern.search(line)]
    return "\n".join(kept)


# Institution and programme name fields are not person names. Roster name columns
# exist only when release.open_names is true; they are the names that switch writes.
_OPEN_NAME_SKIP = {
    "programmes.csv": frozenset({"name_ko", "name_en", "operators"}),
    "institutions.csv": frozenset({"name", "aliases"}),
    "roster_facts.csv": frozenset({"name_ko", "name_en"}),
}


def open_name_leaks(
    tables: Mapping[str, tuple[list[str], list[dict[str, str]]]],
    texts: Mapping[str, str],
    tokens: set[str],
    *,
    creator_given: str = "",
    creator_family: str = "",
    repository_url: str = "",
) -> list[str]:
    """Reasons an open file carries a published name token. Empty means clear.

    The token is the REL-URL normalisation. A URL hits when a host label or a
    path segment equals a token. Prose hits when a word span equals a token.
    Substring search on the whole file is not used: the programme page that
    replaces a withheld URL can contain a four-letter name (``apecamp`` contains
    ``camp``), and the datasheet's word ``date`` is itself a normalised name.
    The licence and citation author lines are not hits, and neither are
    institution or programme name fields.
    """
    if not tokens:
        return []
    reasons = []
    credit = _author_credit_re(creator_given, creator_family, repository_url)
    for filename, (columns, rows) in tables.items():
        skipped = _OPEN_NAME_SKIP.get(filename, frozenset())
        for column in columns:
            if column in skipped:
                continue
            for row in rows:
                if _open_value_forms(row.get(column) or "") & tokens:
                    reasons.append(f"open/{filename} column {column} contains a published name")
                    break
    for filename, text in texts.items():
        if _open_value_forms(_strip_author_credit(text, credit)) & tokens:
            reasons.append(f"open/{filename} contains a published name")
    return reasons


def _frame_http_source(population: _Population, programme: str) -> str:
    """The frame registry's own page when it is http(s). Otherwise empty.

    This is the replacement REL-URL writes. It does not fall through to a
    declared-roster URL: that fallback is only for the programme table.
    """
    frame = population.frames.get(programme)
    source = (getattr(frame, "source_url", "") or "").strip() if frame else ""
    if source.startswith(("http://", "https://")):
        return source
    return ""


def _roster_facts(
    population: _Population, *, open_names: bool
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    """One row per published person and admitted roster edition.

    REL-URL: the open ``source_url`` does not keep a link that embeds a name.
    The membership URL is withheld when, after percent-decoding, lower-casing
    and dropping every non-letter, it contains that person's normalised name.
    It is also withheld when a host label or path segment equals any published
    person's token, so a shared page that names someone else does not stay in
    the open file. The open cell then carries the programme's own http(s) page,
    or is empty, and ``source_withheld`` is ``yes``. The withheld URL is
    returned for ``restricted/roster_sources_withheld.csv``.
    """
    fields = ["gy_id", "frame_code", "programme", "year", "source_url", "source_withheld", "collected_at"]
    if open_names:
        fields = [
            "gy_id",
            "name_ko",
            "name_en",
            "frame_code",
            "programme",
            "year",
            "source_url",
            "source_withheld",
            "collected_at",
        ]
    own_tokens: dict[str, set[str]] = {}
    all_tokens: set[str] = set()
    for ledger_id in population.published:
        tokens = _person_name_tokens(population.artists[ledger_id])
        own_tokens[ledger_id] = tokens
        all_tokens |= tokens
    rows: list[dict[str, str]] = []
    withheld: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ledger_id in sorted(population.published):
        artist = population.artists[ledger_id]
        for mem in sorted(population.memberships.get(ledger_id, []), key=lambda row: row.get("frame_code") or ""):
            frame_code = mem.get("frame_code") or ""
            if (ledger_id, frame_code) in seen:
                continue
            seen.add((ledger_id, frame_code))
            programme, year = _resolve(population, frame_code)
            source = (mem.get("source_url") or "").strip()
            if not source.startswith("http"):
                frame = population.frames.get(programme)
                source = getattr(frame, "source_url", "") if frame else ""
            embeds = bool(source) and (
                url_embeds_name(source, own_tokens.get(ledger_id, set()))
                or bool(_url_components(source) & all_tokens)
            )
            gy_id = artist.get("gy_id") or ""
            if embeds:
                withheld.append({"gy_id": gy_id, "frame_code": frame_code, "source_url": source})
                source = _frame_http_source(population, programme)
                source_withheld = "yes"
            else:
                source_withheld = "no"
            collected = (mem.get("collected_at") or artist.get("collected_at") or "").strip()
            row = {
                "gy_id": gy_id,
                "frame_code": frame_code,
                "programme": programme,
                "year": year,
                "source_url": source,
                "source_withheld": source_withheld,
                "collected_at": collected,
            }
            if open_names:
                row["name_ko"] = artist.get("name_ko") or ""
                row["name_en"] = artist.get("name_en") or ""
            rows.append(row)
    rows.sort(key=lambda row: (row["gy_id"], row["frame_code"]))
    withheld.sort(key=lambda row: (row["gy_id"], row["frame_code"]))
    return fields, rows, withheld


def _declared_http_source(config: Config, code: str) -> str:
    """Earliest accepted http(s) source in ``declared_roster_sizes.csv`` for ``code``.

    Earliest is the edition string in ascending order. A row that is not
    ``accepted``, or whose source is not http(s), is not a candidate. The
    quote and the snapshot path on that file are not read.
    """
    path = config.work / "declared_roster_sizes.csv"
    if not path.is_file():
        return ""
    chosen_edition: str | None = None
    chosen = ""
    for row in read_csv(path):
        if (row.get("status") or "").strip() != "accepted":
            continue
        if (row.get("frame") or "").strip() != code:
            continue
        url = (row.get("source_url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        edition = (row.get("edition") or "").strip()
        if chosen_edition is None or edition < chosen_edition:
            chosen_edition = edition
            chosen = url
    return chosen


def _programmes(population: _Population, config: Config) -> tuple[list[str], list[dict[str, str]]]:
    """One row per admitted frame. Access mode is the recorded F2 sentence.

    Frames that share a stem stay separate rows. The operator quote and the
    snapshot path stay out: the path is a local file, and the quote is page
    text that can name participants. The credit is the name, the role and the
    http(s) source the registry already checked. When the frame's own source
    is not http(s), the earliest accepted declared-roster source is used.
    """
    fields = [
        "code",
        "name_ko",
        "name_en",
        "years_covered",
        "access_mode",
        "eligibility",
        "source_url",
        "operators",
    ]
    rows: list[dict[str, str]] = []
    for code in sorted(population.admitted_codes):
        frame = population.frames[code]
        credits = []
        for operator in frame.operators:  # type: ignore[attr-defined]
            parts = [operator.name_ko, operator.name_en, operator.role, operator.source_url]
            credits.append(" / ".join(part for part in parts if part))
        rows.append(
            {
                "code": code,
                "name_ko": frame.name_ko,  # type: ignore[attr-defined]
                "name_en": frame.name_en,  # type: ignore[attr-defined]
                "years_covered": frame.years_covered,  # type: ignore[attr-defined]
                "access_mode": " ".join((frame.eligibility.f2_cohort or "").split()),  # type: ignore[attr-defined]
                "eligibility": frame.eligibility.decision,  # type: ignore[attr-defined]
                "source_url": _programme_source(frame, code, config),
                "operators": " | ".join(credits),
            }
        )
    return fields, rows


def _programme_source(frame: object, code: str, config: Config) -> str:
    """The frame page when it is http(s); otherwise the earliest accepted declared source."""
    source = (getattr(frame, "source_url", "") or "").strip()
    if source.startswith(("http://", "https://")):
        return source
    fallback = _declared_http_source(config, code)
    return fallback or source


def _staff_shares(cells: dict[tuple, Cell], programme_index: int) -> None:
    """Share of the full staff-adjusted group, including cells that will be omitted.

    The denominator is every entrant in the programme, so an omitted cell under
    ``k`` does not inflate the published percent. A cell that is at least 90%
    of that group is ``>=90``. Anything else kept is an integer percent.
    """
    grouped: dict[str, list[Cell]] = defaultdict(list)
    for key, cell in cells.items():
        grouped[str(key[programme_index])].append(cell)
    for group in grouped.values():
        total = sum(cell.n_people for cell in group)
        if total <= 0:
            continue
        for cell in group:
            if cell.n_people / total >= CONCENTRATION:
                cell.share = BOUND
            else:
                cell.share = str(_round_half_up(100 * cell.n_people / total))


def _staff_row(cell: Cell, k: int) -> dict[str, str] | None:
    """Omit a staff-adjusted cell under ``k``. Publish ``round_to_5`` otherwise.

    ``apply_disclosure`` is not used. It would store ``suppress`` on this
    group-by, and a suppressed cell would equal a count ``roster_facts``
    already shows.
    """
    if cell.n_people < k:
        return None
    if cell.status == "suppress" or cell.share == SUPPRESSED:
        raise ReleaseError("D5: a suppressed cell equals a staff-adjusted group-by of published roster_facts")
    return {"n_people": str(round_to_5(cell.n_people)), "share": cell.share, "suppressed": "no"}


def _assert_no_suppressed_staff_rows(rows: Sequence[dict[str, str]]) -> None:
    """D5. These rows are the staff-adjusted group-by. ``suppressed`` must not appear."""
    for row in rows:
        if row.get("suppressed") != "no" or SUPPRESSED in row.values():
            raise ReleaseError("D5: a suppressed cell equals a staff-adjusted group-by of published roster_facts")


def _entrant_tables(population: _Population, k: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """First-timers and entry generation. Edition size is not written.

    Edition size repeats a count of ``roster_facts`` (staff included), so the
    measure is omitted entirely, including cells already at or above ``k``.

    Both written tables are a staff-adjusted group-by. Staff is ``_is_staff``
    (the first non-CV role). A cell under ``k`` is left out. A cell at or
    above ``k`` is rounded to 5 with ``suppressed`` = no, including a cell a
    secondary-suppression pass would have labelled ``suppressed``.

    Entry generation is the rim's (R1–R2): the earliest non-staff roster year
    across programmes, in five-year bins anchored at 2000. Each person is
    counted once per programme they entered (a non-staff edition), in that
    one field-entry bin. First-timers partition the same people by the first
    non-staff year of that programme.
    """
    first_people: dict[tuple[str, str], set[str]] = defaultdict(set)
    gen_people: dict[tuple[str, str], set[str]] = defaultdict(set)
    for ledger_id in population.published:
        dated_nonstaff: list[int] = []
        by_programme: dict[str, list[str]] = defaultdict(list)
        for mem in population.memberships.get(ledger_id, []):
            frame_code = mem.get("frame_code") or ""
            programme, year = _resolve(population, frame_code)
            if _is_staff(population, ledger_id, frame_code):
                continue
            if programme:
                by_programme[programme].append(year)
            if YEAR_RE.fullmatch(year):
                dated_nonstaff.append(int(year))
        if not by_programme:
            continue
        if dated_nonstaff:
            generation = generation_of(min(dated_nonstaff))[0]
        else:
            generation = UNDATED
        for programme, years in by_programme.items():
            dated = [int(year) for year in years if YEAR_RE.fullmatch(year)]
            entry = str(min(dated)) if dated else "undated"
            first_people[(programme, entry)].add(ledger_id)
            gen_people[(programme, generation)].add(ledger_id)
    first_cells = {
        ("first_timers", programme, year): Cell(n_people=len(people))
        for (programme, year), people in first_people.items()
    }
    gen_cells = {
        (programme, generation): Cell(n_people=len(people)) for (programme, generation), people in gen_people.items()
    }
    _staff_shares(first_cells, 1)
    _staff_shares(gen_cells, 0)
    edition_rows = _measure_rows(first_cells, k)
    gen_rows = []
    for (programme, generation), cell in sorted(gen_cells.items()):
        written = _staff_row(cell, k)
        if written is None:
            continue
        gen_rows.append({"programme": programme, "generation": generation, **written})
    _assert_no_suppressed_staff_rows([*edition_rows, *gen_rows])
    return edition_rows, gen_rows


def _measure_rows(first_cells: dict[tuple, Cell], k: int) -> list[dict[str, str]]:
    """``first_timers`` only. ``edition_size`` is skipped: it repeats ``roster_facts``."""
    rows = []
    for (_measure, programme, year), cell in sorted(first_cells.items(), key=lambda item: (item[0][1], item[0][2])):
        written = _staff_row(cell, k)
        if written is None:
            continue
        rows.append({"programme": programme, "year": year, "measure": "first_timers", **written})
    return rows


def _publishable_dropped(row: Mapping[str, str]) -> bool:
    """True when ``publishable`` is ``no``.

    A missing or blank value is not treated as ``no``. The open CV tables and
    ``restricted/activities.csv`` both call this, so they drop the same rows
    and keep a missing value the same way.
    """
    return (row.get("publishable") or "").strip().lower() == "no"


def _cv_tables(
    config: Config, population: _Population, k: int
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[tuple, Cell], dict[tuple, Cell], dict[str, tuple[int, int]]]:
    """CV-origin activity rows: kind × year, and country group × five-year period.

    A row is kept when ``origin`` starts with ``cv:``, ``activity_channel`` is
    ``activity``, and ``publishable`` is not ``no`` (``_publishable_dropped``).
    Background and hidden channels are not in these counts. Both tables count
    the same rows (``n_rows`` adds up). ``n_people`` does not: one person can
    have several kinds and both a domestic and an abroad row.
    """
    kind_people: dict[tuple[str, str], set[str]] = defaultdict(set)
    kind_rows: dict[tuple[str, str], int] = defaultdict(int)
    country_people: dict[tuple[str, str], set[str]] = defaultdict(set)
    country_rows: dict[tuple[str, str], int] = defaultdict(int)
    kind_people_all: dict[str, set[str]] = defaultdict(set)
    kind_rows_all: dict[str, int] = defaultdict(int)
    cv_people: set[str] = set()
    for row in read_csv(config.processed / "activities.csv"):
        if not str(row.get("origin") or "").startswith("cv:"):
            continue
        if (row.get("activity_channel") or "") != "activity":
            continue
        if _publishable_dropped(row):
            continue
        ledger_id = row.get("ledger_id") or ""
        if ledger_id not in population.published:
            continue
        cv_people.add(ledger_id)
        year = row.get("year") or ""
        year_key = year if YEAR_RE.fullmatch(year) else "undated"
        kind = row.get("activity_kind") or "unresolved"
        bucket = _country_group(row.get("venue_country") or "")
        period = _period(year_key if year_key != "undated" else "")
        kind_people[(kind, year_key)].add(ledger_id)
        kind_rows[(kind, year_key)] += 1
        country_people[(bucket, period)].add(ledger_id)
        country_rows[(bucket, period)] += 1
        kind_people_all[kind].add(ledger_id)
        kind_rows_all[kind] += 1
    kind_cells = {
        key: Cell(n_people=len(kind_people[key]), n_rows=kind_rows[key]) for key in kind_rows
    }
    country_cells = {
        key: Cell(n_people=len(country_people[key]), n_rows=country_rows[key]) for key in country_rows
    }
    groups: list[tuple[str, list[tuple]]] = []
    years = sorted({key[1] for key in kind_cells})
    for year in years:
        groups.append(("rows", [key for key in kind_cells if key[1] == year]))
    periods = sorted({key[1] for key in country_cells})
    for period in periods:
        groups.append(("rows", [key for key in country_cells if key[1] == period]))
    pairs: list[tuple[list[tuple], list[tuple], str]] = []
    for period in periods:
        kind_ids = [key for key in kind_cells if _period(key[1] if key[1] != "undated" else "") == period]
        country_ids = [key for key in country_cells if key[1] == period]
        pairs.append((kind_ids, country_ids, "rows"))
    # Kind totals are cited in stats.json. They are a margin of n_rows, so one
    # suppressed year of a kind would be recoverable from that total.
    kind_totals = [
        ([key for key in kind_cells if key[0] == kind], "rows") for kind in sorted({key[0] for key in kind_cells})
    ]
    if set(kind_cells) & set(country_cells):
        raise ReleaseError("D5: kind and country cells share a key")
    apply_disclosure({**kind_cells, **country_cells}, groups, pairs, kind_totals, k)
    kind_out = [
        {
            "activity_kind": kind,
            "year": year,
            "n_people": _show_count(cell, "people"),
            "n_rows": _show_count(cell, "rows"),
            "share": _show_share(cell),
            "suppressed": _show_flag(cell),
        }
        for (kind, year), cell in sorted(kind_cells.items())
    ]
    country_out = [
        {
            "venue_country": bucket,
            "period": period,
            "n_people": _show_count(cell, "people"),
            "n_rows": _show_count(cell, "rows"),
            "share": _show_share(cell),
            "suppressed": _show_flag(cell),
        }
        for (bucket, period), cell in sorted(country_cells.items())
    ]
    by_kind = {kind: (len(people), kind_rows_all[kind]) for kind, people in kind_people_all.items()}
    by_kind["__cv_people__"] = (len(cv_people), sum(kind_rows_all.values()))
    return kind_out, country_out, kind_cells, country_cells, by_kind


def _depth_table(depths: dict[str, str], k: int, *, publish_total: bool) -> tuple[list[dict[str, str]], dict[tuple, Cell]]:
    """One row per record-depth level that anyone has. The levels partition published people."""
    counts: dict[str, int] = defaultdict(int)
    for value in depths.values():
        counts[str(value)] += 1
    cells = { (level,): Cell(n_people=n) for level, n in counts.items() }
    ids = list(cells)
    groups = [("people", ids)] if ids else []
    totals = [(ids, "people")] if publish_total and ids else []
    apply_disclosure(cells, groups, [], totals, k)
    rows = [
        {
            "record_depth": level,
            "n_people": _show_count(cell, "people"),
            "share": _show_share(cell),
            "suppressed": _show_flag(cell),
        }
        for (level,), cell in sorted(cells.items())
    ]
    return rows, cells


def _institutions(config: Config, k: int) -> tuple[list[str], list[dict[str, str]], set[str]]:
    """Venue entities with at least ``k`` distinct people. ``n_artists`` is rounded to 5.

    The threshold is applied to the unrounded count, so a venue of 8 people is
    left out even though 8 would round to 10.
    """
    path = config.processed / "venues.csv"
    if not path.is_file():
        raise ReleaseError(f"processed venues not found ({path}); run giye normalize")
    fields = ["venue_id", "name", "aliases", "kind", "city", "country", "kr_region", "n_artists", "rules"]
    rows = []
    kept: set[str] = set()
    for row in read_csv(path):
        try:
            n_artists = int(row.get("n_artists") or 0)
        except ValueError:
            continue
        if n_artists < k or not row.get("venue_id"):
            continue
        kept.add(row["venue_id"])
        rounded = round_to_5(n_artists)
        rows.append(
            {
                "venue_id": row.get("venue_id") or "",
                "name": row.get("name") or "",
                "aliases": row.get("aliases") or "",
                "kind": row.get("kind") or "",
                "city": row.get("city") or "",
                "country": row.get("country") or "",
                "kr_region": row.get("kr_region") or "",
                "n_artists": str(rounded if not (n_artists > 0 and rounded == 0) else SUPPRESSED),
                "rules": row.get("rules") or "",
            }
        )
    rows.sort(key=lambda row: row["venue_id"])
    return fields, rows, kept


def _pseudonyms(
    ledger_ids: Sequence[str],
    *,
    mode: str,
    secret: Path | None,
    output: Path,
    rng: random.Random | None,
) -> dict[str, str]:
    """One token per person. ``per_release`` is random; ``stable_hmac`` repeats across versions.

    The secret file has to sit outside the release directory: a key left in
    the deposit would undo the pseudonym.
    """
    if mode == "stable_hmac":
        if secret is None or not secret.is_file():
            raise ReleaseError("[release] pseudonym = stable_hmac needs pseudonym_secret, a file that exists")
        if _is_inside(secret, output) or _is_inside(secret, output.parent):
            raise ReleaseError("pseudonym secret must not live inside the release directory")
        key = secret.read_bytes().strip()
        if not key:
            raise ReleaseError("pseudonym secret is empty")
        return {ledger_id: "ps" + hmac.new(key, ledger_id.encode("utf-8"), hashlib.sha256).hexdigest()[:32] for ledger_id in ledger_ids}
    used: set[str] = set()
    out: dict[str, str] = {}
    for ledger_id in sorted(ledger_ids):
        while True:
            token = "ps" + secrets.token_hex(16) if rng is None else "ps" + "".join(rng.choice("0123456789abcdef") for _ in range(32))
            if token not in used and PSEUDONYM_RE.fullmatch(token):
                used.add(token)
                out[ledger_id] = token
                break
    return out


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _restricted_people(
    population: _Population,
    pseudonym: dict[str, str],
    depths: dict[str, str],
    config: Config,
) -> list[dict[str, str]]:
    from giye.normalize.language import language_for
    from giye.resolve.teams import team_like

    language = language_for(config)
    words = config.field_config.compiled_team_words()
    # Field entry and the programme list, staff editions included in the list
    # because the list is membership, not the entry year.
    field_year: dict[str, int | None] = {}
    programmes: dict[str, set[str]] = defaultdict(set)
    nonstaff_years: dict[str, list[int]] = defaultdict(list)
    for ledger_id in population.published:
        for mem in population.memberships.get(ledger_id, []):
            frame_code = mem.get("frame_code") or ""
            programme, year = _resolve(population, frame_code)
            if programme:
                programmes[ledger_id].add(programme)
            if not _is_staff(population, ledger_id, frame_code) and YEAR_RE.fullmatch(year):
                nonstaff_years[ledger_id].append(int(year))
        field_year[ledger_id] = min(nonstaff_years[ledger_id]) if nonstaff_years[ledger_id] else None
    rows = []
    for ledger_id in population.published:
        artist = population.artists[ledger_id]
        year = field_year.get(ledger_id)
        generation = generation_of(year)[0] if year is not None else UNDATED
        rows.append(
            {
                "pseudonym": pseudonym[ledger_id],
                "entry_year": str(year) if year is not None else "",
                "entry_generation": generation,
                "record_depth": depths[ledger_id],
                "programmes": "|".join(sorted(programmes.get(ledger_id, ()))),
                "team": "1" if team_like(artist, words=words, language=language) else "0",
            }
        )
    rows.sort(key=lambda row: row["pseudonym"])
    return rows


def _restricted_activities(
    config: Config,
    population: _Population,
    pseudonym: dict[str, str],
    institutions: set[str],
) -> list[dict[str, str]]:
    """Activity-channel rows only. Venue and funder ids survive only when the entity is in the open list.

    A row with ``publishable`` ``no`` is skipped, after the channel test.
    A missing ``publishable`` is kept (``_publishable_dropped``), the same
    rule as the open CV tables. Titles, free text, source URLs and the raw
    ``origin`` (it embeds a source id) are not copied. ``origin_type`` is
    ``cv`` or ``roster``.
    """
    if not (config.processed / "activities.csv").is_file():
        raise ReleaseError(f"processed activities not found ({config.processed / 'activities.csv'}); run giye normalize")
    rows = []
    for row in read_csv(config.processed / "activities.csv"):
        if (row.get("activity_channel") or "") != "activity":
            continue
        if _publishable_dropped(row):
            continue
        ledger_id = row.get("ledger_id") or ""
        if ledger_id not in population.published:
            continue
        year = row.get("year") or ""
        if year and not YEAR_RE.fullmatch(year):
            year = ""
        venue_id = row.get("venue_id") or ""
        funder_id = row.get("funder_id") or ""
        event_link = row.get("event_link") or ""
        if not CODE_RE.fullmatch(event_link):
            event_link = ""
        origin = row.get("origin") or ""
        rows.append(
            {
                "pseudonym": pseudonym[ledger_id],
                "year": year,
                "activity_kind": row.get("activity_kind") or "",
                "activity_channel": "activity",
                "venue_id": venue_id if venue_id in institutions else "",
                "venue_country": (row.get("venue_country") or "").strip(),
                "venue_region": row.get("venue_region") or "",
                "venue_kind": row.get("venue_kind") or "",
                "funder_id": funder_id if funder_id in institutions else "",
                "event_link": event_link,
                "origin_type": "cv" if origin.startswith("cv:") else "roster",
            }
        )
    rows.sort(key=lambda row: (row["pseudonym"], row["year"], row["activity_kind"], row["venue_id"], row["event_link"]))
    return rows


ROSTER_NAME_FIELDS = ["gy_id", "name_ko", "name_en", "aliases"]


def _restricted_roster_names(population: _Population) -> list[dict[str, str]]:
    """Published people only: ``gy_id`` and the names. No pseudonym column.

    ``HIDDEN_BY_REQUEST`` is skipped. Those people are already outside
    ``population.published``; the status test is the second gate. This table
    is not passed to ``restricted_leaks``, which treats a ``gy_id`` and a
    ledger name as a leak.
    """
    rows = []
    for ledger_id in sorted(population.published):
        artist = population.artists[ledger_id]
        if (artist.get("status") or "") == "HIDDEN_BY_REQUEST":
            continue
        rows.append(
            {
                "gy_id": artist.get("gy_id") or "",
                "name_ko": artist.get("name_ko") or "",
                "name_en": artist.get("name_en") or "",
                "aliases": artist.get("aliases") or "",
            }
        )
    rows.sort(key=lambda row: row["gy_id"])
    return rows


def _check_roster_names(rows: Sequence[dict[str, str]], population: _Population) -> None:
    """The name file's own check. ``restricted_leaks`` cannot scan it.

    No URL, no pseudonym token, no ``ledger_id`` column. Every ``gy_id`` is
    the published person's id in ``artists.csv``, and the row count is the
    published set.
    """
    if "ledger_id" in ROSTER_NAME_FIELDS:
        raise ReleaseError("roster_names.csv has a ledger_id column")
    if len(rows) != len(population.published):
        raise ReleaseError(
            f"roster_names.csv has {len(rows)} rows; published people are {len(population.published)}"
        )
    by_gy = {}
    for ledger_id in population.published:
        artist = population.artists[ledger_id]
        gy_id = artist.get("gy_id") or ""
        if not gy_id or gy_id in by_gy:
            raise ReleaseError("a published person has a missing or duplicate gy_id")
        by_gy[gy_id] = artist
    seen: set[str] = set()
    for row in rows:
        gy_id = row.get("gy_id") or ""
        artist = by_gy.get(gy_id)
        if artist is None or gy_id in seen:
            raise ReleaseError("roster_names gy_id does not match artists.csv")
        seen.add(gy_id)
        if (row.get("name_ko") or "") != (artist.get("name_ko") or ""):
            raise ReleaseError("roster_names name_ko does not match artists.csv")
        if (row.get("name_en") or "") != (artist.get("name_en") or ""):
            raise ReleaseError("roster_names name_en does not match artists.csv")
        blob = "\n".join(row.get(column) or "" for column in ROSTER_NAME_FIELDS)
        if URL_RE.search(blob):
            raise ReleaseError("roster_names.csv holds a URL")
        if PSEUDONYM_RE.search(blob):
            raise ReleaseError("roster_names.csv holds a pseudonym")
    if seen != set(by_gy):
        raise ReleaseError("roster_names gy_id does not match artists.csv")


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value or "").strip()


def _ledger_secrets(config: Config) -> dict[str, set[str]]:
    """Names, ids, URLs and titles the restricted tier must not contain.

    Names are exact tokens, the same way a public tree is scanned against the
    ledger. A one-character name is not a token. Titles that are only a
    four-digit year are not treated as titles: the year column is a year.
    """
    names: set[str] = set()
    gy_ids: set[str] = set()
    ledger_ids: set[str] = set()
    for row in read_csv(config.ledger / "artists.csv"):
        if row.get("gy_id"):
            gy_ids.add(row["gy_id"].strip())
        if row.get("ledger_id"):
            ledger_ids.add(row["ledger_id"].strip())
        for raw in (row.get("name_ko"), row.get("name_en"), *split_pipe(row.get("aliases"))):
            name = _nfc(raw or "")
            if len(name) >= 2:
                names.add(name)
    urls = {row["url"].strip() for row in read_csv(config.ledger / "links.csv") if row.get("url")}
    titles = set()
    cv_origins = set()
    cv_urls = set()
    for row in read_csv(config.ledger / "activities.csv"):
        title = _nfc(row.get("title") or "")
        if len(title) >= 2 and not YEAR_RE.fullmatch(title):
            titles.add(title)
        origin = row.get("origin") or ""
        if origin.startswith("cv:"):
            cv_origins.add(origin)
    for row in read_csv(config.ledger / "cv_sources.csv"):
        for key in ("url", "fetch_url"):
            if row.get(key):
                cv_urls.add(row[key].strip())
    return {
        "names": names,
        "gy_ids": gy_ids,
        "ledger_ids": ledger_ids,
        "urls": urls,
        "titles": titles,
        "cv_origins": cv_origins,
        "cv_urls": cv_urls,
    }


# Values a restricted column is allowed to hold even when a ledger title happens to use the same letters.
_KIND_SET = frozenset(KINDS) | {"unresolved"}


def _structural(column: str, value: str, *, frame_codes: set[str], venue_ids: set[str], regions: set[str]) -> bool:
    """True when ``value`` is this column's own code, not a copied ledger string."""
    if column == "pseudonym":
        return bool(PSEUDONYM_RE.fullmatch(value))
    if column in {"year", "entry_year"}:
        return bool(YEAR_RE.fullmatch(value))
    if column in {"entry_generation", "generation"}:
        return value == UNDATED or value.startswith("GEN-")
    if column == "record_depth":
        return value in {"1", "2", "3", "4"}
    if column == "team":
        return value in {"0", "1"}
    if column == "activity_kind":
        return value in _KIND_SET
    if column == "activity_channel":
        return value == "activity"
    if column == "origin_type":
        return value in {"cv", "roster"}
    if column == "venue_country":
        return bool(re.fullmatch(r"[A-Z]{2}", value))
    if column == "venue_region":
        return value in regions
    if column in {"venue_id", "funder_id"}:
        return value in venue_ids
    if column == "venue_kind":
        return bool(re.fullmatch(r"[a-z_]+", value))
    if column == "event_link":
        return value in frame_codes or bool(CODE_RE.fullmatch(value))
    if column == "programmes":
        parts = [part for part in value.split("|") if part]
        return bool(parts) and all(part in frame_codes for part in parts)
    return False


def restricted_leaks(
    tables: Mapping[str, tuple[list[str], list[dict[str, str]]]],
    secrets: Mapping[str, set[str]],
    *,
    frame_codes: set[str],
    venue_ids: set[str],
    regions: set[str],
) -> list[str]:
    """Reasons a restricted table echoes the ledger. Empty means the tables are clear.

    A cell that is only its column's code (a year, a kind, a frame code, a
    venue id) is not reported: those strings are produced by the builder.
    Anything else that equals a name, a ``gy_id``, a ledger id, a link URL or
    a title is a leak. Any ``http`` URL is a leak even when it is not in
    ``links.csv``.
    """
    reasons = []
    names = secrets["names"]
    gy_ids = secrets["gy_ids"]
    ledger_ids = secrets["ledger_ids"]
    urls = secrets["urls"]
    titles = secrets["titles"]
    for filename, (columns, rows) in tables.items():
        for row in rows:
            for column in columns:
                value = _nfc(row.get(column) or "")
                if not value:
                    continue
                pieces = [value, *value.split("|")] if column == "programmes" else [value]
                for piece in pieces:
                    piece = _nfc(piece)
                    if not piece:
                        continue
                    if URL_RE.search(piece) or piece in urls:
                        reasons.append(f"{filename} column {column} holds a URL")
                        break
                    structural = _structural(
                        column, piece, frame_codes=frame_codes, venue_ids=venue_ids, regions=regions
                    )
                    if structural:
                        continue
                    if piece in gy_ids or piece in ledger_ids:
                        reasons.append(f"{filename} column {column} holds an id")
                        break
                    if piece in names:
                        reasons.append(f"{filename} column {column} holds a name")
                        break
                    if piece in titles:
                        reasons.append(f"{filename} column {column} holds a title")
                        break
    return reasons


def _open_cv_leaks(
    tables: Mapping[str, tuple[list[str], list[dict[str, str]]]],
    secrets: Mapping[str, set[str]],
    roster_urls: set[str],
) -> list[str]:
    """Open files carry no CV row: no ``cv:`` origin, no CV-only URL, no title/origin column.

    An institution name that happens to equal an artwork title is not a CV
    row. The columns that may hold such a string are the venue name and the
    programme's own name and F2 sentence.
    """
    allowed_name_columns = {"name", "aliases", "name_ko", "name_en", "operators", "access_mode", "rules", "city"}
    reasons = []
    for filename, (columns, rows) in tables.items():
        forbidden = {"title", "title_norm", "origin", "activity_id", "source_id"} & set(columns)
        if forbidden and filename not in {"roster_facts.csv", "programmes.csv"}:
            reasons.append(f"open/{filename} has CV-row columns {sorted(forbidden)}")
        for row in rows:
            for column in columns:
                value = row.get(column) or ""
                if CV_ORIGIN_RE.search(value):
                    reasons.append(f"open/{filename} column {column} contains a cv: origin")
                if URL_RE.search(value):
                    for url in secrets["cv_urls"]:
                        if url and url in value and url not in roster_urls:
                            reasons.append(f"open/{filename} column {column} contains a CV URL")
                            break
                if column in allowed_name_columns or column in {
                    "source_url",
                    "frame_code",
                    "programme",
                    "code",
                    "year",
                    "period",
                    "generation",
                    "measure",
                    "activity_kind",
                    "venue_country",
                    "eligibility",
                    "kr_region",
                    "years_covered",
                    "n_people",
                    "n_rows",
                    "n_artists",
                    "share",
                    "suppressed",
                    "record_depth",
                    "kind",
                    "country",
                }:
                    continue
                if _nfc(value) in secrets["titles"]:
                    reasons.append(f"open/{filename} column {column} equals a CV or activity title")
    return reasons


def _hidden_leaks(text: str, privacy: object) -> list[str]:
    """Hidden ledger ids, gy ids and names as exact tokens. A gy id alone is a token here.

    The site publishes a hidden person's gy id as a tombstone. This deposit
    does not: the release is not the site, and the row is left out entirely.
    """
    if privacy.include_hidden:  # type: ignore[attr-defined]
        return ["release was built with hidden people included"]
    reasons = []
    ids = set(privacy.hidden_ids) | set(privacy.hidden_gy)  # type: ignore[attr-defined]
    for token in ids:
        if token and re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text):
            reasons.append("a hidden id is in the release")
            break
    for name in privacy.hidden_names:  # type: ignore[attr-defined]
        if name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text):
            reasons.append("a hidden name is in the release")
            break
    return reasons


def _assert_clear(reasons: Sequence[str], what: str) -> None:
    if reasons:
        shown = "; ".join(reasons[:8])
        extra = f" (+{len(reasons) - 8})" if len(reasons) > 8 else ""
        raise ReleaseError(f"{what}: {shown}{extra}")


def _ledger_version(config: Config) -> str:
    """Git hash of the private repository, when this tree has one. Empty when it does not."""
    here = config.root
    for directory in (here, *here.parents):
        git_dir = directory / ".git-private"
        if not git_dir.is_dir():
            continue
        try:
            done = subprocess.run(
                ["git", "--git-dir", str(git_dir), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip()
    return ""


def _evidence_stats(path: Path) -> dict | None:
    """Status counts from ``data/work/evidence/report.md``. The URL list is not copied."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    cited = re.search(r"cited URLs (\d+)", text)
    counts = {}
    for status, count in re.findall(r"^\| ([a-z_]+) \| (\d+) \|", text, flags=re.MULTILINE):
        counts[status] = int(count)
    if not counts and not cited:
        return {"parsed": False}
    return {"parsed": True, "cited_urls": int(cited.group(1)) if cited else None, "by_status": counts}


def _declared_stats(path: Path) -> list[dict[str, str]] | None:
    """Accepted programme-stated sizes. The collected count is not copied.

    That count is a group-by of ``roster_facts``. Writing it next to a small
    edition, suppressed or not, restates a margin the roster file already
    shows. The quote and the snapshot path are not copied.
    """
    if not path.is_file():
        return None
    out = []
    for row in read_csv(path):
        if (row.get("status") or "").strip() != "accepted":
            continue
        programme = (row.get("frame") or "").strip()
        edition = (row.get("edition") or "").strip()
        try:
            declared = int(row.get("size") or 0)
        except ValueError:
            continue
        out.append({"programme": programme, "edition": edition, "declared": declared})
    out.sort(key=lambda item: (item["programme"], item["edition"]))
    return out


def _md_table(text: str, heading: str) -> list[dict[str, str]]:
    """The first Markdown table after a heading line. Cells keep their text, pipes stripped."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index + 1
            break
    if start is None:
        return []
    table: list[str] = []
    for line in lines[start:]:
        if line.startswith("|"):
            table.append(line)
        elif table:
            break
    if len(table) < 2:
        return []
    header = [part.strip() for part in table[0].strip("|").split("|")]
    rows = []
    for line in table[2:]:
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) != len(header):
            continue
        rows.append(dict(zip(header, parts, strict=True)))
    return rows


def _disclose_count(value: str, k: int) -> str | int:
    try:
        n = int(value)
    except ValueError:
        return SUPPRESSED
    if n < k:
        return SUPPRESSED
    return n


def _external_note(stats: dict) -> str:
    """README sentence: the external-coverage block describes its own scan cohort, not this build.

    Why: the scan ran on an earlier version of the register, so its population and
    its probable count are not a share of the people in this build (final release
    audit, 2026-10-10).
    """
    ext = stats.get("external_coverage") or {}
    population = ext.get("population")
    if not population:
        return ""
    return (
        f" The `external_coverage` block in `stats.json` describes a scan of an earlier version of the"
        f" register ({population} people); its counts are not shares of the {stats.get('people')} people in this build."
    )


def _external_coverage(path: Path, k: int) -> dict | None:
    """Headline figures from the external-coverage note, when the file parses.

    A breakdown count under ``k``, or a complement under ``k``, is suppressed
    so the release does not publish a cell the research note left exact.
    Wilson intervals are omitted: they recover the exact fraction.
    """
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    population = re.search(
        r"Population: (\d+) published people\. Teams: (\d+)\. Individuals: (\d+)\. "
        r"Korean-named: (\d+)\. First year undated: (\d+)\.",
        text,
    )
    overall = re.search(r"Overall: ([0-9.]+) \[[^\]]+\] \((\d+)/(\d+)\)\.", text)
    if not population and not overall:
        return {"parsed": False}
    classes = []
    for row in _md_table(text, "## Class counts"):
        people = _disclose_count(row.get("People", ""), k)
        classes.append({"class": row.get("Class", ""), "people": people})

    # The first column's header differs by table. Read it from the row keys.
    def breakdown_first(heading: str) -> list[dict]:
        parsed = []
        for row in _md_table(text, heading):
            keys = list(row)
            if len(keys) < 3:
                continue
            label_key, people_key, probable_key = keys[0], keys[1], keys[2]
            try:
                people = int(row[people_key])
                probable = int(row[probable_key])
            except ValueError:
                continue
            complement = people - probable
            hide = people < k or probable < k or (0 < complement < k)
            parsed.append(
                {
                    "group": row[label_key],
                    "people": SUPPRESSED if people < k else people,
                    "probable": SUPPRESSED if hide else probable,
                }
            )
        return parsed
    return {
        "parsed": True,
        "source": "research/external-coverage/RESULTS.md",
        "population": int(population.group(1)) if population else None,
        "teams": int(population.group(2)) if population else None,
        "individuals": int(population.group(3)) if population else None,
        "korean_named": int(population.group(4)) if population else None,
        "first_year_undated": int(population.group(5)) if population else None,
        "probable_overall": (
            {"count": int(overall.group(2)), "of": int(overall.group(3)), "share": float(overall.group(1))}
            if overall
            else None
        ),
        "classes": classes,
        "by_team": breakdown_first("### By team"),
        "by_korean_name": breakdown_first("### By Korean name"),
        "by_programme": breakdown_first("### By programme"),
        "by_first_year": breakdown_first("### By first roster year"),
    }


def _kind_stats(kind_cells: dict[tuple, Cell], by_kind: dict[str, tuple[int, int]], k: int) -> dict[str, dict]:
    """Per-kind totals. A kind is suppressed when its people are under ``k`` or any year cell is unpublished.

    Publishing the exact kind total next to all but one year would recover that year (D5).
    """
    out = {}
    for kind, (people, rows) in sorted(by_kind.items()):
        if kind.startswith("__"):
            continue
        year_cells = [cell for key, cell in kind_cells.items() if key[0] == kind]
        unpublished = [cell for cell in year_cells if cell.status != "publish"]
        # One unpublished year is recoverable from the kind total. Two or more are not.
        if people < k or len(unpublished) == 1:
            out[kind] = {"n_people": SUPPRESSED, "n_rows": SUPPRESSED, "suppressed": "yes"}
            continue
        out[kind] = {
            "n_people": str(round_to_5(people)),
            "n_rows": str(round_to_5(rows)),
            "suppressed": "no",
        }
    return out


def _years_covered(roster: list[dict[str, str]]) -> dict[str, int | None]:
    """Roster edition years only. CV years are not part of the span."""
    years = [int(row["year"]) for row in roster if YEAR_RE.fullmatch(row.get("year") or "")]
    if not years:
        return {"min": None, "max": None}
    return {"min": min(years), "max": max(years)}


def _cv_disclosure_gap(kind_cells: Mapping[tuple, Cell]) -> tuple[int, int]:
    """Unpublished kind × year row mass, and the rounded sum of published cells.

    ``cv_rows`` stays exact. ``cv_rows_suppressed`` is the rows in cells that
    are not published. ``cv_rows_published_rounded`` is what the published
    ``n_rows`` cells add to. Rounding is why the two do not sum to ``cv_rows``.
    """
    suppressed = 0
    rounded = 0
    for cell in kind_cells.values():
        rows = cell.n_rows or 0
        if cell.status == "publish":
            rounded += round_to_5(rows)
        else:
            suppressed += rows
    return suppressed, rounded


def _find_external(config: Config) -> Path | None:
    """The research note is not part of the package. It is read when this archive has it."""
    for base in (config.root, config.root.parent, config.data.parent):
        path = base / "research" / "external-coverage" / "RESULTS.md"
        if path.is_file():
            return path
    return None


def _stats(
    *,
    population: _Population,
    roster: list[dict[str, str]],
    programmes: list[dict[str, str]],
    depths: dict[str, str],
    depth_rows: list[dict[str, str]],
    by_kind: dict[str, tuple[int, int]],
    kind_cells: dict[tuple, Cell],
    k: int,
    config: Config,
) -> dict:
    people = len(population.published)
    editions = len({(row["programme"], row["year"]) for row in roster if row.get("year")})
    cv_people, cv_rows = by_kind.get("__cv_people__", (0, 0))
    publish_people = people >= k
    cv_rows_suppressed, cv_rows_published_rounded = _cv_disclosure_gap(kind_cells)
    return {
        "people": people if publish_people else SUPPRESSED,
        "editions": editions,
        "programmes": len(programmes),
        "cv_people": cv_people if cv_people >= k else SUPPRESSED,
        "cv_rows": cv_rows if cv_rows >= k else SUPPRESSED,
        "cv_rows_suppressed": cv_rows_suppressed,
        "cv_rows_published_rounded": cv_rows_published_rounded,
        "cv_rows_by_kind": _kind_stats(kind_cells, by_kind, k),
        "years_covered": _years_covered(roster),
        "evidence_archive": _evidence_stats(config.work / "evidence" / "report.md"),
        "declared_roster_sizes": _declared_stats(config.work / "declared_roster_sizes.csv"),
        "external_coverage": _external_coverage(_find_external(config) or Path("/no/such/results.md"), k)
        if _find_external(config)
        else None,
        "record_depth": {row["record_depth"]: {"n_people": row["n_people"], "suppressed": row["suppressed"]} for row in depth_rows},
        "record_depth_levels_present": sorted(set(depths.values())),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _site_host(site_url: str) -> str:
    site = (site_url or "").rstrip("/")
    return site.split("://", 1)[1] if "://" in site else site


def _datasheet(stats: dict, settings: ReleaseSettings, version: str, ledger_version: str) -> str:
    people = stats["people"]
    licence = settings.licence
    commercial = settings.commercial_use
    names = "included" if settings.open_names else "omitted"
    site = settings.site_url
    host = _site_host(site)
    request = f"{site}/request" if site else ""
    commercial_sentence = (
        "Commercial use of the restricted record is granted by the configured agreement."
        if commercial
        else "Commercial use of the restricted record is not granted."
    )
    return f"""# Giye dataset {version}

## What this is

This deposit is a census of participation in Korean media art programmes.
It records who appeared on an admitted programme roster, when, and — in
aggregate — what kinds of activity were published about them. It is for
description and reuse. It does not rank people, recommend anyone, or decide
who should be invited.

There are two records. This folder is the open record, licensed CC BY 4.0
(`{licence}`). A second record holds person-level rows and is issued only
after a request is reviewed. The agreement for that record is `DUA.md` in
this folder.

People in this build: {people}. Editions: {stats["editions"]}. Roster frames: {stats["programmes"]}.
CV people: {stats["cv_people"]}. CV rows: {stats["cv_rows"]}.
Years covered: {stats["years_covered"]["min"]}–{stats["years_covered"]["max"]}.
Counts a paper cites are in `stats.json`.{_external_note(stats)}

## Who is included

A person is included when the public site would publish them: in scope, on an
admitted roster (`included` or `adjacent`), with an http(s) source and a
collection date, and with status empty, PUBLISHED or STAGED. People hidden
by request are in neither record. There is no cap on how many activities one
person has. The roster is the population. A CV adds detail. It does not
admit anyone.

The open roster (`roster_facts.csv`) keeps `gy_id`, the programme, the year,
the source URL and the collection date. Names are {names}. A source URL that
embeds a person's name is not kept: `source_withheld` is `yes`, the cell
carries the programme's own page (or is empty), and the per-person URL is
`roster_sources_withheld.csv` in the restricted record. The link from
`gy_id` to a name is `roster_names.csv` in the restricted record, and on
{host}, where a hidden person becomes a nameless tombstone.

## How it was collected

No participants were recruited and nobody was contacted. The rows are
information that programmes and artists published themselves: roster pages,
catalogues, press releases, and CVs the person published. robots.txt is
respected. Platforms whose terms forbid collection are not fetched. A fact
without a source URL and a collection date is not published.

## Files

| File | Record | What a row is |
|---|---|---|
| `roster_facts.csv` | open | One published person in one admitted roster edition. No name when names are omitted. `source_withheld` marks a per-person URL moved to the restricted record. |
| `programmes.csv` | open | One admitted programme: names, years, the F2 sentence, eligibility, source, operator credit. |
| `edition_year.csv` | open | First-timers by programme and year. Edition size is not a column value. |
| `entry_generation.csv` | open | Field-entry generation by programme. |
| `activity_kind_year.csv` | open | CV activity rows by kind and year. |
| `venue_country_period.csv` | open | Those rows by KR / abroad / unresolved and five-year period. |
| `record_depth.csv` | open | How full each published person's record is. |
| `institutions.csv` | open | Venues with at least {settings.k} people. The count is rounded to 5. |
| `stats.json` | open | Headline counts for a paper. |
| `people.csv` | restricted | One pseudonym per published person. No name and no `gy_id`. |
| `activities.csv` | restricted | Activity-channel rows. No title and no source URL. |
| `roster_names.csv` | restricted | `gy_id`, Korean name, English name, aliases. The exception to "no names". |
| `roster_sources_withheld.csv` | restricted | The per-person source URL removed from the open roster because it embeds a name. |

CSV files are UTF-8, with a header and no index column. Column names are
snake_case. Dates are `YYYY-MM-DD`. `CODEBOOK.md` defines every column.
`MANIFEST.json` gives the sha256 of every file in both records, the row
counts, the rule ids, `k`, and the ledger git hash.

Cells under {settings.k} people in the CV and record-depth tables are the
token `suppressed`, never 0. Published counts are rounded to the nearest 5.
A group that is at least 90% one value is `>=90` instead of an exact share.
First-timers and entry generation do not use that token: a cell under
{settings.k} is left out, and a larger cell is a rounded integer.
`publishable` of `no` is dropped from the open CV tables and from
`restricted/activities.csv`. A missing `publishable` is kept in both. It is
not treated as `no`.

## How to cite

Cite this record, not the software licence, when you use the tables. The
software that built them is AGPL-3.0-only and does not license the data.

> {settings.creator_family}, {settings.creator_given}. ({version[:4]}). Giye open census of the Korean media art field (version {version}) [Data set]. {site}

`CITATION.cff` is the same citation in Citation File Format. Add the Zenodo
DOI when the record has one. To cite the pipeline, use the software
repository at {settings.repository_url}.

## How to request the restricted record

Read `DUA.md` before you ask. Access is granted by the author after each
request is reviewed. There is no automatic approval and no embargo. Use is
for research. {commercial_sentence}

Send the request to {request}. Say who you are, the research
question, and that you have read the agreement.

## How to ask for a correction or a withdrawal

Corrections and withdrawals go to {request}. A person who is
hidden by request is removed from the next release. The `gy_id` is not
reused. An earlier Zenodo version cannot be edited in place, which is why
names are not in this open record.

## Further detail

The sections below follow the headings of a datasheet (Gebru et al.,
Datasheets for Datasets, doi:10.1145/3458723).

### Motivation

The census exists so later work can count participation, entry and recorded
activity without assembling the rosters again. It was built for a data paper
about the Korean media art field. It was not collected to train a model.

### Composition

`people.csv` and `activities.csv` carry no names and no published ids.
`roster_names.csv` is the exception: it pairs `gy_id` with names for people
who are still published. Background and hidden activity channels are not in
the restricted activity file. The open record does not contain CV rows.

### Preprocessing

Derived values come from `giye normalize` and are read from `data/processed/`.
This build does not edit the ledger. Entry year uses the earliest non-staff
roster year (R1–R2), in five-year bins anchored at 2000. Staff stay on the
roster-fact row and stay out of the entry tables.

A quantile is not published. If a suppressed cell in the CV or record-depth
tables could be recovered by subtraction, further cells are suppressed until
it cannot. If it still can, the build stops. A suppressed cell that equals a
staff-adjusted group-by of `roster_facts` also stops the build.

### Distribution

The open record is CC BY 4.0. The restricted record is not redistributed. It
goes to one recipient under `DUA.md`. The pseudonym key is not in either
record.

Ledger version recorded on this build: `{ledger_version or "not recorded"}`.

### Maintenance

An edition is collected when the programme publishes it. The maintainer
rebuilds with `giye release`. A new version is a new directory. Older
releases are not silently rewritten.
"""


def _codebook_rows() -> list[tuple[str, str, str, str, str]]:
    return [
        ("open/roster_facts.csv", "gy_id", "string", "published id", "Kept on the open roster, which omits names. Absent from people.csv and activities.csv. Present on roster_names.csv."),
        ("open/roster_facts.csv", "name_ko", "string", "open_names", "Omitted entirely, header included, when release.open_names is false."),
        ("open/roster_facts.csv", "name_en", "string", "open_names", "Same switch as name_ko."),
        ("open/roster_facts.csv", "frame_code", "string", "roster edition", "Membership code, usually PROGRAMME-YYYY."),
        ("open/roster_facts.csv", "programme", "string", "F1–F5", "Registry code of an admitted frame (included or adjacent)."),
        ("open/roster_facts.csv", "year", "string", "R1", "Edition year from the membership code. Empty when the code has none."),
        ("open/roster_facts.csv", "source_url", "string", "REL-URL", "The membership row's http(s) source, else the frame page. When that URL embeds a published name, the frame's http(s) page, or empty."),
        ("open/roster_facts.csv", "source_withheld", "string", "REL-URL", "yes when the per-person URL was withheld, else no. The withheld URL is restricted/roster_sources_withheld.csv."),
        ("open/roster_facts.csv", "collected_at", "string", "published_ids", "The membership row's collection date, else the person's."),
        ("open/programmes.csv", "code", "string", "frame code", "Admitted frames only. Excluded, planned and no_public_roster are absent."),
        ("open/programmes.csv", "name_ko", "string", "frames.yml", "Programme name as recorded."),
        ("open/programmes.csv", "name_en", "string", "frames.yml", "Programme name as recorded."),
        ("open/programmes.csv", "years_covered", "string", "frames.yml", "The frame's years_covered text, not a computed min–max."),
        ("open/programmes.csv", "access_mode", "string", "F2", "The recorded f2_cohort sentence. Not reclassified into a code."),
        ("open/programmes.csv", "eligibility", "string", "F1–F5", "included or adjacent."),
        ("open/programmes.csv", "source_url", "string", "frames.yml", "The programme's source page."),
        ("open/programmes.csv", "operators", "string", "G13", "name / role / source, joined by ` | `. Quote and snapshot path are not included."),
        ("open/edition_year.csv", "programme", "string", "F1–F5", "Registry code."),
        ("open/edition_year.csv", "year", "string", "R1", "Edition year, or undated."),
        ("open/edition_year.csv", "measure", "string", "D1", "first_timers only (first non-staff year in that programme). edition_size is not written."),
        ("open/edition_year.csv", "n_people", "string", "D4, D5", "Rounded to 5. A cell under k is omitted, so this is never suppressed and never 0."),
        ("open/edition_year.csv", "share", "string", "D3, D4", "Integer percent of the programme's entrants, including omitted cells, or >=90. Never suppressed."),
        ("open/edition_year.csv", "suppressed", "string", "D5", "no. A suppressed staff-adjusted cell would equal a roster_facts group-by, which fails the build."),
        ("open/entry_generation.csv", "programme", "string", "F1–F5", "A programme the person entered (non-staff edition)."),
        ("open/entry_generation.csv", "generation", "string", "R2", "GEN-<year> or GEN-UNDATED. Five-year bins anchored at 2000, from the field entry year."),
        ("open/entry_generation.csv", "n_people", "string", "D4, D5", "Distinct people, rounded to 5. A cell under k is omitted."),
        ("open/entry_generation.csv", "share", "string", "D3, D4", "Integer percent of that programme's entrants, including omitted cells, or >=90. Never suppressed."),
        ("open/entry_generation.csv", "suppressed", "string", "D5", "no. Same staff-adjusted rule as edition_year.csv."),
        ("open/activity_kind_year.csv", "activity_kind", "string", "K1", "Kind of a CV-origin activity row, or unresolved. publishable no is dropped. A missing publishable is kept, as in restricted/activities.csv."),
        ("open/activity_kind_year.csv", "year", "string", "K1", "Year of the CV row, or undated."),
        ("open/activity_kind_year.csv", "n_people", "string", "D1, D4", "Distinct people with such a row. The cell is suppressed when this is under k, even if n_rows is large."),
        ("open/activity_kind_year.csv", "n_rows", "string", "D4", "CV rows. Additive across kinds and years. Rounded, or suppressed with the cell."),
        ("open/activity_kind_year.csv", "share", "string", "D3, D4", "Share of CV rows in that year."),
        ("open/activity_kind_year.csv", "suppressed", "string", "D1, D5", "yes, no, or bound."),
        ("open/venue_country_period.csv", "venue_country", "string", "V1", "KR, abroad (a country other than KR), or unresolved (no country)."),
        ("open/venue_country_period.csv", "period", "string", "R2", "Five-year period anchored at 2000, for example 2020-2024, or undated."),
        ("open/venue_country_period.csv", "n_people", "string", "D1, D4", "Distinct people. Not additive across groups."),
        ("open/venue_country_period.csv", "n_rows", "string", "D4", "CV rows. Additive across the three groups and, with kind × year, across a period."),
        ("open/venue_country_period.csv", "share", "string", "D3, D4", "Share of CV rows in that period."),
        ("open/venue_country_period.csv", "suppressed", "string", "D1, D5", "yes, no, or bound."),
        ("open/record_depth.csv", "record_depth", "string", "P6", "1 roster only, 2 practice trace, 3 link or active CV source, 4 standing CV extraction."),
        ("open/record_depth.csv", "n_people", "string", "D1, D4", "The levels partition published people."),
        ("open/record_depth.csv", "share", "string", "D3, D4", "Share of published people."),
        ("open/record_depth.csv", "suppressed", "string", "D1, D5", "yes, no, or bound. The people total is a published margin, so a single suppressed level forces another level to be suppressed."),
        ("open/institutions.csv", "venue_id", "string", "P3", "Kept only when unrounded n_artists is at least k."),
        ("open/institutions.csv", "name", "string", "P3", "Display name of the venue entity."),
        ("open/institutions.csv", "aliases", "string", "P3", "Pipe-separated aliases from venues.csv."),
        ("open/institutions.csv", "kind", "string", "P3", "institution, funder, or another venue kind."),
        ("open/institutions.csv", "city", "string", "V1", "City, when the entity has one."),
        ("open/institutions.csv", "country", "string", "V1", "Country code, when the entity has one."),
        ("open/institutions.csv", "kr_region", "string", "G1–G6", "Korean region, when the entity has one."),
        ("open/institutions.csv", "n_artists", "string", "D4", "Distinct people, rounded to 5. The threshold uses the unrounded count."),
        ("open/institutions.csv", "rules", "string", "V1–V12, G7–G13", "Rule ids that built the entity, as venues.csv records them."),
        ("restricted/roster_names.csv", "gy_id", "string", "published id", "The open roster's id. One row per published person. Hidden people are absent."),
        ("restricted/roster_names.csv", "name_ko", "string", "artists.csv", "Korean name as recorded. Not in the open record when open_names is false."),
        ("restricted/roster_names.csv", "name_en", "string", "artists.csv", "English name as recorded."),
        ("restricted/roster_names.csv", "aliases", "string", "artists.csv", "Pipe-separated aliases. No pseudonym column."),
        ("restricted/roster_sources_withheld.csv", "gy_id", "string", "published id", "The open roster's id for a row whose per-person source URL was withheld."),
        ("restricted/roster_sources_withheld.csv", "frame_code", "string", "roster edition", "The membership code the withheld URL belonged to."),
        ("restricted/roster_sources_withheld.csv", "source_url", "string", "REL-URL", "The per-person http(s) source removed from the open roster because it embeds a published name."),
        ("restricted/people.csv", "pseudonym", "string", "release.pseudonym", "ps plus 32 hex characters. Random per release, or HMAC-SHA256 of the ledger id when stable_hmac."),
        ("restricted/people.csv", "entry_year", "string", "R1", "Earliest non-staff roster year. Empty when none is dated. No name, no gy_id."),
        ("restricted/people.csv", "entry_generation", "string", "R2", "GEN-<year> or GEN-UNDATED."),
        ("restricted/people.csv", "record_depth", "string", "P6", "1–4."),
        ("restricted/people.csv", "programmes", "string", "F1–F5", "Admitted programme codes, sorted, joined by `|`."),
        ("restricted/people.csv", "team", "string", "T1", "1 when the roster row is a team or group, else 0."),
        ("restricted/activities.csv", "pseudonym", "string", "release.pseudonym", "Same token as people.csv."),
        ("restricted/activities.csv", "year", "string", "K1", "Four-digit year, or empty."),
        ("restricted/activities.csv", "activity_kind", "string", "K1", "Kind. The file has only channel activity, so education, employment and the other off-list kinds appear only when K1 left them on that channel."),
        ("restricted/activities.csv", "activity_channel", "string", "K1", "Always activity. Background, hidden, and publishable no are not written. A missing publishable is kept."),
        ("restricted/activities.csv", "venue_id", "string", "P3, D1", "Empty unless the venue is in institutions.csv."),
        ("restricted/activities.csv", "venue_country", "string", "V1", "Country code, or empty."),
        ("restricted/activities.csv", "venue_region", "string", "G1–G6", "Region, or empty."),
        ("restricted/activities.csv", "venue_kind", "string", "P3", "Kind of the venue entity, or empty."),
        ("restricted/activities.csv", "funder_id", "string", "P3, D1", "Empty unless that funder entity is in institutions.csv."),
        ("restricted/activities.csv", "event_link", "string", "P4", "Frame or edition code, or empty. Not a URL."),
        ("restricted/activities.csv", "origin_type", "string", "K1", "cv or roster. The source id inside a cv: origin is not copied."),
    ]


def _codebook_text(rows: Sequence[tuple[str, str, str, str, str]], preamble: str) -> str:
    lines = [
        "# Codebook",
        "",
        preamble.rstrip(),
        "",
        "Types are the types of the cells. A count cell is a string because a CV or",
        "record-depth cell may be `suppressed` or `>=90` rather than a number.",
        "Rule ids point at docs/RULES.md and, for D1–D5 and R1–R2, docs/CAREER.md.",
        "",
        "Open CV tables (`activity_kind_year.csv`, `venue_country_period.csv`) and",
        "`restricted/activities.csv` use one `publishable` rule. A row whose",
        "`publishable` is `no` is dropped from all three. A missing or blank",
        "`publishable` is not treated as `no` and is kept in all three.",
        "",
        "| File | Column | Type | Rule | Meaning |",
        "|---|---|---|---|---|",
    ]
    for file, column, kind, rule, meaning in rows:
        lines.append(f"| `{file}` | `{column}` | {kind} | {rule} | {meaning} |")
    lines.append("")
    lines.append("D2 is not a column. No table publishes a quantile, a minimum or a maximum.")
    lines.append("A quantile would be written only when `quantile_allowed` is true ")
    lines.append("(at least k people on each side; the median needs 20 people at k = 10).")
    lines.append("")
    return "\n".join(lines)


def _codebook() -> str:
    return _codebook_text(
        _codebook_rows(),
        "Every column written by `giye release`.",
    )


def _restricted_codebook() -> str:
    rows = [row for row in _codebook_rows() if row[0].startswith("restricted/")]
    return _codebook_text(
        rows,
        "Column definitions for the open record are in that record's `CODEBOOK.md`. "
        "This file repeats the columns of the restricted files.",
    )


def _dua(settings: ReleaseSettings) -> str:
    commercial = settings.commercial_use
    host = _site_host(settings.site_url)
    commercial_clause = (
        "Commercial use is allowed under this agreement."
        if commercial
        else "Commercial use is not allowed."
    )
    return f"""# Data-use agreement

This agreement covers the restricted record (`restricted/`). It is the text
a requester reads before asking. It becomes binding when the author accepts
the request and the recipient receives the files.

The open record is a separate deposit. It is licensed CC BY 4.0.

By receiving the restricted files you agree:

1. Research use. You will use the files for the research you named in the request, and for work that reports that research.
2. No re-identification. You will not try to find out who a pseudonym is, and you will not try to link a row back to a person.
3. No linkage to identify people. You will not join these files to another dataset in order to identify a person. Joining to the open institution list on `venue_id` or `funder_id`, and to the open programme list on a programme code, is the use those columns were built for and is not identification of a person.
4. No redistribution. You will not publish the files, share them, or deposit them. You may publish aggregates that themselves satisfy the disclosure rules in the codebook (k = {settings.k}, no cell under k, counts rounded to 5).
5. Citation. You will cite the dataset as the manifest and the data paper name it.
6. Deletion on request. If Giye asks you to delete the files, or a person asks through {host}/request and Giye passes that on, you will delete your copies, including backups you control.
7. Contact. Requests and questions go to {host}/request.

{commercial_clause}

The files contain career profiles of living people, assembled from many published facts.
That is why they are not in the open deposit. No participants were recruited and
nobody was contacted. The information was published by programmes and by the people themselves.

The author reviews every access request. There is no automatic approval and no embargo.
"""


def _column_index() -> dict[str, set[str]]:
    """Columns the codebook claims, so a new field cannot ship undocumented."""
    found: dict[str, set[str]] = defaultdict(set)
    for line in _codebook().splitlines():
        if not line.startswith("| `"):
            continue
        parts = [part.strip(" `") for part in line.strip("|").split("|")]
        if len(parts) >= 2 and parts[0].endswith(".csv"):
            found[parts[0]].add(parts[1])
    return found


def _check_codebook(written: Mapping[str, list[str]]) -> None:
    documented = _column_index()
    for name, fields in written.items():
        missing = [field_name for field_name in fields if field_name not in documented.get(name, set())]
        if missing:
            raise ReleaseError(f"CODEBOOK.md does not document {name}: {', '.join(missing)}")


def _count_rows(path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as handle:
        return max(sum(1 for _ in csv.reader(handle)) - 1, 0)


def _license_notice(version: str, settings: ReleaseSettings) -> str:
    """Short CC BY 4.0 notice. The legal code stays at the URL; it is not copied here."""
    who = f"{settings.creator_given} {settings.creator_family}".strip()
    return (
        f"Giye open census tables, version {version}, by {who}.\n"
        "\n"
        "This work is licensed under the Creative Commons Attribution 4.0 International License.\n"
        "\n"
        "To view a copy of this license, visit https://creativecommons.org/licenses/by/4.0/legalcode\n"
    )


def _cff_plain(value: str) -> str:
    """An unquoted Citation File Format scalar when the value is a single token."""
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", value or ""):
        return value
    return json.dumps(value, ensure_ascii=False)


def _citation_cff(version: str, settings: ReleaseSettings) -> str:
    """Citation File Format for the open record. An empty ORCID is omitted."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", version):
        released = version
    else:
        released = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    orcid = f'\n    orcid: "{settings.creator_orcid}"' if settings.creator_orcid else ""
    return f"""cff-version: 1.2.0
message: "If you use this dataset, please cite it as below."
type: dataset
title: "Giye open census of the Korean media art field"
version: "{version}"
date-released: {released}
license: CC-BY-4.0
url: "{settings.site_url}"
repository-code: "{settings.repository_url}"
authors:
  - family-names: {_cff_plain(settings.creator_family)}
    given-names: {_cff_plain(settings.creator_given)}
    affiliation: {_cff_plain(settings.creator_affiliation)}{orcid}
keywords:
  - Korean media art
  - census
  - cultural programmes
  - provenance
"""


def _restricted_readme(version: str, settings: ReleaseSettings) -> str:
    request = f"{settings.site_url}/request" if settings.site_url else ""
    return f"""# Giye restricted record {version}

This record holds person-level rows from the Giye census.

- `people.csv` — one pseudonym per published person, with entry year, entry generation, record depth, programme codes and a team flag. No names and no `gy_id`.
- `activities.csv` — activity-channel rows: year, kind, institution ids when the institution is in the open list, and whether the row came from a CV or a roster. No titles and no source URLs.
- `roster_names.csv` — `gy_id`, `name_ko`, `name_en` and aliases for published people. This is the file that pairs the public id with a name. People hidden by request are not listed.
- `roster_sources_withheld.csv` — `gy_id`, `frame_code` and the per-person `source_url` removed from the open roster because the URL embeds a published name. The open row keeps the programme page instead, with `source_withheld` = yes.

A row whose `publishable` is `no` is omitted from `activities.csv`. A missing `publishable` is kept. The open CV tables use that same rule.

Access is granted by the author after reviewing each request. There is no automatic approval and no embargo. Use is for research. Commercial use is not allowed. The data-use agreement in the open record (`DUA.md`) governs this record. Read it before you ask. Requests go to {request}.

`CODEBOOK.md` in this folder repeats the restricted columns. The open record's codebook covers both records. `MANIFEST.json` is the same file as in the open record.
"""


_OPEN_DOI = "10.5281/zenodo.TODO-open"
_RESTRICTED_DOI = "10.5281/zenodo.TODO-restricted"


def _zenodo_records(version: str, settings: ReleaseSettings) -> tuple[dict, dict]:
    """Deposit metadata for the two Zenodo records. DOIs stay placeholders.

    An empty ``creator_orcid`` is written as ``TODO``, the same placeholder the
    deposit used before an identifier is configured. The descriptions are
    written for the repository. A data paper has to paraphrase them; copying
    either text into the paper is not allowed.
    """
    creators = [
        {
            "name": f"{settings.creator_family}, {settings.creator_given}",
            "affiliation": settings.creator_affiliation,
            "orcid": settings.creator_orcid or "TODO",
        }
    ]
    software = {
        "identifier": settings.repository_url,
        "relation": "isSupplementTo",
        "scheme": "url",
        "resource_type": "software",
    }
    site = {"identifier": settings.site_url, "relation": "isDocumentedBy", "scheme": "url"}
    notes = (
        "Dataset languages: eng/kor (English column names and documentation; Korean names in the "
        "restricted record and in programme titles). Replace creators[0].orcid and the placeholder "
        "DOIs before upload. Do not copy this description into a Journal of Open Humanities Data "
        "paper; paraphrase it."
    )
    open_record = {
        "upload_type": "dataset",
        "publication_date": version if re.fullmatch(r"\d{4}-\d{2}-\d{2}", version) else "",
        "title": "Giye open census tables of Korean media art programmes",
        "creators": creators,
        "description": (
            "Participation tables for programmes in the Korean media-art field. "
            "The roster file lists a public person id, a programme, a year, a source URL and a "
            "collection date, and it does not list personal names. Summary tables cover first "
            "entry, activity kind, country of activity and how complete each record is, after "
            "small cells are removed or rounded. Institutions above the same size threshold are "
            "included. Person-level rows and the name list are a separate record."
        ),
        "keywords": ["Korean media art", "census", "cultural programmes", "roster", "provenance"],
        "license": "cc-by-4.0",
        "access_right": "open",
        "language": "eng",
        "version": version,
        "related_identifiers": [
            software,
            site,
            {"identifier": _RESTRICTED_DOI, "relation": "references", "scheme": "doi"},
        ],
        "notes": notes,
    }
    if not open_record["publication_date"]:
        del open_record["publication_date"]
    restricted = {
        "upload_type": "dataset",
        "title": "Giye restricted person-level rows for the Korean media art census",
        "creators": creators,
        "description": (
            "Person-level companion tables for the Giye census. One file is a pseudonym per "
            "published person. One file is activity-channel rows without titles or source URLs. "
            "One file links the public person id to names. The author reads every access request "
            "before granting it. Research use only. Commercial use is excluded. The signed "
            "data-use agreement in the open record applies."
        ),
        "keywords": ["Korean media art", "career records", "pseudonymised data", "restricted access"],
        "access_right": "restricted",
        "access_conditions": (
            "The author reviews every request. Access is for research use only. "
            "Commercial use is not allowed. A signed data-use agreement is required."
        ),
        "language": "eng",
        "version": version,
        "related_identifiers": [
            software,
            site,
            {"identifier": _OPEN_DOI, "relation": "references", "scheme": "doi"},
        ],
        "notes": notes,
    }
    return open_record, restricted


@dataclass
class ReleaseResult:
    """Paths and the stats object of one successful build."""

    output: Path
    key_path: Path
    stats: dict
    files: list[tuple[str, int | None]] = field(default_factory=list)


def build_release(
    config: Config,
    version: str,
    *,
    rng: random.Random | None = None,
) -> ReleaseResult:
    """Write ``<data>/release/<version>/`` and the private key map.

    ``rng`` is for tests. Without it, ``per_release`` draws from ``secrets`` and
    two builds do not reuse pseudonyms. Aggregates do not use the pseudonym, so
    they stay the same. The build writes into a temporary directory and keeps
    it only after the checks pass.
    """
    if not VERSION_RE.fullmatch(version or ""):
        raise ReleaseError("version must be a short token of letters, digits, '.', '_' or '-'")
    settings = config.release
    output = (config.data / "release" / version).resolve()
    key_path = (config.work / "release" / f"{version}_key.csv").resolve()
    if _is_inside(key_path, output):
        raise ReleaseError("the pseudonym key would be written inside the release directory")
    if not (config.processed / "activities.csv").is_file():
        raise ReleaseError(f"processed activities not found ({config.processed / 'activities.csv'}); run giye normalize")

    population, privacy = _load_population(config)
    k = settings.k
    publish_people_total = len(population.published) >= k
    depths = _depths(config, population)
    if set(depths) != population.published:
        raise ReleaseError("record depth does not cover every published person")
    roster_fields, roster, withheld_sources = _roster_facts(population, open_names=settings.open_names)
    programme_fields, programmes = _programmes(population, config)
    edition_rows, generation_rows = _entrant_tables(population, k)
    kind_rows, country_rows, kind_cells, _country_cells, by_kind = _cv_tables(config, population, k)
    depth_rows, _depth_cells = _depth_table(depths, k, publish_total=publish_people_total)
    institution_fields, institutions, institution_ids = _institutions(config, k)
    pseudonym = _pseudonyms(
        sorted(population.published),
        mode=settings.pseudonym,
        secret=settings.pseudonym_secret,
        output=output,
        rng=rng,
    )
    people = _restricted_people(population, pseudonym, depths, config)
    activities = _restricted_activities(config, population, pseudonym, institution_ids)
    roster_names = _restricted_roster_names(population)
    _check_roster_names(roster_names, population)

    edition_fields = ["programme", "year", "measure", "n_people", "share", "suppressed"]
    generation_fields = ["programme", "generation", "n_people", "share", "suppressed"]
    kind_fields = ["activity_kind", "year", "n_people", "n_rows", "share", "suppressed"]
    country_fields = ["venue_country", "period", "n_people", "n_rows", "share", "suppressed"]
    depth_fields = ["record_depth", "n_people", "share", "suppressed"]
    people_fields = ["pseudonym", "entry_year", "entry_generation", "record_depth", "programmes", "team"]
    activity_fields = [
        "pseudonym",
        "year",
        "activity_kind",
        "activity_channel",
        "venue_id",
        "venue_country",
        "venue_region",
        "venue_kind",
        "funder_id",
        "event_link",
        "origin_type",
    ]
    for fields in (edition_fields, generation_fields, kind_fields, country_fields, depth_fields):
        bad = AGGREGATE_FORBIDDEN & set(fields)
        if bad:
            raise ReleaseError(f"D5: an aggregate column could hold an identifier: {', '.join(sorted(bad))}")

    secrets = _ledger_secrets(config)
    frame_codes = set(population.admitted_codes)
    for row in roster:
        if row.get("frame_code"):
            frame_codes.add(row["frame_code"])
        if row.get("programme"):
            frame_codes.add(row["programme"])
    regions = {row["venue_region"] for row in activities if row.get("venue_region")}
    _assert_clear(
        restricted_leaks(
            {
                "people.csv": (people_fields, people),
                "activities.csv": (activity_fields, activities),
            },
            secrets,
            frame_codes=frame_codes,
            venue_ids=institution_ids,
            regions=regions,
        ),
        "restricted tier",
    )
    roster_urls = {row["source_url"] for row in roster if row.get("source_url")}
    roster_urls.update(row["source_url"] for row in programmes if row.get("source_url"))
    open_tables = {
        "roster_facts.csv": (roster_fields, roster),
        "programmes.csv": (programme_fields, programmes),
        "edition_year.csv": (edition_fields, edition_rows),
        "entry_generation.csv": (generation_fields, generation_rows),
        "activity_kind_year.csv": (kind_fields, kind_rows),
        "venue_country_period.csv": (country_fields, country_rows),
        "record_depth.csv": (depth_fields, depth_rows),
        "institutions.csv": (institution_fields, institutions),
    }
    _assert_clear(_open_cv_leaks(open_tables, secrets, roster_urls), "open tier")
    _check_codebook(
        {
            "open/roster_facts.csv": roster_fields,
            "open/programmes.csv": programme_fields,
            "open/edition_year.csv": edition_fields,
            "open/entry_generation.csv": generation_fields,
            "open/activity_kind_year.csv": kind_fields,
            "open/venue_country_period.csv": country_fields,
            "open/record_depth.csv": depth_fields,
            "open/institutions.csv": institution_fields,
            "restricted/people.csv": people_fields,
            "restricted/activities.csv": activity_fields,
            "restricted/roster_names.csv": ROSTER_NAME_FIELDS,
            "restricted/roster_sources_withheld.csv": ["gy_id", "frame_code", "source_url"],
        }
    )
    stats = _stats(
        population=population,
        roster=roster,
        programmes=programmes,
        depths=depths,
        depth_rows=depth_rows,
        by_kind=by_kind,
        kind_cells=kind_cells,
        k=k,
        config=config,
    )
    ledger_version = _ledger_version(config)
    open_texts = {
        "README.md": _datasheet(stats, settings, version, ledger_version),
        "LICENSE": _license_notice(version, settings),
        "CITATION.cff": _citation_cff(version, settings),
        "CODEBOOK.md": _codebook(),
        "DUA.md": _dua(settings),
    }
    restricted_texts = {
        "README.md": _restricted_readme(version, settings),
        "CODEBOOK.md": _restricted_codebook(),
    }
    open_zenodo, restricted_zenodo = _zenodo_records(version, settings)
    published_name_tokens: set[str] = set()
    for ledger_id in population.published:
        published_name_tokens |= _person_name_tokens(population.artists[ledger_id])
    _assert_clear(
        open_name_leaks(
            open_tables,
            {**open_texts, "stats.json": json.dumps(stats, ensure_ascii=False)},
            published_name_tokens,
            creator_given=settings.creator_given,
            creator_family=settings.creator_family,
            repository_url=settings.repository_url,
        ),
        "open names",
    )
    blob = "\n".join(open_texts.values()) + "\n".join(restricted_texts.values())
    blob += json.dumps(stats, ensure_ascii=False)
    blob += json.dumps(open_zenodo, ensure_ascii=False) + json.dumps(restricted_zenodo, ensure_ascii=False)
    for fields, rows in open_tables.values():
        blob += "\n".join(",".join(row.get(column, "") for column in fields) for row in rows)
    blob += "\n".join(",".join(row.get(column, "") for column in people_fields) for row in people)
    blob += "\n".join(",".join(row.get(column, "") for column in ROSTER_NAME_FIELDS) for row in roster_names)
    # Activities are large. Scan them from the row objects, not one giant string, for hidden tokens.
    _assert_clear(_hidden_leaks(blob, privacy), "release text")
    # Chunk so a large activity table is still scanned for hidden tokens.
    hidden_buf: list[str] = []
    for row in activities:
        hidden_buf.append(",".join(row.get(column, "") for column in activity_fields))
        if len(hidden_buf) >= 5000:
            _assert_clear(_hidden_leaks("\n".join(hidden_buf), privacy), "restricted activities")
            hidden_buf = []
    if hidden_buf:
        _assert_clear(_hidden_leaks("\n".join(hidden_buf), privacy), "restricted activities")

    tmp = output.parent / f".{version}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        (tmp / "open").mkdir()
        (tmp / "restricted").mkdir()
        (tmp / "zenodo").mkdir()
        written = {
            "open/roster_facts.csv": (roster_fields, roster),
            "open/programmes.csv": (programme_fields, programmes),
            "open/edition_year.csv": (edition_fields, edition_rows),
            "open/entry_generation.csv": (generation_fields, generation_rows),
            "open/activity_kind_year.csv": (kind_fields, kind_rows),
            "open/venue_country_period.csv": (country_fields, country_rows),
            "open/record_depth.csv": (depth_fields, depth_rows),
            "open/institutions.csv": (institution_fields, institutions),
            "restricted/people.csv": (people_fields, people),
            "restricted/activities.csv": (activity_fields, activities),
            "restricted/roster_names.csv": (ROSTER_NAME_FIELDS, roster_names),
            "restricted/roster_sources_withheld.csv": (["gy_id", "frame_code", "source_url"], withheld_sources),
        }
        for name, (fields, rows) in written.items():
            write_csv(path=tmp / name, fields=fields, rows=rows)
        for name, text in open_texts.items():
            write_text_atomic(tmp / "open" / name, text)
        for name, text in restricted_texts.items():
            write_text_atomic(tmp / "restricted" / name, text)
        # The paper's number source lives in the open record so the deposit carries it.
        _write_json(tmp / "open" / "stats.json", stats)
        _write_json(tmp / "zenodo" / "open.zenodo.json", open_zenodo)
        _write_json(tmp / "zenodo" / "restricted.zenodo.json", restricted_zenodo)
        manifest_files = {}
        for path in sorted(tmp.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(tmp).as_posix()
            # zenodo/ is deposit metadata, not a file of either record.
            if relative.startswith("zenodo/"):
                continue
            entry: dict[str, object] = {"sha256": _sha256(path)}
            if path.suffix == ".csv":
                entry["rows"] = _count_rows(path)
            manifest_files[relative] = entry
        manifest = {
            "version": version,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ledger_version": ledger_version,
            "rule_ids": list(RULE_IDS),
            "k": k,
            "licence": settings.licence,
            "open_names": settings.open_names,
            "pseudonym": settings.pseudonym,
            "commercial_use": settings.commercial_use,
            "note": "sha256 covers every file in open/ and restricted/ except the two copies of this manifest.",
            "counts": {name: manifest_files[name]["rows"] for name in manifest_files if name.endswith(".csv")},
            "files": manifest_files,
        }
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        write_text_atomic(tmp / "open" / "MANIFEST.json", manifest_text)
        write_text_atomic(tmp / "restricted" / "MANIFEST.json", manifest_text)
        release_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(tmp.rglob("*"))
            if path.is_file() and path.suffix != ".csv"
        )
        _assert_clear(_hidden_leaks(release_text, privacy), "release documents")
        if output.exists():
            shutil.rmtree(output)
        tmp.rename(output)
    except Exception:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    key_rows = [
        {"ledger_id": ledger_id, "gy_id": population.artists[ledger_id].get("gy_id") or "", "pseudonym": pseudonym[ledger_id]}
        for ledger_id in sorted(population.published)
    ]
    write_csv(path=key_path, fields=["ledger_id", "gy_id", "pseudonym"], rows=key_rows)
    if _is_inside(key_path, output):
        raise ReleaseError("the pseudonym key was written inside the release directory")
    files = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            rows_n = _count_rows(path) if path.suffix == ".csv" else None
            files.append((path.relative_to(output).as_posix(), rows_n))
    return ReleaseResult(output=output, key_path=key_path, stats=stats, files=files)
