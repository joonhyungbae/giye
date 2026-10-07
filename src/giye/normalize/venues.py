# SPDX-License-Identifier: AGPL-3.0-only
"""Resolve activity venue strings into institution and funder entities (P3, V1–V9).

Deterministic string rules and the language module's gazetteer only: no fuzzy
matching, no embeddings, no network, no ledger edits. ``build`` writes
``venues.csv``, ``venue_merges.csv`` and ``venue_audit.md``. The audit and
``venue_merges.csv`` list every merge with its rule id (V5a, V5d, V5f, V7e,
V8, V9, V4n, V7f, V9u, V12, V12i, V12k). Each entity in ``venues.csv`` and each
activity annotation (``venue_rule``) names the rules that put it together (N-8).

``name_rules`` turns V7–V9, V4n, V7f, V9u and V12 off for an ablation. Leaving
it unset keeps those rules on, which is what ``giye normalize`` does unless the
config or ``--venue-name-rules`` says otherwise. V4n runs on the activity row
after the entities exist; V7f, V9u, then V12 remap entity ids. The country fill
runs after those joins. A ``funder_id`` follows the entity maps only.
"""

from __future__ import annotations

import contextvars
import csv
import random
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path

from giye.normalize import venue_names
from giye.normalize.language import LanguageModule, load_language
from giye.normalize.rules import match_key, norm_text

VENUE_FIELDS = [
    "venue_id",
    "name",
    "aliases",
    "kind",
    "city",
    "country",
    "kr_region",
    "n_rows",
    "n_artists",
    "rules",
]
# N-8: every join, machine-readable (the audit's section 7 is Markdown).
MERGE_FIELDS = ["rule", "venue_id", "kept_key", "joined_key"]
# Order of the rule ids in ``venue_rule`` and ``rules``.
# G7–G11 fill an empty country (docs/RULES.md). They are not merge rules.
RULE_ORDER = (
    "V4", "V4n", "V7", "V5a", "V5d", "V5f", "V7e", "V7f", "V8", "V9", "V9u",
    "V12", "V12i", "V12k", "G7", "G8", "G9", "G10", "G11",
)
# V2 separators. The full-width and ideographic commas and semicolon split like
# their ASCII forms (예시미술관，서울); before 2026-10-06 they did not (audit m1).
SPLIT_CHARS = {",", "/", "|", "·", ";", "，", "、", "；", "\x1f"}
# Brackets that open and close a V2 parenthetical: round, square, curly, and
# their full-width or lenticular forms.
OPEN_BRACKETS = "(（[［{｛【"
CLOSE_BRACKETS = ")）]］}｝】"
ONLINE_RE = re.compile(
    r"(?:online|web|website|youtube|vimeo|zoom|instagram|virtual|metaverse|온라인|웹사이트|유튜브)",
    re.IGNORECASE,
)
FUNDER_RE = re.compile(
    r"재단|위원회|진흥원|문화체육관광부|문화재단|\bfoundation\b|\bcouncil\b|"
    r"\bfund\b|\bministry\b|\bagency\b|arts council|지원사업",
    re.IGNORECASE,
)
LETTERS_RE = re.compile(r"[가-힣A-Za-z]")
HANGUL_RE = re.compile(r"[가-힣]")
ACRONYM_RE = re.compile(r"(?=.{2,8}$)(?=.*[A-Z])[A-Z0-9.&]+")
ABBREVIATION_RE = re.compile(r"^(?P<name>.*[가-힣])\s+(?P<abbr>(?=.{2,8}$)(?=.*[A-Z])[A-Z0-9.&]+)$")
INITIAL_STOP_WORDS = {"of", "the", "and", "for", "de", "des", "du", "la", "le", "für", "und", "fur", "&"}
QUOTE_CHARS = set("'\"‘’“”‚„‹›«»＇＂")

# V7 spelling (V7a–d) stays on unless build() is asked to ablate V7.
_V7_SPELLING: contextvars.ContextVar[bool] = contextvars.ContextVar("giye_v7_spelling", default=True)
NAME_RULES = frozenset({"V7", "V8", "V9", "V4n", "V7f", "V9u", "V12"})


@dataclass(frozen=True)
class Place:
    """A resolved place: canonical city, ISO country, and Korean region (empty outside Korea)."""

    city: str
    country: str
    kr_region: str


@dataclass(frozen=True)
class Fragment:
    """One V2 piece of a venue string after V3 classification."""

    text: str
    kind: str
    place: Place | None = None
    key: str = ""


@dataclass
class ParsedVenue:
    """One activity row after the V2 split and V3 classification."""

    activity_id: str
    ledger_id: str
    raw: str
    norm: str
    fragments: list[Fragment]
    alias_pairs: list[tuple[str, str]]
    qualifier: str = ""


@dataclass
class BuildResult:
    """Entities, per-activity annotations, and the merges ``build`` recorded."""

    annotations: dict[str, dict[str, str]]
    venues: list[dict]
    stats: dict
    institution_key_by_activity: dict[str, str] = field(default_factory=dict)
    root_before_name_rules: dict[str, str] = field(default_factory=dict)
    name_rule_merges: list[tuple[str, str, str]] = field(default_factory=list)
    merges: list[tuple[str, str, str]] = field(default_factory=list)
    # venue_id → (country, region, rule ids) for entities whose stored country was empty.
    country_fills: dict[str, tuple[str, str, str]] = field(default_factory=dict)


class UnionFind:
    """Disjoint sets of institution keys.

    The lexicographically smaller root is kept so the same pair always joins
    the same way, independent of insertion order.
    """

    def __init__(self, values: set[str]) -> None:
        """One component per starting key."""
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        """Root of ``value``, with path compression."""
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> bool:
        """Join ``left`` and ``right``. True when they were in different components."""
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return False
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        return True


