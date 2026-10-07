# SPDX-License-Identifier: AGPL-3.0-only
"""K1: derived ``activity_kind`` and ``activity_channel``.

The kind vocabulary and the ``cv_section`` table are field-agnostic. Which
roster words may leave the activity channel is not a list in this module: a
word qualifies only when it occurs as a whole word on a roster ``other`` row
and the CV role column supports it (at least 10 rows, more than half of the
role-and-title mentions sitting in the role column, modal private section
above half). The same cutoff is concentration and section share.

A numbered session (``회차``) beside a passing teaching word stays
``talk_workshop`` on the activity channel (``roster_session``). The background
block does not read ``publishable`` and does not treat ``예정`` as upcoming,
so those rows would otherwise appear there.

When a teaching word and an employment word both hit one roster row, the
employment word is dropped before the share comparison. Share order would let
a higher-share employment word (on the reference ledger, ``vfx``) hide the
teaching post. No roster ``other`` row on that ledger carries two passing
words, so the preference does not change a count there.

Usage: ``giye normalize`` calls :func:`classify`. The ledger is not edited.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from giye.extract.apply import PRIVATE_TITLE

RULE_ID = "K1"
SESSION_REASON = "roster_session"

KINDS = (
    "solo_exhibition",
    "group_exhibition",
    "screening",
    "performance",
    "festival",
    "online_release",
    "residency",
    "award",
    "funding",
    "commission",
    "collection",
    "talk_workshop",
    "publication_press",
    "education",
    "teaching",
    "employment",
    "service",
    "other",
)

CHANNELS = ("activity", "background", "hidden")

# Kinds the activity list never shows once K1 has labelled the row.
OFF_ACTIVITY_LIST = frozenset({"education", "teaching", "employment", "service"})

PRIVATE_KIND = {
    "education": "education",
    "employment": "employment",
    "teaching": "teaching",
    "press": "publication_press",
    "scholarship": "funding",
    "service": "service",
}

PRIVATE_CHANNEL = {
    "education": "background",
    "employment": "background",
    "teaching": "background",
    "press": "background",
    "scholarship": "hidden",
    "service": "hidden",
}

TYPE_KIND = {
    "solo_exhibition": "solo_exhibition",
    "group_exhibition": "group_exhibition",
    "screening": "screening",
    "performance": "performance",
    "festival": "festival",
    "online_release": "online_release",
    "award": "award",
    "residency": "residency",
}

OVERRIDES = {
    ("award", "grant"): "funding",
    ("award", "project"): "other",
}

OTHER_KIND = {
    "performance": "performance",
    "screening": "screening",
    "festival": "festival",
    "grant": "funding",
    "residency": "residency",
    "commission": "commission",
    "workshop": "talk_workshop",
    "talk": "talk_workshop",
    "publication": "publication_press",
}

_SECTION = re.compile(r"cv_section=([a-z_]+)")
_HANGUL_WORD = re.compile(r"[가-힣]+")
_LATIN_WORD = re.compile(r"[A-Za-z]+")
# One-syllable Hangul words are particles. Latin length 3 is included so a
# short role word such as vfx is tested instead of being dropped by length.
_MIN_LATIN = 3
_MIN_HANGUL = 2
_MIN_TOKEN_N = 10
_MIN_SHARE = 0.50

_TITLE_LEX = re.compile(
    r"소장|수집|컬렉션|collection|acquired|acquisition|permanent|ankauf|미술은행|art\s*bank|구입|매입",
    re.IGNORECASE,
)
# Venue pattern omits 미술은행 and "art bank" (title wording). 구입 and 매입
# stay, matching the reference script rather than an earlier comment on it.
_VENUE_LEX = re.compile(
    r"소장|수집|컬렉션|collection|acquired|acquisition|permanent|ankauf|구입|매입",
    re.IGNORECASE,
)
# Locative common noun. "X Collection" and "collection of X" do not match.
_SITE_DESC = re.compile(r"\bcollection in the\b", re.IGNORECASE)
_SESSION = re.compile(r"(?<![가-힣])회차(?![가-힣])")
_AWARD_WORD = re.compile(r"\baward\b", re.IGNORECASE)
_GRANT_OR_STUDY = re.compile(r"grant|in support|scholarship|fellowship|stipend|장학", re.IGNORECASE)


@dataclass(frozen=True)
class ActivityKind:
    """One row's derived kind. ``reason`` is the clause id stored beside K1."""

    kind: str
    reason: str
    channel: str


@dataclass(frozen=True)
class _Word:
    word: str
    role_n: int
    share: float
    section: str
    kind: str
    channel: str


