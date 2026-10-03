# SPDX-License-Identifier: MIT
"""Same-person evidence (rules E1–E4).

Two rows are merged only when one of these holds. A shared name, a romanized
spelling, or a report is not evidence. Thresholds and patterns are the
production ones.

E1. The same personal website. The key is the host, or host plus path on a
shared platform, so two accounts on one host stay apart.

E2. One row's CV names the event on the other's roster in that edition's year,
give or take one year. The event regex is ``EVENT_WORDS`` (the production
programme list) plus ``[resolve.event_patterns]`` in the config.

E3. A work title in brackets (``〈…〉``, ``<…>``, ``《…》``, and the same family)
appears on both roster rows, or on one roster row and the other's CV, in the
same year give or take one year. The normalised title is at least 3 characters.
The event name itself is E2's business, so an unbracketed title does not count.

E4. Both roster rows credit the same team in the role, written ``팀: <name>``.
The normalised team name is at least 2 characters.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping

# Production programme names. A frame code matches the first key it starts with
# after a trailing ``-YYYY`` is removed (``DAVINCI-2014`` → ``DAVINCI``).
# These are programme patterns, not people. Another field adds its own prefixes
# in ``[resolve.event_patterns]``; a prefix listed there replaces this one.
EVENT_WORDS: dict[str, str] = {
    "DAVINCI": r"다빈치|da ?vinci|언폴드|unfold",
    "UNFOLD-X": r"언폴드|unfold|서울융합예술",
    "APE": r"에이프|ape ?camp|apecamp",
    "PARADISE-ARTLAB": r"파라다이스|paradise",
    "AKL": r"아트코리아랩|arts? ?korea ?lab|수퍼 ?테스트베드|super ?testbed",
    "ARTIENCE": r"아티언스|artience",
    "ACT": r"\bACT\b|아시아문화전당|asia culture center",
    "NJP-RANDOMACCESS": r"랜덤 ?액세스|random ?access",
    "NCA": r"뉴 ?콘텐츠 ?아카데미|new ?contents? ?academy|\bNCA\b",
    # The institution's name alone does not distinguish this support programme from others.
    "ARKO-ARTTECH": r"예술과 ?기술 ?융합|예술기술융합|아트앤테크|art ?and ?technology",
    "ACC-CREATORS": r"ACC ?크리에이터|ACC ?creators|아시아문화전당.{0,20}레지던시|ACC.{0,15}residency",
    "GMAF": r"광주 ?미디어 ?아트 ?페스티벌|gwangju media ?art ?festival|\bGMAF\b|G\.MAP",
    "MEDIACITY-SEOUL": r"미디어 ?시티|media ?city",
    "NEMAF": r"네마프|nemaf|뉴미디어 ?페스티벌|대안영상예술|new ?media ?festival|alt ?cinema",
    "ZER01NE": r"제로원|zer01ne|zero ?one ?(day|creator)",
    "VH-AWARD": r"vh ?award|브이에이치 ?어워드|현대 ?블루 ?프라이즈|hyundai.{0,20}vh",
    "ISEA-KOREA": r"\bISEA ?20(19|25)\b",
    "PLATFORM-L-PLAP": r"플랫폼 ?엘|platform[- ]?l\b|\bPLAP\b",
    "NJP-AWARD": r"백남준 ?(국제)?예술상|nam ?june ?paik ?(art )?award",
    "NABI-CREATIVE": r"창의 ?인재|creative ?mentoring|creative ?\+|나비.{0,20}멘티|멘티.{0,20}나비|nabi.{0,30}mentee|mentee.{0,30}nabi",
}

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


def pattern_table(extra: tuple[tuple[str, str], ...] = ()) -> dict[str, str]:
    """Production patterns, then config replacements and additions.

    Updating an existing key keeps its position, so the first matching prefix
    is still the production order.
    """
    table = dict(EVENT_WORDS)
    for key, pattern in extra:
        table[key] = pattern
    return table


def event_pattern(frame_code: str, patterns: Mapping[str, str] | None = None) -> str | None:
    """Regex for a frame code, or None when the code names no known event (E2)."""
    table = patterns if patterns is not None else EVENT_WORDS
    base = re.sub(r"-\d{4}$", "", frame_code or "")
    for key, pattern in table.items():
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
    years, which is how the production ledger encoded an edition
    (``DAVINCI-2014``). A frame code with no year uses the years on that
    frame's roster activities. CV rows are not roster appearances.
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


def roster_works(rows: list[dict]) -> set[tuple[str, int]]:
    """Bracketed work titles credited on roster rows, with their year (E3)."""
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
            if len(key) >= _WORK_MIN:
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


def teams(rows: list[dict]) -> set[str]:
    """Normalised team names credited as ``팀: …`` on roster roles (E4)."""
    found: set[str] = set()
    for row in rows:
        for name in re.findall(r"팀: ?([^|;]+)", row.get("role") or ""):
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
) -> str | None:
    """First of E2, E3, E4 that holds, checked in that order, either direction.

    The loop shape matches production: a hit stops the search, E2 is tried
    before E3, and E4 runs only when neither of those fired.
    """
    evidence = None
    for this, other in ((left, right), (right, left)):
        for frame_code in frames.get(other, ()):
            years = edition_years(frame_code, rows_of.get(other, []))
            hit = cv_mentions(cvs.get(this, []), frame_code, years, patterns)
            if hit:
                evidence = f"E2 {this}'s CV lists {frame_code}: {hit}"
                break
        if not evidence:
            works_this = roster_works(rows_of.get(this, []))
            works_other = roster_works(rows_of.get(other, []))
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
        shared = teams(rows_of.get(left, [])) & teams(rows_of.get(right, []))
        if shared:
            evidence = f"E4 both credited in team {min(shared)}"
    return evidence


def rule_of(evidence: str) -> str:
    """Rule token stored on the kept row: ``E1`` … ``E4``, or ``X1+E2`` and the like."""
    token = (evidence or "").split(" ", 1)[0]
    if re.fullmatch(r"(?:X1\+)?E[1-4]", token):
        return token
    raise ValueError(f"evidence does not name a rule: {evidence!r}")