def _plain_fragments(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    """V2 separators and brackets, preserving the fragments' source order."""
    text = re.sub(r"\s+[-–—]\s+", "\x1f", text)  # a spaced hyphen, en dash or em dash
    fragments: list[str] = []
    parent_pairs: list[tuple[str, str]] = []
    stack: list[tuple[str, int]] = []
    buffer: list[str] = []

    def flush() -> None:
        piece = "".join(buffer).strip()
        buffer.clear()
        if piece:
            fragments.append(piece)

    for char in text:
        if char in SPLIT_CHARS:
            flush()
        elif char in OPEN_BRACKETS:
            flush()
            stack.append((fragments[-1] if fragments else "", len(fragments)))
        elif char in CLOSE_BRACKETS:
            flush()
            if stack:
                before, start = stack.pop()
                inside = fragments[start:]
                if before and len(inside) == 1:
                    parent_pairs.append((before, inside[0]))
        else:
            buffer.append(char)
    flush()
    return fragments, parent_pairs


def _expand_abbreviation(fragment: str) -> list[str]:
    """V2: a Hangul name followed by an acronym (``국립현대미술관 MMCA``) is two fragments."""
    match = ABBREVIATION_RE.fullmatch(fragment)
    if not match:
        return [fragment]
    return [match.group("name").strip(), match.group("abbr")]


def split_venue(venue_norm: str) -> tuple[list[str], list[tuple[str, str]]]:
    """V2 fragments plus parenthetical and Hangul-name/acronym alias candidates."""
    raw_fragments, parent_pairs = _plain_fragments(venue_norm)
    fragments: list[str] = []
    alias_pairs: list[tuple[str, str]] = []
    for fragment in raw_fragments:
        expanded = _expand_abbreviation(fragment)
        fragments.extend(expanded)
        if len(expanded) == 2:
            alias_pairs.append((expanded[0], expanded[1]))
    for left, right in parent_pairs:
        alias_pairs.append((_expand_abbreviation(left)[0], _expand_abbreviation(right)[0]))
    return fragments, alias_pairs


def bracketed_pairs(venue_norm: str) -> list[tuple[str, str]]:
    """V2 round-bracket pairs only: ``(fragment before the bracket, the one fragment inside)``.

    Unlike the alias list of :func:`split_venue`, a Hangul name followed by an
    unbracketed acronym is not a pair here; a fragment before the bracket that
    V2 expands into a name and an acronym pairs each of them with the inside.
    """
    _fragments, parent_pairs = _plain_fragments(venue_norm)
    pairs: list[tuple[str, str]] = []
    for left, right in parent_pairs:
        inside = _expand_abbreviation(right)[0]
        pairs.extend((piece, inside) for piece in _expand_abbreviation(left))
    return pairs


def _mostly_hangul(text: str) -> bool:
    return venue_names.mostly_hangul(text)


def place_qualifier(fragments: list[Fragment]) -> str:
    """N-1: the row's place for a generic name: the first city, else the first country, else ""."""
    places = [fragment.place for fragment in fragments if fragment.kind == "place" and fragment.place]
    city = next((place.city for place in places if place.city), "")
    if city:
        return city.casefold()
    return next((place.country for place in places if place.country), "")


def entity_key(text: str, qualifier: str, lang: LanguageModule) -> str:
    """V4 key (with V7a–d), qualified by the row's place when the name is generic words only.

    N-1: ``Museum of Art, Busan`` and ``Museum of Art, Daegu`` are two
    institutions. A name of generic venue words names no particular place, so
    the place written in the same row is part of its key. Without a place the
    key is the bare name. A name with a proper word is never qualified.
    """
    key = institution_key(text, lang)
    if qualifier and venue_names.generic_name(key, lang):
        return f"{key}{venue_names.PLACE_SEP}{qualifier}"
    return key


def _latin_text(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and all("LATIN" in unicodedata.name(char, "") for char in letters)


def _acronym_symbols(text: str) -> str:
    """Comparable acronym symbols, including mixed-case house styles such as SeMA.

    V5d′: a mixed-case house style is read by its capitals (SeMA → SMA, MoMA → MMA).
    """
    strict = ACRONYM_RE.fullmatch(text)
    letters = [char for char in text if char.isalpha()]
    styled = not re.search(r"\s", text) and 2 <= len(text) <= 8 and sum(char.isupper() for char in letters) >= 2
    if not strict and not styled:
        return ""
    if not strict:
        return "".join(char for char in text if char.isupper() or char.isdigit())
    return "".join(char for char in text if char.isalnum()).upper()


def _latin_initials(text: str) -> str:
    """V5d initials after dropping the function words."""
    if not _latin_text(text):
        return ""
    words = re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
    return "".join(word[0].upper() for word in words if word.casefold() not in INITIAL_STOP_WORDS)


def _is_subsequence(needle: str, haystack: str) -> bool:
    iterator = iter(haystack)
    return bool(needle) and all(any(char == candidate for candidate in iterator) for char in needle)


def _alias_candidate(left: str, right: str) -> bool:
    """V5a/V5d: a cross-script pair, or an acronym that is a subsequence of a Latin name's initials."""
    cross_script = (_mostly_hangul(left) and _latin_text(right)) or (_mostly_hangul(right) and _latin_text(left))
    if cross_script:
        return True
    for acronym, name in ((left, right), (right, left)):
        symbols = _acronym_symbols(acronym)
        initials = _latin_initials(name)
        if symbols and initials and _is_subsequence(symbols, initials):
            return True
    return False


def _alias_rule(left: str, right: str) -> str:
    """V5a when the pair crosses scripts; otherwise the acronym/initials rule V5d."""
    cross_script = (_mostly_hangul(left) and _latin_text(right)) or (_mostly_hangul(right) and _latin_text(left))
    return "V5a" if cross_script else "V5d"


def _resolved_place(text: str, lang: LanguageModule) -> Place | None:
    """V3c: the gazetteer's whole-fragment hit, or None. Uses the enhanced index."""
    text = text.strip()
    if not text:
        return None
    found = lang.gazetteer.resolve_fragments([text])
    if not found or found[0] is None:
        return None
    return Place(*found[0])


def _nested_place(text: str, lang: LanguageModule) -> Place | None:
    """V3b: two place names without a comma that agree (``Seoul Korea``, ``서울 종로구``).

    The words split into a head and a tail that both resolve as whole places
    (a city, district, region or country), in one country and, inside Korea,
    one first-level region. The head's place is the fragment's place, or the
    tail's when the head names no city. Why: the city plus its own country
    code was the only such form; ``Tokyo Japan`` and ``서울 종로구`` became
    institutions (pre-release audit N-7).
    """
    words = text.split()
    if not 2 <= len(words) <= 4:
        return None
    gazetteer = lang.gazetteer
    for cut in range(1, len(words)):
        head = gazetteer.resolve_fragments([" ".join(words[:cut])])[0]
        tail = gazetteer.resolve_fragments([" ".join(words[cut:])])[0]
        if not head or not tail or head[1] != tail[1]:
            continue
        if head[2] and tail[2] and head[2] != tail[2]:
            continue
        if head[0] and tail[0] and head[2] == tail[2] == "" and head[0] != tail[0]:
            continue  # two cities abroad
        return Place(*(head if head[0] or not tail[0] else tail))
    return None


def classify_fragment(text: str, lang: LanguageModule) -> Fragment:
    """V3 classification, first match: office, place, city+country-code, online, funder, institution.

    V1 / V3: a fragment is a place only when the whole fragment is a place name.
    V3b: a city followed by its own country code (``Seoul KR``) is a place, not an institution.
    V3c: a fragment the gazetteer resolves to a place (city, district, county,
    neighbourhood) is a place even when it is the venue's only fragment, and
    also when V7a–d leave only that place name. It is not an institution, so
    V7e, V8 and V9 never see it. A longer name that merely contains a place
    word stays an institution.
    V8b: an administrative office is an institution even though it starts with a place.
    A fragment that is only a work title in 《》〈〉<>「」『』 is a title, not an
    institution (audit m4): ``《예시의 정원》`` names no venue.
    """
    if venue_names.TITLE_RE.search(text) and len(LETTERS_RE.findall(venue_names.TITLE_RE.sub(" ", text))) < 2:
        return Fragment(text, "title")
    spelled = institution_key(text, lang)
    if venue_names.is_admin_office(text, lang) or venue_names.is_admin_office(spelled, lang):
        return Fragment(text, "institution")
    place = _resolved_place(text, lang)
    if place is None and spelled != text.strip() and not venue_names.is_admin_office(spelled, lang):
        place = _resolved_place(spelled, lang)
    if place:
        return Fragment(text, "place", place)
    city_code = re.fullmatch(r"(.+?)\s+([A-Z]{2,3})\.?", text)
    if city_code:
        gazetteer = lang.gazetteer
        index = gazetteer.index
        code = city_code.group(2)
        country = code if code in index.country_codes else index.alpha3_codes.get(code, "")
        head = gazetteer.resolve_detail(city_code.group(1), words=False)
        if head and country and head[0][1] == country:
            return Fragment(text, "place", Place(*head[0]))
    nested = _nested_place(text, lang)
    if nested:
        return Fragment(text, "place", nested)
    if ONLINE_RE.fullmatch(text):
        return Fragment(text, "online")
    if FUNDER_RE.search(text):
        return Fragment(text, "funder")
    if len(LETTERS_RE.findall(text)) >= 2:
        return Fragment(text, "institution")
    return Fragment(text, "unclassified")


def classify_fragments(texts: list[str], lang: LanguageModule) -> list[Fragment]:
    """Classify venue fragments with the adjacency context G3 requires.

    V3c applies to every fragment, including a venue string that is only a place.
    V8b is checked first so an office is not swallowed by a place hit on its stem.
    """
    fragments: list[Fragment] = []
    for text, detail in zip(texts, lang.gazetteer.resolve_fragments(texts)):
        if venue_names.is_admin_office(text, lang):
            fragments.append(Fragment(text, "institution"))
        elif detail:
            city, country, region = detail
            fragments.append(Fragment(text, "place", Place(city, country, region)))
        else:
            fragments.append(classify_fragment(text, lang))
    return fragments


def institution_key(text: str, lang: LanguageModule | None = None) -> str:
    """V4 exact entity key, plus V7a–d when the spelling rules are on.

    V7a–d are skipped only while a build() ablation has turned V7 off.
    """
    text = unicodedata.normalize("NFC", text)
    if _V7_SPELLING.get():
        text = venue_names.strip_titles(text)  # V7a
    text = text.casefold()
    kept = []
    for char in text:
        category = unicodedata.category(char)
        if char in QUOTE_CHARS or char in ".,，．" or category in {"Ps", "Pe", "Pi", "Pf"}:
            continue
        kept.append(char)
    key = _strip_the(re.sub(r"\s+", " ", "".join(kept)).strip())
    if _V7_SPELLING.get():
        # V7b–c can leave a new leading or trailing "the" ("2019 The Space"); the
        # second pass keeps the key a fixed point, so a key of a key is the key (audit m8).
        return _strip_the(venue_names.normalize_key(key, lang))
    return key


def _strip_the(key: str) -> str:
    """V4: every leading and trailing "the" ("The The Space" is "space")."""
    key = re.sub(r"^(?:the\s+)+", "", key)
    return re.sub(r"(?:\s+the)+$", "", key).strip()


# Full-width ASCII forms (U+FF01–U+FF5E) and the ideographic space, read as ASCII.
_WIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)} | {0x3000: 0x20}


def fold_width(text: str) -> str:
    """Full-width Latin letters, digits and punctuation as their ASCII forms (audit m3).

    ``Ｓｅｏｕｌ Ｍｕｓｅｕｍ ｏｆ Ａｒｔ`` has no ASCII letter, so V3 found no
    letters and the row was dropped as empty. Only the full-width block is
    folded, not every NFKC compatibility form, so Hangul and other text stay as
    written.
    """
    return text.translate(_WIDTH)


def _funder_acronyms(fragments: list[Fragment], alias_pairs: list[tuple[str, str]]) -> list[Fragment]:
    """N-4: an acronym written as the alias of a funder is that funder, not an institution.

    ``Arts Council Example (ACE)``: ACE abbreviates the funder in the same row,
    so it must not become the row's venue or join an institution called ACE.
    """
    funders = {fragment.text for fragment in fragments if fragment.kind == "funder"}
    acronyms = {
        acronym
        for left, right in alias_pairs
        for name, acronym in ((left, right), (right, left))
        if name in funders and _acronym_symbols(acronym)
    }
    return [
        replace(fragment, kind="funder") if fragment.kind == "institution" and fragment.text in acronyms else fragment
        for fragment in fragments
    ]


def _titles_before_venues(
    fragments: list[Fragment], alias_pairs: list[tuple[str, str]], title: str | None, lang: LanguageModule
) -> list[Fragment]:
    """N-5: in a row with two or more institution fragments, a title is not the venue.

    CV readings often write "Title, Venue, City" without brackets. A fragment
    is venue-like when it names a kind of venue (``has_venue_word``) and is not
    generic words only (``Museum of Modern Art`` alone is the tail of a name
    such as ``Example, Museum of Modern Art``, so it does not count). When the
    row has a venue-like fragment, a fragment that is not venue-like, or one
    equal to the row's title while another fragment is venue-like, is read as a
    title (kind ``title``, not an entity). An acronym and a fragment written as
    the bracketed alias of another are names, not titles. Otherwise nothing
    changes and the first institution fragment stays the venue.
    """
    named = [fragment for fragment in fragments if fragment.kind == "institution"]
    if len(named) < 2:
        return fragments
    venue_like = {
        fragment.text
        for fragment in named
        if venue_names.has_venue_word(fragment.text, lang) and not venue_names.generic_name(fragment.key or institution_key(fragment.text, lang), lang)
    }
    if not venue_like:
        return fragments
    title_key = match_key(title)
    aliases = {text for pair in alias_pairs for text in pair}

    def is_title(fragment: Fragment) -> bool:
        if fragment.text in aliases:
            return False
        if fragment.text in venue_like:
            return bool(title_key) and match_key(fragment.text) == title_key and len(venue_like - {fragment.text}) > 0
        return not _acronym_symbols(fragment.text)

    drop = {id(fragment) for fragment in named if is_title(fragment)}
    return [replace(fragment, kind="title") if id(fragment) in drop else fragment for fragment in fragments]


def parse_venue(row: dict, lang: LanguageModule) -> ParsedVenue:
    """V2 split and V3 classification of one activity row's venue."""
    venue_norm = norm_text(row.get("venue"))
    pieces, alias_pairs = split_venue(fold_width(venue_norm))
    fragments = _titles_before_venues(
        _funder_acronyms(classify_fragments(pieces, lang), alias_pairs), alias_pairs, row.get("title"), lang
    )
    qualifier = place_qualifier(fragments)
    fragments = [
        replace(fragment, key=entity_key(fragment.text, qualifier, lang))
        if fragment.kind in {"institution", "funder"}
        else fragment
        for fragment in fragments
    ]
    return ParsedVenue(
        activity_id=row["activity_id"],
        ledger_id=row["ledger_id"],
        raw=row.get("venue") or "",
        norm=venue_norm,
        fragments=fragments,
        alias_pairs=alias_pairs,
        qualifier=qualifier,
    )


def _spelling_sort(item: tuple[str, set[str]]) -> tuple[int, int, str]:
    """Most-used spelling first, then a Hangul spelling, then the text."""
    spelling, row_ids = item
    return -len(row_ids), 0 if HANGUL_RE.search(spelling) else 1, spelling


def _display_key(key: str) -> str:
    """An entity key as text: a generic name's place after `` @ `` (N-1)."""
    return key.replace(venue_names.PLACE_SEP, " @ ")


def _write_csv(path: Path, rows: list[dict], fields: list[str] = VENUE_FIELDS) -> None:
    """Write ``venues.csv`` (or another table of this module) with a newline after every row."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _audit_clean(value: str) -> str:
    """Markdown-safe venue text. A backtick would break the audit's code spans."""
    return norm_text(value).replace("`", "ˋ") or "(blank)"


def _audit_fragment_line(
    fragment: Fragment,
    root_for_key: dict[str, str],
    entity_by_root: dict[str, dict],
    lang: LanguageModule,
    redirect: dict[str, str] | None = None,
    by_id: dict[str, dict] | None = None,
) -> str:
    """One fragment as the audit prints it: a place, an entity id, or a bare kind.

    ``redirect`` sends an absorbed entity to the keeper, so the id printed here
    is one ``venues.csv`` still contains.
    """
    if fragment.kind == "place" and fragment.place:
        place = fragment.place
        detail = ", ".join(value for value in (place.city, place.country, place.kr_region) if value)
        return f"`{_audit_clean(fragment.text)}` → place ({detail})"
    if fragment.kind in {"institution", "funder"}:
        root = root_for_key[fragment.key]
        entity = entity_by_root[root]
        if redirect and by_id and entity["venue_id"] in redirect:
            entity = by_id.get(redirect[entity["venue_id"]], entity)
        return (
            f"`{_audit_clean(fragment.text)}` → {fragment.kind} → "
            f"{entity['venue_id']} ({_audit_clean(entity['name'])})"
        )
    return f"`{_audit_clean(fragment.text)}` → {fragment.kind}"


