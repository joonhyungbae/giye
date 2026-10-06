# SPDX-License-Identifier: AGPL-3.0-only
"""Resolve activity venue strings into institution and funder entities (P3, V1–V9).

Deterministic string rules and the language module's gazetteer only: no fuzzy
matching, no embeddings, no network, no ledger edits. ``build`` writes
``venues.csv`` and ``venue_audit.md``. The audit lists every merge with its
rule id (V5a, V5d, V5f, V7e, V8, V9).

``name_rules`` turns V7–V9 off for an ablation. Leaving it unset keeps those
rules on, which is what ``giye normalize`` does unless the config or
``--venue-name-rules`` says otherwise.
"""

from __future__ import annotations

import contextvars
import csv
import random
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from giye.normalize import venue_names
from giye.normalize.language import LanguageModule, load_language
from giye.normalize.rules import norm_text

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
]
SPLIT_CHARS = {",", "/", "|", "·", ";", "\x1f"}
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
NAME_RULES = frozenset({"V7", "V8", "V9"})


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


@dataclass
class ParsedVenue:
    """One activity row after the V2 split and V3 classification."""

    activity_id: str
    ledger_id: str
    raw: str
    norm: str
    fragments: list[Fragment]
    alias_pairs: list[tuple[str, str]]


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
    """V2 separators and round parentheses, preserving the fragments' source order."""
    text = re.sub(r"\s+-\s+", "\x1f", text)
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
        elif char in "(（":
            flush()
            stack.append((fragments[-1] if fragments else "", len(fragments)))
        elif char in ")）":
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
    """
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
    key = re.sub(r"\s+", " ", "".join(kept)).strip()
    key = re.sub(r"^the\s+", "", key)
    key = re.sub(r"\s+the$", "", key)
    if _V7_SPELLING.get():
        return venue_names.normalize_key(key.strip(), lang)  # V7b–d
    return key.strip()


def parse_venue(row: dict, lang: LanguageModule) -> ParsedVenue:
    """V2 split and V3 classification of one activity row's venue."""
    venue_norm = norm_text(row.get("venue"))
    pieces, alias_pairs = split_venue(venue_norm)
    return ParsedVenue(
        activity_id=row["activity_id"],
        ledger_id=row["ledger_id"],
        raw=row.get("venue") or "",
        norm=venue_norm,
        fragments=classify_fragments(pieces, lang),
        alias_pairs=alias_pairs,
    )


def _spelling_sort(item: tuple[str, set[str]]) -> tuple[int, int, str]:
    """Most-used spelling first, then a Hangul spelling, then the text."""
    spelling, row_ids = item
    return -len(row_ids), 0 if HANGUL_RE.search(spelling) else 1, spelling


