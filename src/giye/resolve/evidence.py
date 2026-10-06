# SPDX-License-Identifier: AGPL-3.0-only
"""Same-person evidence (rules E1–E4).

Two rows are merged only when one of these holds. A shared name, a romanized
spelling, or a report is not evidence. Thresholds and patterns are fixed here
and in the field file (see docs/RULES.md), so two runs of the same ledger agree.

E1. The same personal website. The key is the host, or host plus path on a
shared platform, so two accounts on one host stay apart.

E2. One row's CV names the event on the other's roster in that edition's year,
give or take one year. An edition both rows are on is not used. The event regex comes from the field file
(``[resolve.events]``) plus ``[resolve.event_patterns]`` in the archive config.
A prefix in the config replaces the field file's pattern for that prefix.

E3. A work title in brackets (``〈…〉``, ``<…>``, ``《…》``, and the same family)
appears on both roster rows, or on one roster row and the other's CV, in the
same year give or take one year. The normalised title is at least 3 characters.
The event name itself is E2's business, so an unbracketed title does not count.
A title many records use does not identify one work: when its base (the
normalised title without a trailing number, ``Untitled #3`` → ``untitled``) is
credited to or listed by at least ``[resolve] generic_title_records`` distinct
records across the ledger, or the base is shorter than 2 characters, it is not
E3 evidence (:func:`generic_titles`).

E4. Both roster rows credit the same team in the role, written with the field
file's team prefix (default ``팀: <name>``). The normalised team name is
at least 2 characters.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

# E2. The CV year and the roster edition year may differ by at most this much.
YEAR_WINDOW = 1

# Hosts where the path distinguishes two people. A personal domain compares the host only.
SHARED_HOSTS = (
    "blog.naver.com",
    "bandcamp.com",
    "notion.site",
    "notion.so",
    "linktr.ee",
    "github.io",
    "wixsite.com",
    "cargo.site",
    "cargocollective.com",
    "myportfolio.com",
    "weebly.com",
    "blogspot.com",
    "sites.google.com",
    "drive.google.com",
    "docs.google.com",
    "tistory.com",
    "behance.net",
    "vimeo.com",
    "youtube.com",
)

# E3. Only a bracketed title is a work. The event name around it is E2.
BRACKETED = re.compile(r"[<〈《‹「]([^>〉》›」]{2,})[>〉》›」]")
_WORK_MIN = 3
_TEAM_MIN = 2
# E3. A title base shorter than this names no work (``〈2019〉``, ``〈A 3〉``).
_WORK_BASE_MIN = 2
# E3. Default of ``[resolve] generic_title_records``: a title base used by this many
# distinct records or more is not E3 evidence. Chosen from the production ledger
# (2026-10-06, docs/RULES.md E3). The pair E3 would join is itself two records.
# Of the 145 bases rosters credit as works, every one used by 4 or more records is
# an exhibition, programme or forum title that a whole group roster credits, or a
# team work credited with the team prefix (E4 covers it). At exactly 3 the set is
# mixed: common words ("flow", "summer") beside specific works credited to three
# makers, one of them with no team credit, so E4 cannot stand in. A threshold of 3
# would also refuse a duplicate pair whenever one unrelated record shares a
# specific title, as in the demo (〈푸른 신호〉). Exactly 2 is always the pair.
GENERIC_TITLE_RECORDS = 4


def pattern_table(*layers: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """Merge pattern tables. A later layer replaces a key and keeps its position.

    The field file is the first layer. ``[resolve.event_patterns]`` is the next,
    so a configured prefix overrides the field without reordering the rest.
    """
    table: dict[str, str] = {}
    for layer in layers:
        for key, pattern in layer:
            table[key] = pattern
    return table


def event_pattern(frame_code: str, patterns: Mapping[str, str]) -> str | None:
    """Regex for a frame code, or None when the code names no known event (E2)."""
    base = re.sub(r"-\d{4}$", "", frame_code or "")
    for key, pattern in patterns.items():
        if base.startswith(key):
            return pattern
    return None


def url_key(url: str) -> str:
    """Identity of a website (E1).

    A personal domain is the host, so ``https://www.artist.example.org/cv`` and
    ``http://artist.example.org/`` are one site. On a shared platform the path
    stays, so two accounts on that host are not one site. A trailing index page
    is removed only on those platforms.
    """
    text = re.sub(r"^https?://(www\.)?", "", (url or "").strip().lower())
    text = text.split("?")[0].split("#")[0]
    host, _, path = text.partition("/")
    if any(host == item or host.endswith("." + item) for item in SHARED_HOSTS):
        path = re.sub(r"(index\.(html?|php))$", "", path).strip("/")
        return f"{host}/{path}"
    return host


def website_keys(links: list[dict]) -> dict[str, set[str]]:
    """Ledger id → website keys. Social links are not a personal site (E1)."""
    sites: dict[str, set[str]] = defaultdict(set)
    for link in links:
        if link.get("link_type") == "social":
            continue
        key = url_key(link.get("url") or "")
        if key:
            sites[link["ledger_id"]].add(key)
    return sites


def evidence_e1(left: str, right: str, sites: Mapping[str, set[str]]) -> str | None:
    """E1 when the two rows share a website key. The key is the evidence."""
    both = sorted(sites.get(left, set()) & sites.get(right, set()))
    if both:
        return f"E1 same website {both[0]}"
    return None


def norm_title(title: str) -> str:
    """Work or team title with punctuation and spaces removed, lower-cased."""
    return re.sub(r"[\W_]+", "", (title or "").lower())


def _year(value: object) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    return int(text)


def edition_years(frame_code: str, rows: list[dict]) -> list[int]:
    """Years of one roster edition (E2).

    A frame code that ends in ``-YYYY`` uses that year and ignores activity
    years, which is how an edition is encoded (``EXAMPLE-2014``). A frame code
    with no year uses the years on that frame's roster activities. CV rows are
    not roster appearances.
    """
    match = re.search(r"(\d{4})$", frame_code or "")
    if match:
        return [int(match.group(1))]
    base = re.sub(r"-\d{4}$", "", frame_code or "")
    years: list[int] = []
    for row in rows:
        origin = row.get("origin") or ""
        if origin.startswith("cv:"):
            continue
        if origin and origin != frame_code and origin != base:
            continue
        year = _year(row.get("year"))
        if year is not None:
            years.append(year)
    return years


def cv_mentions(cv_rows: list[dict], frame_code: str, years: list[int], patterns: Mapping[str, str]) -> str | None:
    """E2 hit: a CV line names the event in one of ``years``, ± ``YEAR_WINDOW``."""
    pattern = event_pattern(frame_code, patterns)
    if not pattern or not years:
        return None
    for activity in cv_rows:
        text = f"{activity.get('title', '')} {activity.get('venue', '')}"
        year = _year(activity.get("year"))
        if year is None or not re.search(pattern, text, re.IGNORECASE):
            continue
        if any(abs(year - edition) <= YEAR_WINDOW for edition in years):
            return f"{year} {text[:80]}"
    return None


def title_base(key: str) -> str:
    """A normalised title without its trailing number (E3).

    ``norm_title`` has already dropped ``#``, brackets and spaces, so
    ``Untitled #3``, ``Untitled 3`` and ``Untitled (2019)`` all end in digits
    and share the base ``untitled``. A series is one title for counting use.
    """
    return re.sub(r"\d+$", "", key)


def _row_titles(title: str, role: str = "") -> set[str]:
    """Title bases one ledger or CV row names: its bracketed titles, else its whole title."""
    found = BRACKETED.findall(f"{title} {role}")
    if not found:
        found = [title]
    return {title_base(norm_title(item)) for item in found} - {""}


def title_records(rows_of: Mapping[str, list[dict]], cvs: Mapping[str, list[dict]]) -> Counter[str]:
    """Title base → number of distinct records that credit or list it (E3).

    A record is a ledger id. Its titles are those of its activity rows (roster
    and CV origin) and of its extracted CV lines. A row with a bracketed title
    counts that title; a row without one counts its whole title, so a CV line
    that is only ``Untitled`` counts too.
    """
    owners: dict[str, set[str]] = defaultdict(set)
    for lid, rows in rows_of.items():
        for row in rows:
            for key in _row_titles(str(row.get("title") or ""), str(row.get("role") or "")):
                owners[key].add(lid)
    for lid, rows in cvs.items():
        for row in rows:
            for key in _row_titles(str(row.get("title") or "")):
                owners[key].add(lid)
    return Counter({key: len(ids) for key, ids in owners.items()})


def generic_titles(
    rows_of: Mapping[str, list[dict]], cvs: Mapping[str, list[dict]], threshold: int = GENERIC_TITLE_RECORDS
) -> frozenset[str]:
    """Title bases used by at least ``threshold`` distinct records. 0 turns the rule off."""
    if threshold <= 0:
        return frozenset()
    return frozenset(key for key, count in title_records(rows_of, cvs).items() if count >= threshold)


def is_work_title(key: str, generic: Iterable[str] = ()) -> bool:
    """True when a normalised title can be E3 evidence.

    It has ``_WORK_MIN`` characters, its base has ``_WORK_BASE_MIN``, and its
    base is not in ``generic`` (:func:`generic_titles`). The resolver and the
    manual-merge check both read works through :func:`roster_works`, so both
    apply this.
    """
    base = title_base(key)
    return len(key) >= _WORK_MIN and len(base) >= _WORK_BASE_MIN and base not in generic


def roster_works(rows: list[dict], generic: frozenset[str] = frozenset()) -> set[tuple[str, int]]:
    """Bracketed work titles credited on roster rows, with their year (E3).

    A title in ``generic`` (or too short) is left out: see :func:`is_work_title`.
    """
    found: set[tuple[str, int]] = set()
    for row in rows:
        if (row.get("origin") or "").startswith("cv:"):
            continue
        year = _year(row.get("year"))
        if year is None:
            continue
        blob = f"{row.get('title', '')} {row.get('role', '')}"
        for title in BRACKETED.findall(blob):
            key = norm_title(title)
            if is_work_title(key, generic):
                found.add((key, year))
    return found


def cv_lists_work(cv_rows: list[dict], works: set[tuple[str, int]]) -> str | None:
    """E3 hit: a CV title contains a roster work in the same year ± ``YEAR_WINDOW``."""
    if not works or not cv_rows:
        return None
    for activity in cv_rows:
        text = norm_title(str(activity.get("title") or ""))
        year = _year(activity.get("year"))
        if year is None:
            continue
        for work, work_year in works:
            if work in text and abs(year - work_year) <= YEAR_WINDOW:
                return f"{year} {str(activity.get('title') or '')[:80]}"
    return None


def teams(rows: list[dict], *, prefix: str = "팀:") -> set[str]:
    """Normalised team names credited with ``prefix`` on roster roles (E4).

    ``prefix`` is the field file's marker. The default ``팀:`` is the usual E4
    spelling, so a caller that has not loaded a field still reads those credits.
    """
    found: set[str] = set()
    pattern = re.escape(prefix) + r" ?([^|;]+)"
    for row in rows:
        for name in re.findall(pattern, row.get("role") or ""):
            key = norm_title(name)
            if len(key) >= _TEAM_MIN:
                found.add(key)
    return found


def evidence_e2_e4(
    left: str,
    right: str,
    frames: Mapping[str, set[str]],
    rows_of: Mapping[str, list[dict]],
    cvs: Mapping[str, list[dict]],
    patterns: Mapping[str, str],
    *,
    team_prefix: str = "팀:",
    generic: frozenset[str] = frozenset(),
) -> str | None:
    """First of E2, E3, E4 that holds, checked in that order, either direction.

    A hit stops the search, so the first rule that holds is the one recorded.
    E2 is tried before E3, and E4 runs only when neither of those fired.
    ``generic`` is :func:`generic_titles` over the whole ledger: those titles are not E3 works.
    """
    evidence = None
    for this, other in ((left, right), (right, left)):
        # An edition both records are on does not tie them: the CV's owner
        # lists their own appearance (software review, round 6).
        for frame_code in sorted(frames.get(other, set()) - frames.get(this, set())):
            years = edition_years(frame_code, rows_of.get(other, []))
            hit = cv_mentions(cvs.get(this, []), frame_code, years, patterns)
            if hit:
                evidence = f"E2 {this}'s CV lists {frame_code}: {hit}"
                break
        if not evidence:
            works_this = roster_works(rows_of.get(this, []), generic)
            works_other = roster_works(rows_of.get(other, []), generic)
            both = sorted(
                title
                for title, year in works_this
                if any(
                    title == other_title and abs(year - other_year) <= YEAR_WINDOW
                    for other_title, other_year in works_other
                )
            )
            if both:
                evidence = f"E3 both rosters credit the work {both[0]}"
                break
            hit = cv_lists_work(cvs.get(this, []), works_other)
            if hit:
                evidence = f"E3 {this}'s CV lists a work {other}'s roster credits: {hit}"
        if evidence:
            break
    if not evidence:
        shared = teams(rows_of.get(left, []), prefix=team_prefix) & teams(
            rows_of.get(right, []), prefix=team_prefix
        )
        if shared:
            evidence = f"E4 both credited in team {min(shared)}"
    return evidence


def rule_of(evidence: str) -> str:
    """Rule token stored on the kept row: ``E1`` … ``E4``, or ``X1+E2`` and the like."""
    token = (evidence or "").split(" ", 1)[0]
    if re.fullmatch(r"(?:X1\+)?E[1-4]", token):
        return token
    raise ValueError(f"evidence does not name a rule: {evidence!r}")