def _audit_sample_lines(
    parsed: list[ParsedVenue],
    annotations: dict[str, dict[str, str]],
    entity_by_root: dict[str, dict],
    root_for_key: dict[str, str],
    lang: LanguageModule,
    redirect: dict[str, str] | None = None,
) -> list[str]:
    """Section 1. A fixed seed so the same rows are the sample every run."""
    by_id = {entity["venue_id"]: entity for entity in entity_by_root.values()}
    rng = random.Random(20260925)
    # Sampled from rows sorted by activity id, so the ledger's row order does not pick the sample.
    by_activity = sorted(parsed, key=lambda row: row.activity_id)
    sample = rng.sample(by_activity, min(60, len(by_activity)))
    lines = [
        "# Place and institution entity-resolution audit sample",
        "",
        "Rules G1–G6 · V2–V9, V4n, V7f, V9u, V12 · G7–G11 country fill. Random sample seed: `20260925`.",
        "Section 7 lists every merge and the rule that made it. Section 8 lists the country fill.",
        "",
        "## 1. Random sample of activity rows",
        "",
    ]
    for index, row in enumerate(sample, 1):
        annotation = annotations[row.activity_id]
        venue = by_id.get(annotation["venue_id"])
        funder = by_id.get(annotation["funder_id"])
        lines.extend(
            [
                f"### {index}. {row.activity_id}",
                "",
                f"- Raw venue: `{_audit_clean(row.raw)}`",
                "- Fragments: "
                + (
                    "; ".join(
                        _audit_fragment_line(fragment, root_for_key, entity_by_root, lang, redirect, by_id)
                        for fragment in row.fragments
                    )
                    or "(none)"
                ),
                f"- venue_kind: {annotation['venue_kind']}",
                f"- venue_id: {annotation['venue_id']}" + (f" ({_audit_clean(venue['name'])})" if venue else ""),
                f"- funder_id: {annotation['funder_id']}" + (f" ({_audit_clean(funder['name'])})" if funder else ""),
                "",
            ]
        )
    return lines


def _audit_alias_lines(
    entity_by_root: dict[str, dict], alias_roots: set[str], omit_ids: set[str] | None = None
) -> list[str]:
    """Section 2. The thirty alias-merged entities with the most rows."""
    hidden = omit_ids or set()
    alias_entities = sorted(
        (entity_by_root[root] for root in alias_roots if entity_by_root[root]["venue_id"] not in hidden),
        key=lambda entity: (-entity["n_rows"], entity["name"]),
    )[:30]
    lines = ["## 2. Top 30 entities merged by alias", ""]
    if not alias_entities:
        lines.append("- none")
    for entity in alias_entities:
        spellings = [entity["name"], *entity["_aliases"]]
        lines.append(
            f"- {entity['venue_id']} · {_audit_clean(entity['name'])} · {entity['n_rows']} rows: "
            + " · ".join(_audit_clean(value) for value in spellings)
        )
    return lines


def _audit_largest_lines(entity_by_root: dict[str, dict], omit_ids: set[str] | None = None) -> list[str]:
    """Section 3. The forty entities with the most rows."""
    hidden = omit_ids or set()
    lines = ["", "## 3. Top 40 entities by row count", ""]
    visible = [entity for entity in entity_by_root.values() if entity["venue_id"] not in hidden]
    for entity in sorted(visible, key=lambda item: (-item["n_rows"], item["name"]))[:40]:
        lines.append(
            f"- {entity['venue_id']} · {_audit_clean(entity['name'])} · {entity['kind']} · "
            f"{entity['n_rows']} rows · {entity['n_artists']} artists"
        )
    return lines


def _audit_funder_lines(entity_by_root: dict[str, dict], omit_ids: set[str] | None = None) -> list[str]:
    """Section 4. The thirty funders with the most rows."""
    hidden = omit_ids or set()
    lines = ["", "## 4. Top 30 funders", ""]
    funders = [
        entity for entity in entity_by_root.values() if entity["kind"] == "funder" and entity["venue_id"] not in hidden
    ]
    if not funders:
        lines.append("- none")
    for entity in sorted(funders, key=lambda item: (-item["n_rows"], item["name"]))[:30]:
        lines.append(
            f"- {entity['venue_id']} · {_audit_clean(entity['name'])} · "
            f"{entity['n_rows']} rows · {entity['n_artists']} artists"
        )
    return lines


def _audit_code_like_lines(
    parsed: list[ParsedVenue],
    entity_by_root: dict[str, dict],
    root_for_key: dict[str, str],
    lang: LanguageModule,
) -> list[str]:
    """Section 5. Institution fragments that are only a country code or an admin1 name.

    They stayed institutions because the fragment was not a whole place name.
    The list is a check that G1 and admin1 lookup did not miss them.
    """
    suspicious: dict[str, dict[str, set[str] | Counter]] = defaultdict(
        lambda: {"rows": set(), "artists": set(), "spellings": Counter()}
    )
    code_like = re.compile(r"^[A-Z]{2,3}\.?$")
    for row in parsed:
        seen_keys: set[str] = set()
        for fragment in row.fragments:
            if fragment.kind != "institution":
                continue
            if not code_like.fullmatch(fragment.text.strip()) and not lang.gazetteer.is_admin1_name(fragment.text):
                continue
            key = fragment.key
            item = suspicious[key]
            spellings = item["spellings"]
            assert isinstance(spellings, Counter)
            spellings[fragment.text] += 1
            if key not in seen_keys:
                rows = item["rows"]
                artists = item["artists"]
                assert isinstance(rows, set) and isinstance(artists, set)
                rows.add(row.activity_id)
                artists.add(row.ledger_id)
                seen_keys.add(key)
    lines = [
        "",
        "## 5. Top 30 fragments left as institution that look like a country code or a state name",
        "",
    ]
    ordered_suspicious = sorted(
        suspicious.items(),
        key=lambda item: (-len(item[1]["rows"]), institution_key(item[0], lang)),
    )[:30]
    if not ordered_suspicious:
        lines.append("- none")
    for key, item in ordered_suspicious:
        spellings = item["spellings"]
        rows = item["rows"]
        artists = item["artists"]
        assert isinstance(spellings, Counter)
        assert isinstance(rows, set) and isinstance(artists, set)
        spelling = min(spellings.items(), key=lambda value: (-value[1], value[0]))[0]
        entity = entity_by_root[root_for_key[key]]
        lines.append(
            f"- {entity['venue_id']} · `{_audit_clean(spelling)}` · {len(rows)} rows · {len(artists)} artists"
        )
    return lines


def _audit_unmerged_lines(blocked_components: list[dict]) -> list[str]:
    """Section 6. Components V5e left unmerged because they hold two Hangul names."""
    lines = [
        "",
        "## 6. Components left unmerged",
        "",
        (
            "V5e: if a candidate pair's connected component contains two or more distinct Hangul name keys, "
            "the whole component is left unmerged. V5d (N-4): an acronym written beside the names of two or "
            "more institutions joins none of them; it is listed first."
        ),
        "",
        f"- Component count: {len(blocked_components)}",
    ]
    for index, component in enumerate(blocked_components, 1):
        lines.append(
            f"- {index}. {component['n_rows']} rows · {component['n_artists']} artists · "
            + " · ".join(f"`{_audit_clean(name)}`" for name in component["names"])
        )
    return lines


def _audit_spelling(key: str, spell_rows: dict) -> tuple[str, int]:
    """Most-used spelling of a key, and how many rows use any spelling of it."""
    spellings = spell_rows.get(key, {})
    rows = set().union(*spellings.values()) if spellings else set()
    best = min(spellings.items(), key=_spelling_sort)[0] if spellings else key
    return _audit_clean(best), len(rows)


def _audit_merge_lines(merges: list[tuple[str, str, str]], spell_rows: dict) -> list[str]:
    """Section 7. Every join, with the rule that made it, not a sample."""
    lines = [
        "",
        "## 7. Merges (every join, with its rule)",
        "",
        (
            "V5a cross-script parenthetical pair written by two or more artists · "
            "V5d acronym and Latin initials, two or more artists · "
            "V5f one artist, Hangul and Latin, both names specific, after V5e · "
            "V7e Latin word-bag · V8 part of a known entity · "
            "V9 Hangul reading equals the Latin bag, one reading per component · "
            "V4n generic national name, same country and the same display fold · "
            "V7f host-role word removed because the stripped name already exists · "
            "V9u unique Hangul–Latin pair that passes the spelling gate · "
            "V12 same-row alias (short Hangul name, two people, or one person and two editions) · "
            "V12i exact initials after the acronym guard and the contradiction screens · "
            "V12k keeper kind set to the majority venue_kind."
        ),
        "Format: rule · kept spelling (row count) ← joined spelling (row count).",
    ]
    for rule in ("V5a", "V5d", "V5f", "V9", "V8", "V7e", "V4n", "V7f", "V9u", "V12", "V12i"):
        items = [
            (_audit_spelling(left, spell_rows), _audit_spelling(right, spell_rows))
            for found, left, right in merges
            if found == rule
        ]
        items.sort(key=lambda item: (-(item[0][1] + item[1][1]), item[0][0]))
        lines.extend(["", f"### {rule} — {len(items)} cases", ""])
        if not items:
            lines.append("- none")
        lines.extend(
            f"- {rule} · `{left}`({n_left}) ← `{right}`({n_right})" for (left, n_left), (right, n_right) in items
        )
    # V12k is a kind vote on the keeper, not a second name. The third field is "old -> new".
    kind_changes = [(left, right) for found, left, right in merges if found == "V12k"]
    lines.extend(["", f"### V12k — {len(kind_changes)} cases", ""])
    if not kind_changes:
        lines.append("- none")
    for left, change in kind_changes:
        spelling, _rows = _audit_spelling(left, spell_rows)
        lines.append(f"- V12k · `{spelling}` {change}")
    return lines


def _audit_text(
    parsed: list[ParsedVenue],
    annotations: dict[str, dict[str, str]],
    entity_by_root: dict[str, dict],
    root_for_key: dict[str, str],
    alias_roots: set[str],
    blocked_components: list[dict],
    merges: list[tuple[str, str, str]],
    spell_rows: dict,
    lang: LanguageModule,
    omit_ids: set[str] | None = None,
    redirect: dict[str, str] | None = None,
) -> str:
    """Audit markdown. Section 7 lists every merge, not a sample, each with its rule id."""
    lines = _audit_sample_lines(parsed, annotations, entity_by_root, root_for_key, lang, redirect)
    lines.extend(_audit_alias_lines(entity_by_root, alias_roots, omit_ids))
    lines.extend(_audit_largest_lines(entity_by_root, omit_ids))
    lines.extend(_audit_funder_lines(entity_by_root, omit_ids))
    lines.extend(_audit_code_like_lines(parsed, entity_by_root, root_for_key, lang))
    lines.extend(_audit_unmerged_lines(blocked_components))
    lines.extend(_audit_merge_lines(merges, spell_rows))
    return "\n".join(lines) + "\n"