def _write_csv(path: Path, rows: list[dict]) -> None:
    """Write ``venues.csv`` with ``VENUE_FIELDS`` and a newline after every row."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=VENUE_FIELDS, lineterminator="\n")
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
) -> str:
    """One fragment as the audit prints it: a place, an entity id, or a bare kind."""
    if fragment.kind == "place" and fragment.place:
        place = fragment.place
        detail = ", ".join(value for value in (place.city, place.country, place.kr_region) if value)
        return f"`{_audit_clean(fragment.text)}` → place ({detail})"
    if fragment.kind in {"institution", "funder"}:
        root = root_for_key[institution_key(fragment.text, lang)]
        entity = entity_by_root[root]
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
        "Rules G1–G6 · V2–V9. Random sample seed: `20260925`.",
        "Section 7 lists every merge and the rule that made it.",
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
                        _audit_fragment_line(fragment, root_for_key, entity_by_root, lang)
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


def _audit_alias_lines(entity_by_root: dict[str, dict], alias_roots: set[str]) -> list[str]:
    """Section 2. The thirty alias-merged entities with the most rows."""
    alias_entities = sorted(
        (entity_by_root[root] for root in alias_roots),
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


def _audit_largest_lines(entity_by_root: dict[str, dict]) -> list[str]:
    """Section 3. The forty entities with the most rows."""
    lines = ["", "## 3. Top 40 entities by row count", ""]
    for entity in sorted(entity_by_root.values(), key=lambda item: (-item["n_rows"], item["name"]))[:40]:
        lines.append(
            f"- {entity['venue_id']} · {_audit_clean(entity['name'])} · {entity['kind']} · "
            f"{entity['n_rows']} rows · {entity['n_artists']} artists"
        )
    return lines


def _audit_funder_lines(entity_by_root: dict[str, dict]) -> list[str]:
    """Section 4. The thirty funders with the most rows."""
    lines = ["", "## 4. Top 30 funders", ""]
    funders = [entity for entity in entity_by_root.values() if entity["kind"] == "funder"]
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
            key = institution_key(fragment.text, lang)
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
            "the whole component is left unmerged."
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
            "V9 Hangul reading equals the Latin bag, one reading per component."
        ),
        "Format: rule · kept spelling (row count) ← joined spelling (row count).",
    ]
    for rule in ("V5a", "V5d", "V5f", "V9", "V8", "V7e"):
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
) -> str:
    """Audit markdown. Section 7 lists every merge, not a sample, each with its rule id."""
    lines = _audit_sample_lines(parsed, annotations, entity_by_root, root_for_key, lang)
    lines.extend(_audit_alias_lines(entity_by_root, alias_roots))
    lines.extend(_audit_largest_lines(entity_by_root))
    lines.extend(_audit_funder_lines(entity_by_root))
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
        bag = venue_names.latin_bag(key, lang)
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
                key_cities[institution_key(fragment.text, lang)][cities[0]] += 1
    acronym_sites: dict[str, set[str]] = defaultdict(set)
    branch_sites: dict[str, set[str]] = defaultdict(set)
    acronyms = venue_names.known_acronyms(spell_rows)
    for key in keys:
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
        bag = venue_names.latin_bag(key, lang, cross_script=True)
        if bag:
            latin_roots[bag].add(key)
    # The first Hangul reading that matches anything decides (현대 = contemporary before modern).
    edges: list[tuple[str, str, tuple]] = []
    for key in sorted(keys):
        for bag in venue_names.hangul_bags(key, lang):
            hits = sorted({union_find.find(latin) for latin in latin_roots.get(bag, ())})
            if hits:
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
) -> BuildResult:
    """Build venue entities from activity rows.

    ``name_rules=None`` applies V7 (spelling and V7e), V8 and V9. ``write=False``
    skips ``venues.csv`` and ``venue_audit.md``. ``lang`` defaults to the
    Korean–English module.
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
        return _resolve(activity_rows, out_dir, rules, write, language)
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
            key = institution_key(fragment.text, lang)
            key_kinds[key].add(fragment.kind)
            marker = key, fragment.text
            if marker not in seen:
                spell_rows[key][fragment.text].add(row.activity_id)
                seen.add(marker)
    return key_kinds, spell_rows


