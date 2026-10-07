# SPDX-License-Identifier: AGPL-3.0-only
"""V12: two venue entities are one institution when a same-row alias says so.

V5 trusts a parenthetical pair and drops a component that holds two Hangul
names. V12 is the residual rule. It runs after V4n, V7f and V9u, on entities
that are still separate, and before the country fill so that fill sees the
merged entities. Channels are V12h (a short Hangul nickname), V12b (two or
more people), V12c (one person and two shared editions) and V12i (exact
initials). V12i then drops a pair the acronym guard kept when a second
witness contradicts it. V12k sets the keeper's kind from the majority
``venue_kind`` on the component. There is no bilingual one-to-one channel,
and a shared edition without a same-row alias is not a join.

Words and organisation classes come from the language module. The thresholds
below are the ones the alias measurement kept.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from giye.normalize.language import LanguageModule
from giye.normalize.venue_names import generic_name
from giye.normalize.venues import (
    _acronym_symbols,
    _latin_initials,
    classify_fragment,
    institution_key,
)

# A Hangul nickname is at most four syllables and at least two; the official
# name beside it is at least eight. Every support>=2 Hangul pair except the
# festival nickname puts an organisation word on one side, or is longer than four.
HANGUL_NICKNAME_MAX = 4
HANGUL_OFFICIAL_MIN = 8
# V12b needs two people. Below three people a fully Latin expansion must contain
# the acronym's letters in order: two people drop a slash-split fragment, three
# keep a name whose letter comes from another language (HEK).
V12B_MIN_PEOPLE = 2
V12B_LETTER_BELOW = 3
# V12c needs one same-row person and two shared editions. An edition alone is not a join.
V12C_MIN_PEOPLE = 1
V12C_MIN_EDITIONS = 2
# V12h is the Hangul nickname. V12i needs one person and exact initials, not a letter subsequence.
V12H_MIN_PEOPLE = 2
V12I_MIN_PEOPLE = 1
# A rival stem shorter than four letters is a shared syllable, not a second institution.
RIVAL_STEM_MIN = 4
# Two stem sets are one spelling when the shared fraction is at least this (Berkley/Berkeley).
SPELLING_OVERLAP = 0.8

# A Hangul or Japanese expansion has no Latin initials. V12b's letter test does not apply to it.
_NONLATIN = re.compile(r"[가-힣\u3040-\u30ff\u4e00-\u9fff]")
_HANGUL = re.compile(r"[가-힣]")
_KIND_OK = frozenset({"institution", "funder"})


@dataclass
class AliasPlan:
    """Joins and kind votes. Ids are the venue ids from before V12."""

    # Dropped id → component keeper. Only ids that move.
    redirect: dict[str, str] = field(default_factory=dict)
    # (rule, keeper_id, dropped_id). One row per channel that kept the edge.
    joins: list[tuple[str, str, str]] = field(default_factory=list)
    # Keeper id → (kind before, kind after). Only a real change.
    kinds: dict[str, tuple[str, str]] = field(default_factory=dict)


def _org_markers(name: str, lang: LanguageModule) -> frozenset[str]:
    """Organisation classes named by ``name``. Empty when the module defines none."""
    found = set()
    for item in lang.venue_words.org_classes:
        if any(word in name for word in item.hangul) or re.search(item.latin, name, re.IGNORECASE):
            found.add(item.label)
    return frozenset(found)


def _credit_pattern(lang: LanguageModule) -> re.Pattern[str] | None:
    credits = lang.venue_words.alias_credits
    if not credits:
        return None
    parts = []
    for credit in credits:
        if " " in credit:
            parts.append(re.escape(credit).replace(r"\ ", r"\s+"))
        else:
            parts.append(rf"(?:^|[\s:：]){re.escape(credit)}(?:$|[\s:：])")
    return re.compile("|".join(parts), re.IGNORECASE)


def _role_credit(left: str, right: str, pattern: re.Pattern[str] | None) -> bool:
    if pattern is None:
        return False
    return bool(pattern.search(left) or pattern.search(right))


def _hangul_len(name: str) -> int:
    return len(_HANGUL.findall(name))


def _short_hangul_alias(name_a: str, name_b: str) -> bool:
    lengths = sorted((_hangul_len(name_a), _hangul_len(name_b)))
    return lengths[0] <= HANGUL_NICKNAME_MAX and lengths[1] >= HANGUL_OFFICIAL_MIN and lengths[0] >= 2


def _pure_acronym(name: str) -> str:
    """Symbol string when the whole name is an acronym. SeMA is not: its capitals are SMA."""
    symbols = _acronym_symbols(name)
    core = re.sub(r"[^A-Za-z0-9]", "", name)
    if symbols and core.upper() == symbols.upper():
        return symbols
    return ""


def _letter_subsequence(symbols: str, name: str) -> bool:
    """V12b only, and only for a fully Latin expansion written by fewer than three people.

    V12i does not use this. Exact initials are the V12i test.
    """
    haystack = re.sub(r"[^a-z0-9]", "", name.casefold())
    iterator = iter(haystack)
    return all(any(char == candidate for candidate in iterator) for char in symbols.casefold())


def _city_token(token: str, lang: LanguageModule) -> bool:
    found = lang.gazetteer.resolve_fragments([token])
    return bool(found and found[0] and found[0][0])


def _place_token(token: str, lang: LanguageModule) -> bool:
    """True when the gazetteer reads the token as a city or a country."""
    found = lang.gazetteer.resolve_fragments([token])
    return bool(found and found[0])


def _is_extension(name_a: str, name_b: str, lang: LanguageModule) -> bool:
    """True when one display name is a branch, room, or child site of the other."""
    from giye.normalize import venue_names

    left = venue_names.name_part(institution_key(name_a, lang))
    right = venue_names.name_part(institution_key(name_b, lang))
    if left == right:
        return False
    nouns = lang.venue_words.child_nouns
    for child, parent in ((left, right), (right, left)):
        if venue_names.hangul_part_parent(child, lang) == parent:
            return True
        branch, _place = venue_names.hangul_branch_site(child, lang)
        if branch and branch == parent:
            return True
        part = venue_names.latin_part_parent(child, lang)
        if part and venue_names.name_part(institution_key(part, lang)) == parent:
            return True
        acronym, place = venue_names.acronym_place_parent(child, [name_a, name_b], lang, {parent})
        if acronym and acronym == parent and place:
            return True
        if not child.startswith(parent):
            continue
        extra = child[len(parent) :].strip()
        if not extra:
            continue
        if nouns and any(noun in extra for noun in nouns):
            return True
        tokens = extra.split()
        if len(tokens) == 1 and _city_token(tokens[0], lang):
            return True
    return False


def _name_stems(name: str, lang: LanguageModule) -> set[str]:
    """Content stems of a Latin expansion. Accents and a trailing s are folded.

    Generic words and place tokens are not stems: shared ones are how unrelated
    galleries collide on initials.
    """
    stops = {word.casefold() for word in lang.venue_words.stem_stops}
    text = unicodedata.normalize("NFD", name.casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.replace("ue", "u").replace("ae", "a").replace("oe", "o")
    found = set()
    for word in re.findall(r"[a-z]+", text):
        if word in stops or generic_name(word, lang):
            continue
        if word.endswith("s") and len(word) > 3:
            word = word[:-1]
        if _place_token(word, lang):
            continue
        found.add(word)
    return found


def _edit_distance_le1(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    index_left = index_right = edits = 0
    while index_left < len(left) and index_right < len(right):
        if left[index_left] == right[index_right]:
            index_left += 1
            index_right += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(left) == len(right):
            index_left += 1
        index_right += 1
    return edits + (len(left) - index_left) + (len(right) - index_right) <= 1


def _letters(name: str) -> str:
    """Letters only, accents stripped. Spacing and camel case then compare equal."""
    text = unicodedata.normalize("NFD", name.casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return "".join(char for char in text if char.isalpha())


def _same_spelling(left_name: str, right_name: str, left_stems: set[str], right_stems: set[str]) -> bool:
    """One expansion written two ways: stem edit, or the same letters with different spacing."""
    if _spelling_variant(left_stems, right_stems):
        return True
    folded = _letters(left_name)
    return bool(folded) and folded == _letters(right_name)


def _spelling_variant(left: set[str], right: set[str]) -> bool:
    """True when two stem sets are one name (Berkley/Berkeley, Arti/Arte)."""
    if left == right:
        return True
    only_left, only_right = left - right, right - left
    if len(only_left) == 1 and len(only_right) == 1:
        token_left, token_right = next(iter(only_left)), next(iter(only_right))
        if _edit_distance_le1(token_left, token_right):
            return True
    union = left | right
    return bool(union) and len(left & right) / len(union) >= SPELLING_OVERLAP


def _script_buckets(text: str) -> set[str]:
    """Scripts of the letters in ``text``: latin, hangul, or cjk (Han/Kana)."""
    found = set()
    for char in text:
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        if "HANGUL" in name:
            found.add("hangul")
        elif "LATIN" in name:
            found.add("latin")
        elif "CJK" in name or "HIRAGANA" in name or "KATAKANA" in name:
            found.add("cjk")
    return found


def _script_residue(texts: list[str], acronym: str, expansion: str, lang: LanguageModule) -> bool:
    """The acronym already names the expansion, and another string is a second institution.

    After the acronym token and gazetteer places are removed, letters remain in
    a script the expansion does not use. A place gloss leaves nothing. A
    translation that is the entity's only string does not fire.
    """
    exp_fold = expansion.casefold()
    if not any(exp_fold in text.casefold() for text in texts):
        return False
    allowed = _script_buckets(expansion)
    acronym_fold = acronym.casefold()
    for text in texts:
        if exp_fold in text.casefold():
            continue
        for token in re.findall(r"[^\W\d_]+", text, flags=re.UNICODE):
            if token.casefold() == acronym_fold:
                continue
            if lang.gazetteer.resolve_detail(token):
                continue
            if _script_buckets(token) - allowed:
                return True
    return False


def _pair_key(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def _drop_second_expansion(
    edges: dict[tuple[str, str], str],
    entities: dict[str, dict],
    stems: dict[str, set[str]],
    lang: LanguageModule,
) -> None:
    """An acronym that already expands to a Latin name does not take a different one."""
    for left, right in list(edges):
        for acronym, partner in ((left, right), (right, left)):
            symbols = _pure_acronym(entities[acronym]["name"])
            if not symbols or _mostly(entities[partner]["name"]):
                continue
            stored = [
                spelling
                for spelling in (entities[acronym]["name"], *entities[acronym]["aliases"])
                if spelling
                and not _pure_acronym(spelling)
                and not _mostly(spelling)
                and _latin_initials(spelling).upper() == symbols.upper()
            ]
            if not stored:
                continue
            partner_stems = stems[partner]
            if any(
                _same_spelling(entities[partner]["name"], spelling, partner_stems, _name_stems(spelling, lang))
                for spelling in stored
            ):
                continue
            edges.pop(_pair_key(acronym, partner), None)
            break


def _drop_tied_latin_spellings(
    edges: dict[tuple[str, str], str],
    people: dict[tuple[str, str], set[str]],
    entities: dict[str, dict],
    stems: dict[str, set[str]],
) -> None:
    """Drop every Latin partner of an acronym when the top supports tie.

    Partners are grouped by spelling (a stem variant, or the same letters with
    different spacing). A tie is the N-4 case: two names written beside one
    acronym by the same number of people, so the acronym joins neither. A
    strictly stronger spelling does not drop a weaker one. The measurement
    keeps that weaker expansion (InterCommunication Center beside the NTT form).
    """
    by_acronym: dict[str, list[str]] = defaultdict(list)
    for left, right in edges:
        for venue_id, other in ((left, right), (right, left)):
            if _pure_acronym(entities[venue_id]["name"]) and not _mostly(entities[other]["name"]):
                by_acronym[venue_id].append(other)
    for acronym, partners in by_acronym.items():
        groups: list[list[str]] = []
        for partner in dict.fromkeys(partners):
            placed = False
            for group in groups:
                head = group[0]
                if _same_spelling(entities[partner]["name"], entities[head]["name"], stems[partner], stems[head]):
                    group.append(partner)
                    placed = True
                    break
            if not placed:
                groups.append([partner])
        if len(groups) < 2:
            continue

        def support(group: list[str], acronym_id: str = acronym) -> int:
            return max(len(people.get(_pair_key(acronym_id, partner), ())) for partner in group)

        ranked = sorted(groups, key=support, reverse=True)
        if support(ranked[0]) != support(ranked[1]):
            continue
        for partner in partners:
            edges.pop(_pair_key(acronym, partner), None)


def _drop_two_hangul(edges: dict[tuple[str, str], str], entities: dict[str, dict]) -> None:
    """Undo a component that would hold two Hangul names, unless it is a V12h nickname."""
    parent = {venue_id: venue_id for pair in edges for venue_id in pair}

    def find(venue_id: str) -> str:
        while parent[venue_id] != venue_id:
            parent[venue_id] = parent[parent[venue_id]]
            venue_id = parent[venue_id]
        return venue_id

    for left, right in list(edges):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root
    members: dict[str, list[str]] = defaultdict(list)
    for venue_id in list(parent):
        members[find(venue_id)].append(venue_id)
    for ids in members.values():
        hangul = [venue_id for venue_id in ids if _mostly(entities[venue_id]["name"])]
        if len(hangul) < 2:
            continue
        nickname = False
        if len(hangul) == 2:
            key = _pair_key(hangul[0], hangul[1])
            nickname = (
                key in edges
                and "V12h" in edges[key].split("+")
                and _short_hangul_alias(entities[hangul[0]]["name"], entities[hangul[1]]["name"])
            )
        if nickname:
            continue
        for left, right in list(edges):
            if left in ids or right in ids:
                del edges[_pair_key(left, right)]


def _richer(left: str, right: str, entities: dict[str, dict]) -> str:
    """More artists, then more rows, then the smaller venue id."""

    def rank(venue_id: str) -> tuple[int, int, int]:
        row = entities[venue_id]
        return (row["n_artists"], row["n_rows"], -int(venue_id.split("-")[1]))

    return left if rank(left) >= rank(right) else right


def plan_v12(entities: dict[str, dict], rows: list[dict], lang: LanguageModule) -> AliasPlan:
    """Accepted V12 edges, the keeper map, and V12k kind changes.

    ``entities`` are the entities still separate after V9u. ``rows`` are the
    publishable activities, with venue ids already remapped by V4n, V7f and
    V9u. ``venue_country`` is the V1 country: the country fill has not run.
    """
    plan = AliasPlan()
    if len(entities) < 2 or not rows:
        return plan
    credit = _credit_pattern(lang)
    kind_cache: dict[str, str] = {}

    def kind_of(text: str) -> str:
        cached = kind_cache.get(text)
        if cached is None:
            cached = classify_fragment(text, lang).kind
            kind_cache[text] = cached
        return cached

    key_to_ids: dict[str, set[str]] = defaultdict(set)
    for venue_id, entity in entities.items():
        for spelling in [entity["name"], *entity["aliases"]]:
            if spelling:
                key_to_ids[institution_key(spelling, lang)].add(venue_id)

    def resolve(text: str, row_venue: str) -> set[str]:
        found = set(key_to_ids.get(institution_key(text, lang), ()))
        if row_venue in found:
            return {row_venue}
        return found

    people: dict[tuple[str, str], set[str]] = defaultdict(set)
    countries: dict[str, set[str]] = defaultdict(set)
    votes: dict[str, Counter] = defaultdict(Counter)
    acronym_texts: dict[str, list[str]] = defaultdict(list)
    editions: dict[str, list[str]] = defaultdict(list)

    for row in rows:
        venue_id = row.get("venue_id") or ""
        funder_id = row.get("funder_id") or ""
        venue_kind = row.get("venue_kind") or "unknown"
        if venue_id and row.get("venue_country"):
            countries[venue_id].add(row["venue_country"])
        if venue_id:
            votes[venue_id][venue_kind] += 1
            entity = entities.get(venue_id)
            if entity and _pure_acronym(entity["name"]) and row.get("venue_norm"):
                acronym_texts[venue_id].append(row["venue_norm"])
        if funder_id:
            votes[funder_id][venue_kind] += 1
        link = row.get("event_link") or ""
        if link and venue_id:
            editions[link].append(venue_id)
        norm = row.get("venue_norm") or ""
        if not norm or not venue_id:
            continue
        for left, right in row.get("alias_pairs") or ():
            if kind_of(left) not in _KIND_OK or kind_of(right) not in _KIND_OK:
                continue
            if _role_credit(left, right, credit):
                continue
            left_ids = resolve(left, venue_id)
            right_ids = resolve(right, venue_id)
            if len(left_ids) != 1 or len(right_ids) != 1:
                continue
            left_id, right_id = next(iter(left_ids)), next(iter(right_ids))
            if left_id == right_id or left_id not in entities or right_id not in entities:
                continue
            people[_pair_key(left_id, right_id)].add(row.get("ledger_id") or "")

    edition_count: dict[tuple[str, str], int] = defaultdict(int)
    for ids in editions.values():
        unique = list(dict.fromkeys(ids))
        for index, left in enumerate(unique):
            for right in unique[index + 1 :]:
                if left in entities and right in entities:
                    edition_count[_pair_key(left, right)] += 1

    prepared = {venue_id: _name_stems(entity["name"], lang) for venue_id, entity in entities.items()}
    by_initials: dict[str, list[str]] = defaultdict(list)
    for venue_id, entity in entities.items():
        initials = _latin_initials(entity["name"])
        if len(initials) < 2 or generic_name(entity["name"], lang):
            continue
        core = "".join(char for char in entity["name"] if char.isalnum())
        if core.upper() == initials:
            continue
        by_initials[initials].append(venue_id)

    def attested_cities(acr_id: str) -> set[str]:
        found = set()
        own = (entities[acr_id].get("city") or "").casefold()
        if own:
            found.add(own)
        for text in acronym_texts.get(acr_id, ()):
            for city, _country, _region in lang.gazetteer.resolve_detail(text):
                if city:
                    found.add(city.casefold())
        return found

    def place_locked(acr_id: str, exp_id: str) -> bool:
        """Both countries are the same singleton and the expansion's city is on the acronym."""
        left = countries.get(acr_id, set())
        right = countries.get(exp_id, set())
        if not (left and right and left == right and len(left) == 1):
            return False
        exp_city = (entities[exp_id].get("city") or "").casefold()
        if not exp_city:
            return False
        return exp_city in attested_cities(acr_id)

    def pollution(acr_id: str, exp_id: str, symbols: str) -> bool:
        """Another same-initials expansion is named inside the acronym entity's strings."""
        mine = prepared[exp_id]
        others = [
            other_id
            for other_id in by_initials.get(symbols, ())
            if other_id != exp_id and not _spelling_variant(mine, prepared[other_id])
        ]
        if not others:
            return False
        token = symbols.casefold()
        for text in acronym_texts.get(acr_id, ()):
            stems = _name_stems(text, lang) - {token}
            for other_id in others:
                hit = {stem for stem in (stems & prepared[other_id]) - mine if len(stem) >= RIVAL_STEM_MIN}
                if hit:
                    return True
        return False

    def v12i_blocked(acr_id: str, exp_id: str) -> bool:
        """A second witness says this acronym and this expansion are not one institution."""
        left = countries.get(acr_id, set())
        right = countries.get(exp_id, set())
        if left and right and left.isdisjoint(right):
            return True
        exp_name = entities[exp_id]["name"]
        symbols = _latin_initials(exp_name).upper()
        if pollution(acr_id, exp_id, symbols):
            return True
        if generic_name(exp_name, lang) and not place_locked(acr_id, exp_id):
            return True
        mine = prepared[exp_id]
        for other_id in by_initials.get(symbols, ()):
            if other_id == exp_id:
                continue
            other = prepared[other_id]
            if mine & other and not _spelling_variant(mine, other):
                return True
        return _script_residue(
            list(acronym_texts.get(acr_id, ())), entities[acr_id]["name"], exp_name, lang
        )

    def channels(left: str, right: str, support: int, shared_editions: int) -> list[str]:
        name_a = entities[left]["name"]
        name_b = entities[right]["name"]
        if _is_extension(name_a, name_b, lang):
            return []
        both_hangul = _mostly(name_a) and _mostly(name_b)
        org_ok = _org_markers(name_a, lang) == _org_markers(name_b, lang)
        acronym_a = _pure_acronym(name_a)
        acronym_b = _pure_acronym(name_b)
        found: list[str] = []
        if both_hangul and org_ok and _short_hangul_alias(name_a, name_b) and support >= V12H_MIN_PEOPLE:
            found.append("V12h")
        if support >= V12B_MIN_PEOPLE and not both_hangul and (org_ok or acronym_a or acronym_b):
            acronym = acronym_a or acronym_b
            other = name_b if acronym_a else name_a
            keep = True
            if acronym and not _NONLATIN.search(other) and support < V12B_LETTER_BELOW:
                keep = _letter_subsequence(acronym, other)
            if keep:
                found.append("V12b")
        if (
            support >= V12C_MIN_PEOPLE
            and shared_editions >= V12C_MIN_EDITIONS
            and org_ok
            and not both_hangul
        ):
            found.append("V12c")
        if support >= V12I_MIN_PEOPLE and bool(acronym_a) != bool(acronym_b):
            symbols = acronym_a or acronym_b
            other = name_b if acronym_a else name_a
            if symbols and _latin_initials(other).upper() == symbols.upper():
                found.append("V12i")
        return found

    edges: dict[tuple[str, str], str] = {}
    for key, persons in people.items():
        for channel in channels(key[0], key[1], len(persons), edition_count.get(key, 0)):
            if key not in edges:
                edges[key] = channel
            elif channel not in edges[key].split("+"):
                edges[key] = edges[key] + "+" + channel

    # The acronym guard sees every support-1 partner, including one a later
    # screen will drop. A repeated partner keeps the acronym; two single-person
    # partners join nothing.
    by_acronym: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for left, right in edges:
        for venue_id, other in ((left, right), (right, left)):
            if _pure_acronym(entities[venue_id]["name"]):
                by_acronym[venue_id].append((other, len(people.get(_pair_key(venue_id, other), ()))))
    for acronym, partners in by_acronym.items():
        if len(partners) < 2:
            continue
        best = max(count for _other, count in partners)
        if best >= 2:
            reject = {other for other, count in partners if count < 2}
        else:
            reject = {other for other, _count in partners}
        for other in reject:
            edges.pop(_pair_key(acronym, other), None)

    for key in list(edges):
        parts = edges[key].split("+")
        if "V12i" not in parts:
            continue
        left, right = key
        acronym_left = _pure_acronym(entities[left]["name"])
        acronym_right = _pure_acronym(entities[right]["name"])
        if bool(acronym_left) == bool(acronym_right):
            continue
        acr_id, exp_id = (left, right) if acronym_left else (right, left)
        if not v12i_blocked(acr_id, exp_id):
            continue
        kept = [part for part in parts if part != "V12i"]
        if kept:
            edges[key] = "+".join(kept)
        else:
            del edges[key]

    # N-4: one acronym beside two Latin institutions joins neither when the
    # supports tie. Spacing variants (InterCommunication / Inter Communication)
    # are one spelling, so they are not a tie. A weaker different spelling stays.
    # A Hangul partner is not part of that tie (the council and its Korean name).
    # An acronym entity that already carries a Latin expansion (V5d) does not
    # take a second one: ECC beside a culture centre stays off the arts council.
    _drop_second_expansion(edges, entities, prepared, lang)
    _drop_tied_latin_spellings(edges, people, entities, prepared)
    # V5e: a component with two Hangul names stays apart, except the V12h
    # nickname (a 2–4 syllable name beside a name of at least 8).
    _drop_two_hangul(edges, entities)

    parent = {venue_id: venue_id for pair in edges for venue_id in pair}

    def find(venue_id: str) -> str:
        while parent[venue_id] != venue_id:
            parent[venue_id] = parent[parent[venue_id]]
            venue_id = parent[venue_id]
        return venue_id

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return
        keep = _richer(left_root, right_root, entities)
        drop = right_root if keep == left_root else left_root
        parent[drop] = keep

    for left, right in edges:
        union(left, right)

    recorded: set[tuple[str, str, str]] = set()
    for (left, right), channel in edges.items():
        keeper = find(left)
        rule_ids = []
        if any(part in {"V12h", "V12b", "V12c"} for part in channel.split("+")):
            rule_ids.append("V12")
        if "V12i" in channel.split("+"):
            rule_ids.append("V12i")
        for venue_id in (left, right):
            if venue_id == keeper:
                continue
            for rule in rule_ids:
                recorded.add((rule, keeper, venue_id))
    plan.joins = sorted(recorded, key=lambda item: (item[0] != "V12", item[1], item[2], item[0]))
    plan.redirect = {venue_id: find(venue_id) for venue_id in parent if find(venue_id) != venue_id}

    members: dict[str, list[str]] = defaultdict(list)
    for venue_id in parent:
        members[find(venue_id)].append(venue_id)
    for root, ids in members.items():
        if len(ids) < 2:
            continue
        tally: Counter = Counter()
        for venue_id in ids:
            tally.update(votes.get(venue_id, ()))
        current = entities[root]["kind"]
        if not tally:
            adopted = current
        else:
            best = max(tally.values())
            winners = [kind for kind, count in tally.items() if count == best]
            adopted = winners[0] if len(winners) == 1 else current
        if adopted != current:
            plan.kinds[root] = (current, adopted)
    return plan


def _mostly(name: str) -> bool:
    from giye.normalize.venue_names import mostly_hangul

    return mostly_hangul(name)