def _join_unless_office(
    union_find: UnionFind,
    lang: LanguageModule,
    merges: list[tuple[str, str, str]],
    rule: str,
    left: str,
    right: str,
) -> None:
    """Join unless V8b says one key is the office of the other's place."""
    if venue_names.forbids_place_office_merge(left, right, lang):
        return
    if union_find.union(left, right):
        merges.append((rule, left, right))


def _merge_v7e(
    union_find: UnionFind,
    keys: set[str],
    lang: LanguageModule,
    merges: list[tuple[str, str, str]],
) -> None:
    """V7e: Latin keys with one word-bag join the lexicographically first key."""
    by_bag: dict[tuple, list[str]] = defaultdict(list)
    for key in keys:
        bag = None if venue_names.is_qualified(key) else venue_names.latin_bag(key, lang)
        if bag:
            by_bag[bag].append(key)
    for _bag, members in sorted(by_bag.items()):
        members.sort()
        for key in members[1:]:
            _join_unless_office(union_find, lang, merges, "V7e", members[0], key)


def _v8_sites(
    keys: set[str],
    spell_rows: dict,
    parsed: list[ParsedVenue],
    lang: LanguageModule,
) -> tuple[dict[str, Counter], dict[str, set[str]], dict[str, set[str]], set[str]]:
    """Cities beside each key, acronym sites, Hangul branch sites, and known acronyms.

    A site is the spelling itself (acronym + place), not a place fragment next
    to a bare acronym. A row that names no city says nothing. A Hangul
    <place>관 is a site of its parent. ``key_cities`` is a city written in the
    same row as the acronym (ZKM, Karlsruhe).
    """
    key_cities: dict[str, Counter] = defaultdict(Counter)
    for row in parsed:
        cities = [
            fragment.place.city.lower()
            for fragment in row.fragments
            if fragment.kind == "place" and fragment.place and fragment.place.city
        ]
        for fragment in row.fragments:
            if fragment.kind == "institution" and cities:
                key_cities[fragment.key][cities[0]] += 1
    acronym_sites: dict[str, set[str]] = defaultdict(set)
    branch_sites: dict[str, set[str]] = defaultdict(set)
    acronyms = venue_names.known_acronyms(spell_rows)
    for key in keys:
        if venue_names.is_qualified(key):
            continue
        acronym, place = venue_names.acronym_place_parent(key, list(spell_rows[key]), lang, acronyms)
        if acronym:
            acronym_sites[acronym].add(venue_names.city_name(place, lang))
        branch_parent, branch_place = venue_names.hangul_branch_site(key, lang)
        if branch_parent and branch_place and branch_parent in keys:
            branch_sites[branch_parent].add(branch_place)
    return key_cities, acronym_sites, branch_sites, acronyms


def _unique_top(counts: Counter) -> str | None:
    """The single most frequent value, or None when two or more share the top count."""
    top = max(counts.values())
    leaders = [value for value, count in counts.items() if count == top]
    return leaders[0] if len(leaders) == 1 else None


def _join_v8_key(
    key: str,
    keys: set[str],
    spell_rows: dict,
    lang: LanguageModule,
    union_find: UnionFind,
    merges: list[tuple[str, str, str]],
    key_cities: dict[str, Counter],
    acronym_sites: dict[str, set[str]],
    branch_sites: dict[str, set[str]],
    acronyms: set[str],
) -> None:
    """V8: a room joins its parent; an acronym plus its only city joins the acronym."""
    if venue_names.is_qualified(key):
        return  # a generic name qualified by its place has no parent or site
    parent = venue_names.hangul_part_parent(key, lang) or venue_names.latin_part_parent(key, lang)
    if parent and parent in keys and venue_names.part_parent_ok(parent, lang):
        _join_unless_office(union_find, lang, merges, "V8", parent, key)
        return
    acronym, place = venue_names.acronym_place_parent(key, list(spell_rows[key]), lang, acronyms)
    if acronym in keys and venue_names.specific(acronym, lang):
        city = venue_names.city_name(place, lang)
        sites = set(acronym_sites[acronym])
        root = union_find.find(acronym)
        for hangul_parent, places in branch_sites.items():
            if union_find.find(hangul_parent) == root:
                sites |= places
        # One city: the city names the only site (ZKM Karlsruhe). A
        # different city, or a Hangul branch of the same institution,
        # means the acronym plus a city is itself a branch.
        # Both must hold. The bare acronym's own rows sit mostly in that city
        # (so the acronym is that site, not a word such as City or Digital
        # that happens to precede a place), and no spelling names a second
        # site of it (so it has no branches).
        # Tie rule: the city must be the unique most frequent one. When two
        # cities tie, no city is the acronym's own and nothing joins. Why:
        # Counter.most_common breaks ties by insertion order, so the ledger's
        # row order decided the join (the result must not depend on it).
        seen = key_cities.get(acronym)
        own_city = bool(seen) and _unique_top(seen) == city.lower()
        if own_city and sites and all(other == city for other in sites):
            _join_unless_office(union_find, lang, merges, "V8", acronym, key)


def _merge_v8(
    union_find: UnionFind,
    keys: set[str],
    spell_rows: dict,
    parsed: list[ParsedVenue],
    lang: LanguageModule,
    merges: list[tuple[str, str, str]],
) -> None:
    """V8, keys in sorted order so the same pairs join the same way every run."""
    key_cities, acronym_sites, branch_sites, acronyms = _v8_sites(keys, spell_rows, parsed, lang)
    for key in sorted(keys):
        _join_v8_key(
            key, keys, spell_rows, lang, union_find, merges, key_cities, acronym_sites, branch_sites, acronyms
        )


def _merge_v9(
    union_find: UnionFind,
    keys: set[str],
    lang: LanguageModule,
    merges: list[tuple[str, str, str]],
) -> None:
    """V9: join a Hangul reading to Latin bags only when the component has one reading."""
    latin_roots: dict[tuple, set[str]] = defaultdict(set)
    for key in keys:
        bag = None if venue_names.is_qualified(key) else venue_names.latin_bag(key, lang, cross_script=True)
        if bag:
            latin_roots[bag].add(key)
    hangul_keys = sorted(key for key in keys if not venue_names.is_qualified(key) and venue_names.mostly_hangul(key))
    # N-3: a bag that two different Hangul institutions read alike (예시미술관 and
    # 예시시립미술관: 시립 reads as nothing) is ambiguous, and neither joins the Latin name.
    ambiguous = venue_names.ambiguous_readings({key: union_find.find(key) for key in hangul_keys}, lang)
    # The first Hangul reading that matches anything decides (현대 = contemporary before modern).
    edges: list[tuple[str, str, tuple]] = []
    for key in hangul_keys:
        for bag in venue_names.hangul_bags(key, lang):
            hits = sorted({union_find.find(latin) for latin in latin_roots.get(bag, ())})
            if hits:
                if bag not in ambiguous:
                    edges.extend((union_find.find(key), latin_root, bag) for latin_root in hits)
                break
    # One reading per component. Two different bags in one component are ambiguous.
    component = UnionFind({node for hangul, latin, _bag in edges for node in (f"h:{hangul}", f"l:{latin}")})
    for hangul, latin, _bag in edges:
        component.union(f"h:{hangul}", f"l:{latin}")
    bags_of: dict[str, set[tuple]] = defaultdict(set)
    for hangul, _latin, bag in edges:
        bags_of[component.find(f"h:{hangul}")].add(bag)
    for hangul, latin, bag in edges:
        if len(bags_of[component.find(f"h:{hangul}")]) == 1:
            _join_unless_office(union_find, lang, merges, "V9", hangul, latin)


def _name_rule_merges(
    union_find: UnionFind,
    keys: set[str],
    spell_rows: dict,
    parsed: list[ParsedVenue],
    lang: LanguageModule,
    rules: frozenset[str] = NAME_RULES,
) -> list[tuple[str, str, str]]:
    """V7e, then V8, then V9. A rule absent from ``rules`` is not applied.

    Returns ``(rule, left key, right key)`` for each new join. V9 joins only when
    every Hangul–Latin link in a connected component carries the same reading.
    """
    merges: list[tuple[str, str, str]] = []
    if "V7" in rules:
        _merge_v7e(union_find, keys, lang, merges)
    if "V8" in rules:
        _merge_v8(union_find, keys, spell_rows, parsed, lang, merges)
    if "V9" not in rules:
        return merges
    _merge_v9(union_find, keys, lang, merges)
    return merges


def build(
    activity_rows: list[dict],
    out_dir: Path | None = None,
    *,
    name_rules: frozenset[str] | None = None,
    write: bool = True,
    lang: LanguageModule | None = None,
    frames: list[tuple[str, str, str]] | None = None,
    links: dict[str, str] | None = None,
) -> BuildResult:
    """Build venue entities from activity rows.

    ``name_rules=None`` applies V7 (spelling and V7e), V8, V9, V4n, V7f, V9u and V12.
    ``write=False`` skips ``venues.csv`` and ``venue_audit.md``. ``lang`` defaults
    to the Korean–English module. ``frames`` is ``(code, name_ko, name_en)`` from
    the registry; G8 reads those names and nothing else on the frame. ``links``
    is activity id → edition (P4). A row's own ``event_link`` is used when
    ``links`` has no entry. V12c counts those editions.
    """
    rules = NAME_RULES if name_rules is None else frozenset(name_rules)
    unknown = rules - NAME_RULES
    if unknown:
        raise ValueError(f"unknown venue name rules: {sorted(unknown)}")
    if write and out_dir is None:
        raise ValueError("out_dir is required when write is true")
    language = lang or load_language("giye.normalize.lang.ko_en:KoEn")
    token = _V7_SPELLING.set("V7" in rules)
    try:
        return _resolve(activity_rows, out_dir, rules, write, language, frames or [], links or {})
    finally:
        _V7_SPELLING.reset(token)


def _index_named_fragments(
    parsed: list[ParsedVenue], lang: LanguageModule
) -> tuple[dict[str, set[str]], dict[str, dict[str, set[str]]]]:
    """Institution and funder keys, and the activity rows of each spelling."""
    key_kinds: dict[str, set[str]] = defaultdict(set)
    spell_rows: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for row in parsed:
        seen: set[tuple[str, str]] = set()
        for fragment in row.fragments:
            if fragment.kind not in {"institution", "funder"}:
                continue
            key = fragment.key
            key_kinds[key].add(fragment.kind)
            marker = key, fragment.text
            if marker not in seen:
                spell_rows[key][fragment.text].add(row.activity_id)
                seen.add(marker)
    return key_kinds, spell_rows


def _alias_candidates(
    parsed: list[ParsedVenue], lang: LanguageModule, key_kinds: dict[str, set[str]]
) -> tuple[dict[tuple[str, str], set[str]], dict[tuple[str, str], str]]:
    """V5a/V5d pairs that are both institutions, mapped to the artists who wrote them.

    The second map gives, for a pair in which exactly one side is written as an
    acronym (``KAMS``, ``SeMA``), the key of that acronym (N-4).
    """
    candidate_artists: dict[tuple[str, str], set[str]] = defaultdict(set)
    acronym_of: dict[tuple[str, str], str] = {}
    for row in parsed:
        for left, right in row.alias_pairs:
            if not _alias_candidate(left, right):
                continue
            left_fragment, right_fragment = classify_fragment(left, lang), classify_fragment(right, lang)
            if left_fragment.kind != "institution" or right_fragment.kind != "institution":
                continue
            left_key = entity_key(left, row.qualifier, lang)
            right_key = entity_key(right, row.qualifier, lang)
            if left_key == right_key or left_key not in key_kinds or right_key not in key_kinds:
                continue
            pair = tuple(sorted((left_key, right_key)))
            candidate_artists[pair].add(row.ledger_id)
            left_acronym, right_acronym = bool(_acronym_symbols(left)), bool(_acronym_symbols(right))
            if left_acronym != right_acronym:
                acronym_of[pair] = left_key if left_acronym else right_key
    return candidate_artists, acronym_of