def section_of(note: str) -> str:
    """The ``cv_section`` token in a reviewer note, or ``""`` when it is absent."""
    for part in (note or "").split(";"):
        part = part.strip()
        if _SECTION.fullmatch(part):
            return part.split("=", 1)[1]
    return ""


def words_of(text: str) -> set[str]:
    """Whole words. A Hangul compound is one word, so 연구원 does not fire inside 전임연구원."""
    found = set(_HANGUL_WORD.findall(text or ""))
    found.update(word.lower() for word in _LATIN_WORD.findall(text or ""))
    return found


def _acquisition(title: str, venue: str) -> bool:
    """Holding lexicon. A locative ``collection in the`` on the venue is not a holding name."""
    venue_hit = bool(_VENUE_LEX.search(venue)) and not _SITE_DESC.search(venue)
    return bool(_TITLE_LEX.search(title) or venue_hit)


def _bare_prize(title: str) -> bool:
    """Award-word title with no grant or study wording, on the scholarship×award cell."""
    return bool(_AWARD_WORD.search(title) and not _GRANT_OR_STUDY.search(title))


def kind_of(activity_type: str, section: str, title: str = "", venue: str = "") -> tuple[str, str]:
    """Return ``(kind, reason_id)`` for one CV row.

    A private section wins, except an award-word title on ``scholarship`` that
    does not also say grant or study: that row is ``award`` and stays hidden
    via :func:`channel_of`. ``award``×``grant`` is funding. ``award``×``project``
    and ``other``×``project`` stay ``other``.
    """
    if section == "scholarship" and activity_type == "award" and _bare_prize(title):
        return "award", "scholarship_bare_prize"
    if section in PRIVATE_KIND:
        return PRIVATE_KIND[section], "private_section"
    if (activity_type, section) in OVERRIDES:
        reason = "award_grant" if section == "grant" else "award_project"
        return OVERRIDES[(activity_type, section)], reason
    if activity_type in TYPE_KIND:
        return TYPE_KIND[activity_type], "type_wins"
    if section == "exhibition":
        return "other", "other_exhibition"
    if section == "award":
        return "other", "other_award"
    if section == "program":
        return "other", "other_program"
    if section == "project":
        return "other", "other_project"
    if section == "collection":
        if _acquisition(title, venue):
            return "collection", "collection_lexicon"
        return "other", "collection_residual"
    if section == "other":
        return "other", "other_residual"
    if section in OTHER_KIND:
        return OTHER_KIND[section], "other_section"
    return "other", "other_residual"


def channel_of(section: str, title: str, role: str) -> str:
    """Where a CV row is allowed to show. Kind does not choose this.

    Scholarship and service stay off both surfaces, as do scholarship-like
    titles. Education, employment, teaching, and press are the background block.
    """
    if section in {"scholarship", "service"} or PRIVATE_TITLE.search(f"{title} {role}"):
        return "hidden"
    if section in {"education", "employment", "teaching", "press"}:
        return "background"
    return "activity"


def _cv_row(row: Mapping[str, str]) -> bool:
    return (row.get("origin") or "").startswith("cv:")


def _token_table(cv_rows: Sequence[Mapping[str, str]]) -> tuple[dict[str, Counter[str]], Counter[str]]:
    """Section counts of each whole word in the CV role column, and role-plus-title row counts."""
    role_counts: dict[str, Counter[str]] = defaultdict(Counter)
    both_counts: Counter[str] = Counter()
    for row in cv_rows:
        role = row.get("role") or ""
        both = f"{role} {row.get('title') or ''}"
        section = section_of(row.get("reviewer_note") or "")
        for word in words_of(role):
            role_counts[word][section] += 1
        for word in words_of(both):
            both_counts[word] += 1
    return role_counts, both_counts


def _candidates(roster_other: Sequence[Mapping[str, str]]) -> set[str]:
    """Words that occur on a roster ``other`` row. The CV test then keeps or drops them."""
    found: set[str] = set()
    for row in roster_other:
        blob = f"{row.get('role') or ''} {row.get('title') or ''}"
        for word in words_of(blob):
            hangul = bool(re.fullmatch(r"[가-힣]+", word)) and len(word) >= _MIN_HANGUL
            latin = bool(re.fullmatch(r"[a-z]+", word)) and len(word) >= _MIN_LATIN
            if hangul or latin:
                found.add(word)
    return found