def _alias_candidates(
    parsed: list[ParsedVenue], lang: LanguageModule, key_kinds: dict[str, set[str]]
) -> dict[tuple[str, str], set[str]]:
    """V5a/V5d pairs that are both institutions, mapped to the artists who wrote them."""
    candidate_artists: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in parsed:
        for left, right in row.alias_pairs:
            if not _alias_candidate(left, right):
                continue
            left_fragment, right_fragment = classify_fragment(left, lang), classify_fragment(right, lang)
            if left_fragment.kind != "institution" or right_fragment.kind != "institution":
                continue
            left_key, right_key = institution_key(left, lang), institution_key(right, lang)
            if left_key == right_key or left_key not in key_kinds or right_key not in key_kinds:
                continue
            candidate_artists[tuple(sorted((left_key, right_key)))].add(row.ledger_id)
    return candidate_artists


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
    dict[str, list[tuple[str, str]]],
    dict[str, str],
]:
    """Rows, artists, and places of each entity, plus the institution key of each activity."""
    entity_rows: dict[str, set[str]] = defaultdict(set)
    entity_artists: dict[str, set[str]] = defaultdict(set)
    entity_places: dict[str, Counter] = defaultdict(Counter)
    row_roots: dict[str, list[tuple[str, str]]] = {}
    institution_key_by_activity: dict[str, str] = {}
    for row in parsed:
        place = next((fragment.place for fragment in row.fragments if fragment.kind == "place"), None)
        roots: list[tuple[str, str]] = []
        seen_roots: set[str] = set()
        chosen_institution: str | None = None
        for fragment in row.fragments:
            if fragment.kind not in {"institution", "funder"}:
                continue
            key = institution_key(fragment.text, lang)
            if fragment.kind == "institution" and chosen_institution is None:
                chosen_institution = key
            root = root_for_key[key]
            roots.append((fragment.kind, root))
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


def _annotate_rows(
    parsed: list[ParsedVenue], entity_by_root: dict[str, dict], row_roots: dict[str, list[tuple[str, str]]]
) -> tuple[dict[str, dict[str, str]], Counter, list[dict]]:
    """Number entities ``VEN-`` by row count, and the venue_kind of each activity."""
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
        institution_root = next((root for kind, root in roots if kind == "institution"), "")
        funder_root = next((root for kind, root in roots if kind == "funder"), "")
        venue_root = institution_root or funder_root
        kinds = {fragment.kind for fragment in row.fragments}
        if institution_root:
            venue_kind = "institution"
        elif funder_root:
            venue_kind = "funder"
        elif "online" in kinds:
            venue_kind = "online"
        elif "place" in kinds:
            venue_kind = "place_only"
        else:
            venue_kind = "empty"
        annotation = {
            "venue_id": entity_by_root[venue_root]["venue_id"] if venue_root else "",
            "funder_id": entity_by_root[funder_root]["venue_id"] if funder_root else "",
            "venue_kind": venue_kind,
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
        }
        for _, entity in ordered_entities
    ]
    return annotations, venue_kind_counts, venue_rows


def _resolve(
    activity_rows: list[dict],
    out_dir: Path | None,
    rules: frozenset[str],
    write: bool,
    lang: LanguageModule,
) -> BuildResult:
    """V2–V6, then whichever of V7e/V8/V9 are in ``rules``. The caller sets V7 spelling.

    Rows are processed in activity-id order, so the entities, their ids and
    names, and the audit sample do not depend on the order of the ledger rows.
    """
    parsed = sorted((parse_venue(row, lang) for row in activity_rows), key=lambda row: (row.activity_id, row.norm))
    key_kinds, spell_rows = _index_named_fragments(parsed, lang)
    candidate_artists = _alias_candidates(parsed, lang, key_kinds)
    repeated_pairs, single_pairs = _alias_pair_tiers(candidate_artists, lang)
    candidate_graph, blocked_roots, blocked_components = _v5e_components(
        key_kinds, repeated_pairs, spell_rows, parsed, lang
    )
    union_find, alias_merges, single_merges, v5_merges, qualified_pairs = _apply_v5(
        key_kinds, repeated_pairs, single_pairs, blocked_roots, candidate_graph, lang
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
    annotations, venue_kind_counts, venue_rows = _annotate_rows(parsed, entity_by_root, row_roots)
    if write:
        assert out_dir is not None
        _write_csv(out_dir / "venues.csv", venue_rows)
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
            ),
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
    }
    return BuildResult(
        annotations=annotations,
        venues=venue_rows,
        stats=stats,
        institution_key_by_activity=institution_key_by_activity,
        root_before_name_rules=root_before_name_rules,
        name_rule_merges=rule_merges,
        merges=all_merges,
    )