def _ambiguous_acronyms(
    acronym_of: dict[tuple[str, str], str], roots: dict[str, str]
) -> dict[str, list[str]]:
    """N-4: acronyms written beside names of two or more different institutions.

    ``roots`` is the entity of each key once every rule except the acronym
    pairs has run. An acronym whose partner names fall in two or more of those
    entities is shared, so it is ambiguous and joins none of them
    (``Example Arts Service (EAS)`` and ``Example Art School (EAS)``).
    Returns each ambiguous acronym key with its partner keys.
    """
    partners: dict[str, set[str]] = defaultdict(set)
    for pair, acronym in acronym_of.items():
        partners[acronym].add(pair[0] if pair[1] == acronym else pair[1])
    return {
        acronym: sorted(names)
        for acronym, names in sorted(partners.items())
        if len({roots.get(name, name) for name in names}) >= 2
    }


def _alias_pair_tiers(
    candidate_artists: dict[tuple[str, str], set[str]], lang: LanguageModule
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Two-artist pairs, then one-artist V5f pairs of specific Hangul and Latin names.

    V5f is weaker than two artists, so the caller applies it only after V5e, and
    never into a group that would then hold two different Hangul names.
    """
    repeated_pairs = [pair for pair, artists in candidate_artists.items() if len(artists) >= 2]
    single_pairs = sorted(
        pair
        for pair, artists in candidate_artists.items()
        if len(artists) == 1
        and venue_names.mostly_hangul(pair[0]) != venue_names.mostly_hangul(pair[1])
        and venue_names.specific(pair[0], lang)
        and venue_names.specific(pair[1], lang)
    )
    return repeated_pairs, single_pairs


def _v5e_components(
    key_kinds: dict[str, set[str]],
    repeated_pairs: list[tuple[str, str]],
    spell_rows: dict[str, dict[str, set[str]]],
    parsed: list[ParsedVenue],
    lang: LanguageModule,
) -> tuple[UnionFind, set[str], list[dict]]:
    """V5e: a component with two distinct mostly-Hangul keys stays unmerged.

    Returns the candidate graph, the blocked roots, and the audit records.
    """
    candidate_graph = UnionFind(set(key_kinds))
    for left, right in repeated_pairs:
        candidate_graph.union(left, right)
    candidate_components: dict[str, set[str]] = defaultdict(set)
    for key in key_kinds:
        candidate_components[candidate_graph.find(key)].add(key)
    blocked_roots = {
        root for root, keys in candidate_components.items() if sum(_mostly_hangul(key) for key in keys) >= 2
    }
    blocked_components: list[dict] = []
    for root in blocked_roots:
        keys = candidate_components[root]
        rows = {row_id for key in keys for row_ids in spell_rows[key].values() for row_id in row_ids}
        artists = {row.ledger_id for row in parsed if row.activity_id in rows}
        names = [min(spell_rows[key].items(), key=_spelling_sort)[0] for key in keys]
        blocked_components.append(
            {
                "n_rows": len(rows),
                "n_artists": len(artists),
                "names": sorted(names, key=lambda name: (not _mostly_hangul(name), institution_key(name, lang))),
            }
        )
    blocked_components.sort(key=lambda component: (-component["n_rows"], -component["n_artists"], component["names"]))
    return candidate_graph, blocked_roots, blocked_components


def _acronym_block_records(
    shared: dict[str, list[str]], spell_rows: dict[str, dict[str, set[str]]], parsed: list[ParsedVenue]
) -> list[dict]:
    """Audit section 6 records of the acronyms N-4 left unjoined."""
    ledger_of = {row.activity_id: row.ledger_id for row in parsed}
    records: list[dict] = []
    for acronym, partners in shared.items():
        keys = [acronym, *partners]
        rows = {row_id for key in keys for row_ids in spell_rows[key].values() for row_id in row_ids}
        records.append(
            {
                "n_rows": len(rows),
                "n_artists": len({ledger_of[row_id] for row_id in rows}),
                "names": [min(spell_rows[key].items(), key=_spelling_sort)[0] for key in keys],
            }
        )
    return records


def _apply_v5(
    key_kinds: dict[str, set[str]],
    repeated_pairs: list[tuple[str, str]],
    single_pairs: list[tuple[str, str]],
    blocked_roots: set[str],
    candidate_graph: UnionFind,
    lang: LanguageModule,
) -> tuple[UnionFind, int, int, list[tuple[str, str, str]], list[tuple[str, str]]]:
    """V5a/V5d outside a V5e block, then V5f. V5f never creates a second Hangul name."""
    qualified_pairs = [
        (left, right) for left, right in repeated_pairs if candidate_graph.find(left) not in blocked_roots
    ]
    union_find = UnionFind(set(key_kinds))
    alias_merges = 0
    v5_merges: list[tuple[str, str, str]] = []
    for left, right in sorted(qualified_pairs):
        if venue_names.forbids_place_office_merge(left, right, lang):
            continue
        if union_find.union(left, right):
            alias_merges += 1
            v5_merges.append((_alias_rule(left, right), left, right))
    hangul_in: dict[str, set[str]] = defaultdict(set)
    for key in key_kinds:
        if _mostly_hangul(key):
            hangul_in[union_find.find(key)].add(key)
    single_merges = 0
    for left, right in single_pairs:
        if venue_names.forbids_place_office_merge(left, right, lang):
            continue
        left_root, right_root = union_find.find(left), union_find.find(right)
        if left_root == right_root or len(hangul_in[left_root] | hangul_in[right_root]) >= 2:
            continue
        union_find.union(left_root, right_root)
        root = union_find.find(left_root)
        hangul_in[root] = hangul_in.pop(left_root, set()) | hangul_in.pop(right_root, set())
        single_merges += 1
        v5_merges.append(("V5f", left, right))
    return union_find, alias_merges, single_merges, v5_merges, qualified_pairs


def _collect_entity_use(
    parsed: list[ParsedVenue], lang: LanguageModule, root_for_key: dict[str, str]
) -> tuple[
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, Counter],
    dict[str, list[tuple[str, str, str, str]]],
    dict[str, str],
]:
    """Rows, artists, and places of each entity, plus the institution key of each activity."""
    entity_rows: dict[str, set[str]] = defaultdict(set)
    entity_artists: dict[str, set[str]] = defaultdict(set)
    entity_places: dict[str, Counter] = defaultdict(Counter)
    row_roots: dict[str, list[tuple[str, str, str, str]]] = {}
    institution_key_by_activity: dict[str, str] = {}
    for row in parsed:
        place = next((fragment.place for fragment in row.fragments if fragment.kind == "place"), None)
        roots: list[tuple[str, str, str, str]] = []
        seen_roots: set[str] = set()
        chosen_institution: str | None = None
        for fragment in row.fragments:
            if fragment.kind not in {"institution", "funder"}:
                continue
            key = fragment.key
            if fragment.kind == "institution" and chosen_institution is None:
                chosen_institution = key
            root = root_for_key[key]
            roots.append((fragment.kind, root, key, fragment.text))
            if root in seen_roots:
                continue
            entity_rows[root].add(row.activity_id)
            entity_artists[root].add(row.ledger_id)
            if place:
                entity_places[root][(place.city, place.country, place.kr_region)] += 1
            seen_roots.add(root)
        institution_key_by_activity[row.activity_id] = chosen_institution or ""
        row_roots[row.activity_id] = roots
    return entity_rows, entity_artists, entity_places, row_roots, institution_key_by_activity


def _name_entities(
    grouped_keys: dict[str, set[str]],
    key_kinds: dict[str, set[str]],
    spell_rows: dict[str, dict[str, set[str]]],
    entity_rows: dict[str, set[str]],
    entity_artists: dict[str, set[str]],
    entity_places: dict[str, Counter],
    lang: LanguageModule,
) -> dict[str, dict]:
    """Display name, aliases, kind, and place of each entity root.

    The name is the key with the most rows among keys V7a/b did not have to
    trim, then that key's most used spelling. A place is kept when at least
    half the entity's rows name it.
    """
    entity_by_root: dict[str, dict] = {}
    for root, keys in grouped_keys.items():
        spellings: dict[str, set[str]] = defaultdict(set)
        kinds: set[str] = set()
        for key in keys:
            kinds.update(key_kinds[key])
            for spelling, row_ids in spell_rows[key].items():
                spellings[spelling].update(row_ids)
        by_key: dict[str, dict[str, set[str]]] = defaultdict(dict)
        for spelling, row_ids in spellings.items():
            by_key[institution_key(spelling, lang)][spelling] = row_ids

        def key_rank(item: tuple[str, dict[str, set[str]]]) -> tuple:
            """Untrimmed key first, then more rows, then Hangul, then the spelling."""
            _key, group = item
            was_trimmed = all(venue_names.trimmed(spelling, lang) for spelling in group)
            rows = set().union(*group.values())
            return was_trimmed, -len(rows), 0 if any(HANGUL_RE.search(spelling) for spelling in group) else 1, min(group)

        ordered_spellings = [
            spelling
            for _key, group in sorted(by_key.items(), key=key_rank)
            for spelling, _ids in sorted(
                group.items(), key=lambda item: (venue_names.trimmed(item[0], lang), *_spelling_sort(item))
            )
        ]
        n_rows = len(entity_rows[root])
        city = country = kr_region = ""
        if entity_places[root]:
            place, count = min(entity_places[root].items(), key=lambda item: (-item[1], item[0]))
            if count / n_rows >= 0.5:
                city, country, kr_region = place
        entity_by_root[root] = {
            "_keys": sorted(keys),
            "_name_key": next(key for key in sorted(keys) if ordered_spellings[0] in spell_rows[key]),
            "name": ordered_spellings[0],
            "_aliases": ordered_spellings[1:],
            "kind": "funder" if "funder" in kinds else "institution",
            "city": city,
            "country": country,
            "kr_region": kr_region,
            "n_rows": n_rows,
            "n_artists": len(entity_artists[root]),
        }
    return entity_by_root


def _rule_text(found: set[str]) -> str:
    return "|".join(rule for rule in RULE_ORDER if rule in found)


def _merge_paths(merges: list[tuple[str, str, str]], entity_by_root: dict[str, dict]) -> dict[str, set[str]]:
    """N-8: the merge rules on the path from each key to the key of its entity's name.

    The merges inside one entity form a tree (a join is recorded only when it
    joins two components), so the path is unique.
    """
    graph: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for rule, left, right in merges:
        graph[left].append((right, rule))
        graph[right].append((left, rule))
    found: dict[str, set[str]] = {}
    for entity in entity_by_root.values():
        start = entity["_name_key"]
        found[start] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            for other, rule in graph.get(node, ()):
                if other not in found:
                    found[other] = found[node] | {rule}
                    stack.append(other)
    return found


def _plain_key(text: str) -> str:
    """The V4 key of ``text`` without V7a–d."""
    token = _V7_SPELLING.set(False)
    try:
        return institution_key(text)
    finally:
        _V7_SPELLING.reset(token)


def _spelling_rules(text: str, key: str, paths: dict[str, set[str]]) -> set[str]:
    """V4; V7 when V7a–d rewrote this spelling; the merge rules from its key to the entity name."""
    respelled = venue_names.name_part(key) != _plain_key(text)
    return {"V4", *(("V7",) if respelled else ()), *paths.get(key, ())}


def _annotate_rows(
    parsed: list[ParsedVenue],
    entity_by_root: dict[str, dict],
    row_roots: dict[str, list[tuple[str, str, str, str]]],
    paths: dict[str, set[str]],
    spell_rows: dict[str, dict[str, set[str]]],
) -> tuple[dict[str, dict[str, str]], Counter, list[dict]]:
    """Number entities ``VEN-`` by row count, and the venue_kind and venue_rule of each activity."""
    # The root key breaks a tie between two entities with one display name and one row count.
    ordered_entities = sorted(
        entity_by_root.items(), key=lambda item: (-item[1]["n_rows"], item[1]["name"], item[0])
    )
    for index, (_, entity) in enumerate(ordered_entities, 1):
        entity["venue_id"] = f"VEN-{index:06d}"
    annotations: dict[str, dict[str, str]] = {}
    venue_kind_counts: Counter = Counter()
    for row in parsed:
        roots = row_roots[row.activity_id]
        institution_root = next((root for kind, root, _key, _text in roots if kind == "institution"), "")
        funder_root = next((root for kind, root, _key, _text in roots if kind == "funder"), "")
        venue_root = institution_root or funder_root
        venue_kind_of_key = "institution" if institution_root else "funder"
        venue_key, venue_text = next(
            ((key, text) for kind, _root, key, text in roots if kind == venue_kind_of_key), ("", "")
        )
        kinds = {fragment.kind for fragment in row.fragments}
        if institution_root:
            venue_kind = "institution"
        elif funder_root:
            venue_kind = "funder"
        elif "online" in kinds:
            venue_kind = "online"
        elif "place" in kinds:
            venue_kind = "place_only"
        elif "title" in kinds:
            venue_kind = "title_only"  # a work title and nothing that names a venue
        elif "unclassified" in kinds:
            venue_kind = "unclassified"  # text that names nothing V3 knows; not an empty venue
        else:
            venue_kind = "empty"
        annotation = {
            "venue_id": entity_by_root[venue_root]["venue_id"] if venue_root else "",
            "funder_id": entity_by_root[funder_root]["venue_id"] if funder_root else "",
            "venue_kind": venue_kind,
            "venue_rule": _rule_text(_spelling_rules(venue_text, venue_key, paths)) if venue_key else "",
        }
        annotations[row.activity_id] = annotation
        venue_kind_counts[venue_kind] += 1
    venue_rows = [
        {
            "venue_id": entity["venue_id"],
            "name": entity["name"],
            "aliases": "|".join(entity["_aliases"]),
            "kind": entity["kind"],
            "city": entity["city"],
            "country": entity["country"],
            "kr_region": entity["kr_region"],
            "n_rows": entity["n_rows"],
            "n_artists": entity["n_artists"],
            "rules": _rule_text(
                {
                    rule
                    for key in entity["_keys"]
                    for spelling in spell_rows[key]
                    for rule in _spelling_rules(spelling, key, paths)
                }
            ),
        }
        for _, entity in ordered_entities
    ]
    return annotations, venue_kind_counts, venue_rows


def _follow(mapping: dict[str, str], venue_id: str) -> str:
    """Walk ``mapping`` until it stops. A cycle keeps the id already seen."""
    seen: set[str] = set()
    while venue_id in mapping and mapping[venue_id] != venue_id and venue_id not in seen:
        seen.add(venue_id)
        venue_id = mapping[venue_id]
    return venue_id


def _fold_display(name: str) -> str:
    """Casefold a display name and keep a leading article. V4 strips ``the``; V4n does not."""
    text = unicodedata.normalize("NFC", name or "").casefold()
    return re.sub(r"\s+", " ", text).strip()


def _add_rules(text: str, extra: set[str]) -> str:
    if not extra:
        return text
    found = {part for part in (text or "").split("|") if part}
    return _rule_text(found | extra)


def _national_generic(key: str, lang: LanguageModule) -> bool:
    """V4n: a generic name marked national by the language module.

    A Hangul marker is a prefix of the spaceless name (국립…). A Latin marker
    is the first token (``national``). Both lists are empty on a module that
    does not define them, and the rule then joins nothing.
    """
    if not key or not venue_names.generic_name(key, lang):
        return False
    words = lang.venue_words
    name = venue_names.name_part(key)
    compact = name.replace(" ", "")
    if any(prefix and compact.startswith(prefix) for prefix in words.national_prefixes):
        return True
    first = name.split()[:1]
    tokens = {token.casefold() for token in words.national_tokens}
    return bool(first) and first[0].casefold() in tokens


def _boundary_patterns(phrases: tuple[str, ...]) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    """Leading and trailing phrase patterns. Longer phrases are tried first.

    The leading form requires whitespace after the phrase, and the trailing
    form requires whitespace before it, so a role word inside a token stays.
    """
    if not phrases:
        return None, None
    body = "|".join(re.escape(phrase) for phrase in sorted(phrases, key=len, reverse=True))
    return (
        re.compile(rf"^(?:{body})\s*[:：·]?\s+", re.IGNORECASE),
        re.compile(rf"\s+(?:{body})\s*$", re.IGNORECASE),
    )


def _strip_boundary(text: str, lead: re.Pattern[str] | None, tail: re.Pattern[str] | None) -> str:
    """Remove a leading or trailing phrase up to three times. Stop if the name would vanish."""
    if lead is None or tail is None:
        return text.strip()
    out = text.strip()
    for _ in range(3):
        nxt = tail.sub("", lead.sub("", out, count=1), count=1).strip(" :：·")
        if nxt == out or sum(char.isalpha() for char in nxt) < 2:
            break
        out = nxt
    return out


def _row_place(row: ParsedVenue) -> Place | None:
    """The row's place: the first city, else the first country. V4n needs both."""
    places = [fragment.place for fragment in row.fragments if fragment.kind == "place" and fragment.place]
    for place in places:
        if place.city:
            return place
    for place in places:
        if place.country:
            return place
    return None


def _chosen_fragment(row: ParsedVenue) -> Fragment | None:
    """The fragment that became the row's venue: the first institution, else the first funder."""
    institution = next((fragment for fragment in row.fragments if fragment.kind == "institution"), None)
    if institution is not None:
        return institution
    return next((fragment for fragment in row.fragments if fragment.kind == "funder"), None)


def _chosen_root(roots: list[tuple[str, str, str, str]]) -> str:
    institution = next((root for kind, root, _key, _text in roots if kind == "institution"), "")
    if institution:
        return institution
    return next((root for kind, root, _key, _text in roots if kind == "funder"), "")


def _bag_parts(bag: tuple[str, ...]) -> tuple[tuple[str, ...], frozenset[str]]:
    proper = tuple(sorted(token for token in bag if token.startswith("~")))
    generic = frozenset(token for token in bag if not token.startswith("~"))
    return proper, generic


def _proper_min(bag: tuple[str, ...]) -> int:
    """Shortest proper skeleton in the bag. The leading ``~`` is not part of the length."""
    lengths = [len(token) - 1 for token in bag if token.startswith("~")]
    return min(lengths) if lengths else 0


def _spellings_in_script(entity: dict, lang: LanguageModule, hangul: bool) -> list[str]:
    """``name`` and aliases whose key is in the partner's script (Hangul or Latin)."""
    texts = [entity["name"], *[part for part in (entity.get("aliases") or "").split("|") if part]]
    kept: list[str] = []
    for text in texts:
        key = institution_key(text, lang)
        if key and venue_names.mostly_hangul(key) == hangul:
            kept.append(text)
    return kept


def _v9u_gate(
    hangul: dict,
    latin: dict,
    bag: tuple[str, ...],
    bags: tuple[tuple[str, ...], ...],
    lang: LanguageModule,
) -> bool:
    """V9u-spell. True when this unique exact pair is one institution.

    Every proper skeleton has length at least 3, the bag has a generic word,
    and two set countries are the same country. The keeper is the entity with
    more rows; a tie keeps the Hangul entity. If that keeper already stores a
    spelling in the partner's script, the partner's key must be one of them.
    If it stores none, the Hangul name has exactly one reading.
    """
    if _proper_min(bag) < 3:
        return False
    if not any(not token.startswith("~") for token in bag):
        return False
    hangul_country = (hangul.get("country") or "").strip()
    latin_country = (latin.get("country") or "").strip()
    if hangul_country and latin_country and hangul_country != latin_country:
        return False
    if int(hangul["n_rows"]) >= int(latin["n_rows"]):
        keep, drop = hangul, latin
    else:
        keep, drop = latin, hangul
    drop_key = institution_key(drop["name"], lang)
    drop_hangul = venue_names.mostly_hangul(drop_key)
    other_script = _spellings_in_script(keep, lang, drop_hangul)
    if other_script and not any(institution_key(text, lang) == drop_key for text in other_script):
        return False
    return bool(other_script) or len(bags) == 1


def _plan_v4n(
    parsed: list[ParsedVenue],
    annotations: dict[str, dict[str, str]],
    by_id: dict[str, dict],
    key_of: dict[str, str],
    lang: LanguageModule,
) -> tuple[dict[str, str], dict[str, str], list[tuple[str, str, str]], set[str]]:
    """V4n-same. Returns the row remap, the absorbed-entity map, the joins, and touched ids.

    Rows of a generic national name that name a city and a country listed in
    the language module's ``national_countries`` share one bucket per country. The bucket joins only when every entity's display fold
    matches, article included. The keeper has the most rows; a tie keeps the
    smaller id. A member is absorbed only when none of its rows stay. The row
    remap moves every row in a joined bucket. The entity map lists only absorbed
    members, which is all a ``funder_id`` may follow.
    """
    rows_of: Counter[str] = Counter(
        annotation["venue_id"] for annotation in annotations.values() if annotation["venue_id"]
    )
    groups: dict[tuple[str, str, str], list[ParsedVenue]] = defaultdict(list)
    for row in parsed:
        venue_id = annotations[row.activity_id]["venue_id"]
        fragment = _chosen_fragment(row)
        place = _row_place(row)
        if (
            venue_id
            and fragment
            and fragment.key
            and _national_generic(fragment.key, lang)
            and place
            and place.city
            and place.country
            and place.country.upper() in {code.upper() for code in lang.venue_words.national_countries}
        ):
            label = ("v4n", venue_names.name_part(fragment.key), place.country.upper())
        else:
            label = ("stay", venue_id, "")
        groups[label].append(row)
    component: dict[str, str] = {}
    join_groups: list[dict] = []
    for label, group in groups.items():
        venue_ids = sorted({annotations[row.activity_id]["venue_id"] for row in group if annotations[row.activity_id]["venue_id"]})
        if label[0] == "stay" or len(venue_ids) <= 1:
            continue
        names = {_fold_display(by_id[venue_id]["name"]) for venue_id in venue_ids if venue_id in by_id}
        if len(names) != 1:
            continue
        keep = min(venue_ids, key=lambda venue_id: (-rows_of[venue_id], venue_id))
        for row in group:
            component[row.activity_id] = keep
        join_groups.append({"members": venue_ids, "keep": keep})
    stay_left: Counter[str] = Counter()
    for row in parsed:
        venue_id = annotations[row.activity_id]["venue_id"]
        if component.get(row.activity_id, venue_id) == venue_id and venue_id:
            stay_left[venue_id] += 1
    absorbed: dict[str, str] = {}
    merges: list[tuple[str, str, str]] = []
    touched: set[str] = set()
    for group in join_groups:
        touched.add(group["keep"])
        touched.update(group["members"])
        for venue_id in group["members"]:
            if venue_id == group["keep"]:
                continue
            merges.append(("V4n", key_of[group["keep"]], key_of[venue_id]))
            if stay_left[venue_id] == 0:
                absorbed[venue_id] = group["keep"]
    return component, absorbed, merges, touched


def _plan_v7f(
    venue_rows: list[dict],
    key_of: dict[str, str],
    lang: LanguageModule,
) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    """V7f-host. Join a display name to the stripped key only when that key exists.

    A collaboration phrase with no host role is left alone. The role word comes
    off only at a token boundary. Nothing is renamed when the stripped name is
    not already an entity. The keeper is the existing entity with the most artists.
    """
    words = lang.venue_words
    host_lead, host_tail = _boundary_patterns(words.host_roles)
    collab_lead, collab_tail = _boundary_patterns(words.collaboration_phrases)
    by_key: dict[str, list[dict]] = defaultdict(list)
    for entity in venue_rows:
        by_key[institution_key(entity["name"], lang)].append(entity)
    mapping: dict[str, str] = {}
    merges: list[tuple[str, str, str]] = []
    for entity in venue_rows:
        name = entity["name"]
        collab = bool(
            (collab_lead and collab_lead.search(name)) or (collab_tail and collab_tail.search(name))
        )
        host = bool((host_lead and host_lead.search(name)) or (host_tail and host_tail.search(name)))
        if collab and not host:
            continue
        if not host:
            continue
        stripped = _strip_boundary(name, host_lead, host_tail)
        if stripped == name:
            continue
        owners = [
            other
            for other in by_key.get(institution_key(stripped, lang), [])
            if other["venue_id"] != entity["venue_id"]
        ]
        if not owners:
            continue
        best = max(owners, key=lambda other: int(other["n_artists"]))
        mapping[entity["venue_id"]] = best["venue_id"]
        merges.append(("V7f", key_of[best["venue_id"]], key_of[entity["venue_id"]]))
    return mapping, merges


def _plan_v9u(
    venue_rows: list[dict],
    key_of: dict[str, str],
    lang: LanguageModule,
) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    """V9u-spell. One unique exact Hangul–Latin pair that passes :func:`_v9u_gate`.

    The first four Hangul readings are the ones that may match, which is the
    bound the measurement used. A pair both sides already share with someone
    else is not unique and is not joined.
    """
    latin_index: dict[tuple[str, ...], list[tuple[dict, frozenset[str]]]] = defaultdict(list)
    hangul_entries: list[tuple[dict, tuple[tuple[str, ...], ...]]] = []
    for entity in venue_rows:
        key = institution_key(entity["name"], lang)
        if venue_names.is_qualified(key):
            continue
        if venue_names.mostly_hangul(key):
            bags = venue_names.hangul_bags(key, lang)
            if bags:
                hangul_entries.append((entity, bags))
        else:
            bag = venue_names.latin_bag(key, lang, True)
            if not bag:
                continue
            proper, generic = _bag_parts(bag)
            if proper:
                latin_index[proper].append((entity, generic))
    exact: list[tuple[dict, dict, tuple[str, ...], tuple[tuple[str, ...], ...]]] = []
    for entity, bags in hangul_entries:
        for bag in bags[:4]:
            proper, generic = _bag_parts(bag)
            if not proper:
                continue
            for other, other_generic in latin_index.get(proper, []):
                if other["venue_id"] == entity["venue_id"]:
                    continue
                if generic == other_generic:
                    exact.append((entity, other, bag, bags))
    best: dict[tuple[str, str], tuple] = {}
    for item in exact:
        ident = tuple(sorted((item[0]["venue_id"], item[1]["venue_id"])))
        best.setdefault(ident, item)
    pairs = list(best.values())
    hangul_n: Counter[str] = Counter(item[0]["venue_id"] for item in pairs)
    latin_n: Counter[str] = Counter(item[1]["venue_id"] for item in pairs)
    unique = [
        item for item in pairs if hangul_n[item[0]["venue_id"]] == 1 and latin_n[item[1]["venue_id"]] == 1
    ]
    mapping: dict[str, str] = {}
    merges: list[tuple[str, str, str]] = []
    for hangul, latin, bag, bags in unique:
        if not _v9u_gate(hangul, latin, bag, bags, lang):
            continue
        if int(hangul["n_rows"]) >= int(latin["n_rows"]):
            keep, drop = hangul, latin
        else:
            keep, drop = latin, hangul
        mapping[drop["venue_id"]] = keep["venue_id"]
        merges.append(("V9u", key_of[keep["venue_id"]], key_of[drop["venue_id"]]))
    return mapping, merges


def _remember_alias(keeper: dict, source: dict) -> None:
    """Keep the absorbed display name on the keeper when the fold is not already there."""
    have = {_fold_display(keeper["name"])}
    have.update(_fold_display(item) for item in keeper["_aliases"])
    for item in [source["name"], *source["_aliases"]]:
        folded = _fold_display(item)
        if item and folded not in have:
            keeper["_aliases"].append(item)
            have.add(folded)


def _recount_entities(
    parsed: list[ParsedVenue],
    row_roots: dict[str, list[tuple[str, str, str, str]]],
    entity_by_root: dict[str, dict],
    component: dict[str, str],
    v4_map: dict[str, str],
    v7_map: dict[str, str],
    v9_map: dict[str, str],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Rows and artists of each final entity id after the three maps.

    V4n's row remap applies only to the fragment that is the row's venue.
    Every other fragment follows the absorbed-entity map. V7f then V9u apply
    to all of them.
    """
    id_of = {root: entity["venue_id"] for root, entity in entity_by_root.items()}
    rows_of: dict[str, set[str]] = defaultdict(set)
    artists_of: dict[str, set[str]] = defaultdict(set)
    for row in parsed:
        roots = row_roots.get(row.activity_id, [])
        chosen = _chosen_root(roots)
        seen: set[str] = set()
        for kind, root, _key, _text in roots:
            if kind not in {"institution", "funder"}:
                continue
            venue_id = id_of[root]
            if root == chosen:
                venue_id = component.get(row.activity_id, venue_id)
            else:
                venue_id = _follow(v4_map, venue_id)
            venue_id = _follow(v9_map, _follow(v7_map, venue_id))
            if not venue_id or venue_id in seen:
                continue
            seen.add(venue_id)
            rows_of[venue_id].add(row.activity_id)
            artists_of[venue_id].add(row.ledger_id)
    return rows_of, artists_of


def _apply_stacked_entity_rules(
    parsed: list[ParsedVenue],
    annotations: dict[str, dict[str, str]],
    venue_rows: list[dict],
    entity_by_root: dict[str, dict],
    row_roots: dict[str, list[tuple[str, str, str, str]]],
    lang: LanguageModule,
    rules: frozenset[str],
) -> tuple[list[tuple[str, str, str]], set[str], dict[str, str], list[dict]]:
    """V4n on the activity row, then V7f, then V9u on entity ids.

    A rule absent from ``rules`` is not applied. ``funder_id`` follows the
    entity maps (absorbed V4n members, then V7f, then V9u) and not the V4n row
    remap. Returns the new joins, the ids removed from ``venues.csv``, the
    redirect the audit prints, and the venue rows that remain.
    """
    by_id = {row["venue_id"]: row for row in venue_rows}
    key_of = {entity["venue_id"]: entity["_name_key"] for entity in entity_by_root.values()}
    entity_of = {entity["venue_id"]: entity for entity in entity_by_root.values()}
    if "V4n" in rules:
        component, v4_map, v4_merges, touched = _plan_v4n(parsed, annotations, by_id, key_of, lang)
    else:
        component, v4_map, v4_merges, touched = {}, {}, [], set()
    v7_map, v7_merges = _plan_v7f(venue_rows, key_of, lang) if "V7f" in rules else ({}, [])
    v9_map, v9_merges = _plan_v9u(venue_rows, key_of, lang) if "V9u" in rules else ({}, [])
    merges = [*v4_merges, *v7_merges, *v9_merges]
    for row in parsed:
        annotation = annotations[row.activity_id]
        venue_id = annotation["venue_id"]
        funder_id = annotation["funder_id"]
        after_v4 = component.get(row.activity_id, venue_id) if venue_id else venue_id
        after_v7 = _follow(v7_map, after_v4) if after_v4 else after_v4
        after_v9 = _follow(v9_map, after_v7) if after_v7 else after_v7
        added: set[str] = set()
        if venue_id and after_v4 != venue_id:
            added.add("V4n")
        if after_v4 and after_v7 != after_v4:
            added.add("V7f")
        if after_v7 and after_v9 != after_v7:
            added.add("V9u")
        annotation["venue_id"] = after_v9
        if funder_id:
            annotation["funder_id"] = _follow(v9_map, _follow(v7_map, _follow(v4_map, funder_id)))
        if added:
            annotation["venue_rule"] = _add_rules(annotation["venue_rule"], added)
    redirect: dict[str, str] = {}
    for source in (*v4_map, *v7_map, *v9_map):
        redirect[source] = _follow(v9_map, _follow(v7_map, _follow(v4_map, source)))
    referenced = {
        annotation["venue_id"] for annotation in annotations.values() if annotation["venue_id"]
    } | {annotation["funder_id"] for annotation in annotations.values() if annotation["funder_id"]}
    absorbed = {venue_id for venue_id, target in redirect.items() if target != venue_id and venue_id not in referenced}
    for source in sorted(absorbed):
        target = redirect[source]
        if target in entity_of and source in entity_of:
            _remember_alias(entity_of[target], entity_of[source])
    rows_of, artists_of = _recount_entities(
        parsed, row_roots, entity_by_root, component, v4_map, v7_map, v9_map
    )
    touched.update(v7_map.values())
    touched.update(v9_map.values())
    id_of_key = {name_key: venue_id for venue_id, name_key in key_of.items()}
    rules_on: dict[str, set[str]] = defaultdict(set)
    for rule, kept_key, _joined_key in merges:
        keeper_id = redirect.get(id_of_key[kept_key], id_of_key[kept_key])
        rules_on[keeper_id].add(rule)
    for row in venue_rows:
        venue_id = row["venue_id"]
        if venue_id not in touched or venue_id in absorbed:
            continue
        entity = entity_of[venue_id]
        entity["n_rows"] = len(rows_of.get(venue_id, ()))
        entity["n_artists"] = len(artists_of.get(venue_id, ()))
        row["n_rows"] = entity["n_rows"]
        row["n_artists"] = entity["n_artists"]
        row["aliases"] = "|".join(entity["_aliases"])
        row["rules"] = _add_rules(row["rules"], rules_on.get(venue_id, set()))
    kept = [row for row in venue_rows if row["venue_id"] not in absorbed]
    return merges, absorbed, redirect, kept


def _apply_v12(
    activity_rows: list[dict],
    parsed: list[ParsedVenue],
    annotations: dict[str, dict[str, str]],
    venue_rows: list[dict],
    entity_by_root: dict[str, dict],
    row_roots: dict[str, list[tuple[str, str, str, str]]],
    redirect: dict[str, str],
    lang: LanguageModule,
    links: dict[str, str],
) -> tuple[list[tuple[str, str, str]], set[str], dict[str, str], list[dict]]:
    """V12 on the entities V9u left separate, then V12k on those components.

    The country fill has not run. Activity countries are the V1 country of the
    venue string. ``funder_id`` follows the entity map. Returns the new joins
    (V12, V12i, and V12k), the ids removed from ``venues.csv``, the redirect
    extended with those ids, and the venue rows that remain.
    """
    from giye.normalize.rules import venue_place
    from giye.normalize.venue_alias import plan_v12

    by_activity = {row.get("activity_id", ""): row for row in activity_rows}
    entity_of = {entity["venue_id"]: entity for entity in entity_by_root.values()}
    current = {
        row["venue_id"]: {
            "name": row["name"],
            "aliases": [part for part in (row.get("aliases") or "").split("|") if part],
            "kind": row["kind"],
            "city": row.get("city") or "",
            "n_artists": int(row["n_artists"]),
            "n_rows": int(row["n_rows"]),
        }
        for row in venue_rows
    }
    evidence = []
    for item in parsed:
        raw = by_activity.get(item.activity_id, {})
        if raw.get("publishable") != "yes":
            continue
        annotation = annotations[item.activity_id]
        evidence.append(
            {
                "ledger_id": item.ledger_id,
                "venue_id": annotation["venue_id"],
                "funder_id": annotation["funder_id"],
                "venue_kind": annotation["venue_kind"],
                "venue_norm": item.norm,
                "alias_pairs": item.alias_pairs,
                "venue_country": venue_place(item.norm, lang.gazetteer)[0],
                "event_link": links.get(item.activity_id) or raw.get("event_link") or "",
            }
        )
    plan = plan_v12(current, evidence, lang)
    rules_for: dict[str, set[str]] = defaultdict(set)
    merges: list[tuple[str, str, str]] = []
    for rule, keeper_id, dropped_id in plan.joins:
        rules_for[dropped_id].add(rule)
        merges.append((rule, entity_of[keeper_id]["_name_key"], entity_of[dropped_id]["_name_key"]))
    for keeper_id, (old, new) in plan.kinds.items():
        merges.append(("V12k", entity_of[keeper_id]["_name_key"], f"{old} -> {new}"))
    for annotation in annotations.values():
        added: set[str] = set()
        for column in ("venue_id", "funder_id"):
            venue_id = annotation[column]
            if not venue_id:
                continue
            nxt = plan.redirect.get(venue_id, venue_id)
            if nxt != venue_id:
                added |= rules_for.get(venue_id, set())
                annotation[column] = nxt
            if nxt in plan.kinds:
                added.add("V12k")
        if added:
            annotation["venue_rule"] = _add_rules(annotation["venue_rule"], added)
    for source, target in list(redirect.items()):
        redirect[source] = plan.redirect.get(target, target)
    redirect.update(plan.redirect)
    for source, target in plan.redirect.items():
        if source in entity_of and target in entity_of:
            _remember_alias(entity_of[target], entity_of[source])
    id_of = {root: entity["venue_id"] for root, entity in entity_by_root.items()}
    rows_of: dict[str, set[str]] = defaultdict(set)
    artists_of: dict[str, set[str]] = defaultdict(set)
    for item in parsed:
        roots = row_roots.get(item.activity_id, [])
        chosen = _chosen_root(roots)
        seen: set[str] = set()
        for kind, root, _key, _text in roots:
            if kind not in {"institution", "funder"}:
                continue
            base = id_of[root]
            if root == chosen:
                venue_id = annotations[item.activity_id]["venue_id"] or base
            else:
                mapped = redirect.get(base, base)
                venue_id = mapped
            if not venue_id or venue_id in seen:
                continue
            seen.add(venue_id)
            rows_of[venue_id].add(item.activity_id)
            artists_of[venue_id].add(item.ledger_id)
    referenced = {annotation["venue_id"] for annotation in annotations.values() if annotation["venue_id"]} | {
        annotation["funder_id"] for annotation in annotations.values() if annotation["funder_id"]
    }
    absorbed = {venue_id for venue_id in plan.redirect if venue_id not in referenced}
    rules_on: dict[str, set[str]] = defaultdict(set)
    for rule, keeper_id, _dropped in plan.joins:
        rules_on[keeper_id].add(rule)
    for keeper_id in plan.kinds:
        rules_on[keeper_id].add("V12k")
        entity_of[keeper_id]["kind"] = plan.kinds[keeper_id][1]
        entity_of[keeper_id]["_v12k"] = plan.kinds[keeper_id]
    touched = set(rules_on) | set(plan.redirect.values())
    for row in venue_rows:
        venue_id = row["venue_id"]
        if venue_id in absorbed or venue_id not in touched:
            continue
        entity = entity_of[venue_id]
        entity["n_rows"] = len(rows_of.get(venue_id, ()))
        entity["n_artists"] = len(artists_of.get(venue_id, ()))
        row["n_rows"] = entity["n_rows"]
        row["n_artists"] = entity["n_artists"]
        row["kind"] = entity["kind"]
        row["aliases"] = "|".join(entity["_aliases"])
        row["rules"] = _add_rules(row["rules"], rules_on.get(venue_id, set()))
    kept = [row for row in venue_rows if row["venue_id"] not in absorbed]
    return merges, absorbed, redirect, kept


def _resolve(
    activity_rows: list[dict],
    out_dir: Path | None,
    rules: frozenset[str],
    write: bool,
    lang: LanguageModule,
    frames: list[tuple[str, str, str]],
    links: dict[str, str],
) -> BuildResult:
    """V2–V6, then whichever of V7e/V8/V9 are in ``rules``. The caller sets V7 spelling.

    Rows are processed in activity-id order, so the entities, their ids and
    names, and the audit sample do not depend on the order of the ledger rows.
    """
    parsed = sorted((parse_venue(row, lang) for row in activity_rows), key=lambda row: (row.activity_id, row.norm))
    key_kinds, spell_rows = _index_named_fragments(parsed, lang)
    candidate_artists, acronym_of = _alias_candidates(parsed, lang, key_kinds)
    repeated_pairs, single_pairs = _alias_pair_tiers(candidate_artists, lang)
    candidate_graph, blocked_roots, blocked_components = _v5e_components(
        key_kinds, repeated_pairs, spell_rows, parsed, lang
    )
    # N-4: a dry run without the acronym pairs gives each name its entity; an
    # acronym beside names of two entities is shared and its pairs are dropped.
    dry, *_unused = _apply_v5(
        key_kinds,
        [pair for pair in repeated_pairs if pair not in acronym_of],
        [pair for pair in single_pairs if pair not in acronym_of],
        blocked_roots,
        candidate_graph,
        lang,
    )
    _name_rule_merges(dry, set(key_kinds), spell_rows, parsed, lang, rules)
    shared = _ambiguous_acronyms(acronym_of, {key: dry.find(key) for key in key_kinds})
    dropped = {pair for pair, acronym in acronym_of.items() if acronym in shared}
    blocked_components.extend(_acronym_block_records(shared, spell_rows, parsed))
    union_find, alias_merges, single_merges, v5_merges, qualified_pairs = _apply_v5(
        key_kinds,
        [pair for pair in repeated_pairs if pair not in dropped],
        [pair for pair in single_pairs if pair not in dropped],
        blocked_roots,
        candidate_graph,
        lang,
    )
    root_before_name_rules = {key: union_find.find(key) for key in key_kinds}
    rule_merges = _name_rule_merges(union_find, set(key_kinds), spell_rows, parsed, lang, rules)
    all_merges = v5_merges + rule_merges
    root_for_key = {key: union_find.find(key) for key in key_kinds}
    grouped_keys: dict[str, set[str]] = defaultdict(set)
    for key, root in root_for_key.items():
        grouped_keys[root].add(key)
    alias_roots = {
        union_find.find(left)
        for left, right in qualified_pairs
        if union_find.find(left) == union_find.find(right)
    }
    entity_rows, entity_artists, entity_places, row_roots, institution_key_by_activity = _collect_entity_use(
        parsed, lang, root_for_key
    )
    entity_by_root = _name_entities(
        grouped_keys, key_kinds, spell_rows, entity_rows, entity_artists, entity_places, lang
    )
    paths = _merge_paths(all_merges, entity_by_root)
    annotations, venue_kind_counts, venue_rows = _annotate_rows(parsed, entity_by_root, row_roots, paths, spell_rows)
    extra, absorbed, redirect, venue_rows = _apply_stacked_entity_rules(
        parsed, annotations, venue_rows, entity_by_root, row_roots, lang, rules
    )
    if "V12" in rules:
        v12_merges, v12_absorbed, redirect, venue_rows = _apply_v12(
            activity_rows, parsed, annotations, venue_rows, entity_by_root, row_roots, redirect, lang, links
        )
        extra = [*extra, *v12_merges]
        absorbed |= v12_absorbed
    all_merges = [*all_merges, *extra]
    rule_merges = [*rule_merges, *extra]
    # Imported here: country fill reads venue rows this function has just built.
    from giye.normalize.country import apply_country_fill

    country = apply_country_fill(
        parsed,
        annotations,
        venue_rows,
        row_roots,
        redirect,
        entity_by_root,
        lang,
        frames,
        key_of=lambda text: institution_key(text, lang),
        pairs_of=bracketed_pairs,
        rule_order=RULE_ORDER,
    )
    if write:
        assert out_dir is not None
        _write_csv(out_dir / "venues.csv", venue_rows)
        _write_csv(
            out_dir / "venue_merges.csv",
            [
                {
                    "rule": rule,
                    "venue_id": redirect.get(
                        entity_by_root[root_for_key[left]]["venue_id"],
                        entity_by_root[root_for_key[left]]["venue_id"],
                    ),
                    "kept_key": _display_key(left),
                    "joined_key": _display_key(right),
                }
                for rule, left, right in all_merges
            ],
            MERGE_FIELDS,
        )
        (out_dir / "venue_audit.md").write_text(
            _audit_text(
                parsed,
                annotations,
                entity_by_root,
                root_for_key,
                alias_roots,
                blocked_components,
                all_merges,
                spell_rows,
                lang,
                absorbed,
                redirect,
            ).rstrip()
            + "\n"
            + "\n".join(country["audit"]),
            encoding="utf-8",
        )
    stats = {
        "entities": len(venue_rows),
        "shared_entities": sum(entity["n_artists"] >= 2 for entity in venue_rows),
        "venue_kind": dict(sorted(venue_kind_counts.items())),
        "alias_merges": alias_merges,
        "single_artist_alias_merges": single_merges,
        "name_rule_merges": dict(sorted(Counter(rule for rule, _left, _right in rule_merges).items())),
        "qualified_alias_pairs": len(qualified_pairs),
        "alias_entities": len(alias_roots),
        "blocked_alias_components": len(blocked_components),
        "country_fill": country["counts"],
    }
    return BuildResult(
        annotations=annotations,
        venues=venue_rows,
        stats=stats,
        institution_key_by_activity=institution_key_by_activity,
        root_before_name_rules=root_before_name_rules,
        name_rule_merges=rule_merges,
        merges=all_merges,
        country_fills=country["cv_fills"],
    )