def _describe(
    word: str,
    role_counts: Mapping[str, Counter[str]],
    both_counts: Mapping[str, int],
    min_n: int,
) -> _Word | None:
    counts = role_counts.get(word) or Counter()
    role_n = sum(counts.values())
    both_n = both_counts.get(word, 0)
    if not role_n or not both_n:
        return None
    section, top = counts.most_common(1)[0]
    share = top / role_n
    conc = role_n / both_n
    passes = role_n >= min_n and conc > _MIN_SHARE and share > _MIN_SHARE and section in PRIVATE_KIND
    if not passes:
        return None
    return _Word(
        word=word,
        role_n=role_n,
        share=share,
        section=section,
        kind=PRIVATE_KIND[section],
        channel=PRIVATE_CHANNEL[section],
    )


def passing_words(rows: Sequence[Mapping[str, str]]) -> list[_Word]:
    """Words on roster ``other`` rows that pass the CV role-column test, highest share first."""
    cv_rows = [row for row in rows if _cv_row(row)]
    roster_other = [row for row in rows if not _cv_row(row) and (row.get("activity_type") or "") == "other"]
    role_counts, both_counts = _token_table(cv_rows)
    passed: list[_Word] = []
    for word in _candidates(roster_other):
        item = _describe(word, role_counts, both_counts, _MIN_TOKEN_N)
        if item is not None:
            passed.append(item)
    passed.sort(key=lambda item: (-item.share, item.word))
    return passed


def _latin_script_title(title: str) -> bool:
    """A title with no Hangul. Survey lines store the post itself as the title.

    A Korean work title that also contains an acronym still has Hangul, so the
    acronym is not read as a role.
    """
    return bool(_LATIN_WORD.search(title or "")) and not _HANGUL_WORD.search(title or "")


def _hits(role: str, title: str, passing: Sequence[_Word]) -> list[_Word]:
    """Where a passing word is allowed to fire.

    The role field always counts. A Hangul word in the title counts, because a
    programme line names the post there while the role field holds a different
    job title. A Latin word in the title counts only when that title has no
    Hangul, so an acronym inside a Korean work title does not become a job.
    """
    role_words = words_of(role)
    title_words = words_of(title)
    title_ok = _latin_script_title(title)
    hits = []
    for item in passing:
        hangul = bool(re.fullmatch(r"[가-힣]+", item.word))
        if item.word in role_words or (item.word in title_words and (hangul or title_ok)):
            hits.append(item)
    return hits


def _without_employment_when_teaching(hits: list[_Word]) -> list[_Word]:
    """Drop employment hits when a teaching word also matches this row.

    The share comparison stays inside what remains, so a press word can still
    outrank teaching. Only the employment-over-teaching collision is removed.
    """
    if any(item.section == "teaching" for item in hits) and any(item.section == "employment" for item in hits):
        return [item for item in hits if item.section != "employment"]
    return hits


def roster_of(activity_type: str, title: str, role: str, passing: Sequence[_Word]) -> ActivityKind:
    """Kind, reason, and channel for one roster row."""
    if activity_type in TYPE_KIND:
        return ActivityKind(TYPE_KIND[activity_type], "roster_type", "activity")
    if activity_type != "other":
        return ActivityKind("other", "roster_other", "activity")
    hits = _hits(role, title, passing)
    teach_hits = [item for item in hits if item.section == "teaching"]
    # A numbered session is an event instance. CV teaching does not use 회차 as a role.
    if _SESSION.search(f"{role} {title}") and teach_hits:
        return ActivityKind("talk_workshop", SESSION_REASON, "activity")
    if not hits:
        return ActivityKind("other", "roster_other", "activity")
    best = max(_without_employment_when_teaching(hits), key=lambda item: (item.share, item.role_n))
    return ActivityKind(best.kind, f"roster_token_{best.word}", best.channel)


def classify(rows: Sequence[Mapping[str, str]]) -> list[ActivityKind]:
    """One :class:`ActivityKind` per row, in the same order.

    CV rows use :func:`kind_of` and :func:`channel_of`. Roster rows use words
    discovered from this same sequence: hidden people are already absent when
    normalize calls this, and a test passes the rows it wants judged.
    """
    passing = passing_words(rows)
    out: list[ActivityKind] = []
    for row in rows:
        if _cv_row(row):
            section = section_of(row.get("reviewer_note") or "")
            title = row.get("title") or ""
            role = row.get("role") or ""
            kind, reason = kind_of(row.get("activity_type") or "", section, title, row.get("venue") or "")
            out.append(ActivityKind(kind, reason, channel_of(section, title, role)))
        else:
            out.append(
                roster_of(row.get("activity_type") or "", row.get("title") or "", row.get("role") or "", passing)
            )
    return out
