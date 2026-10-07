# SPDX-License-Identifier: AGPL-3.0-only
"""Fill an empty institution country, and the same fill on an empty CV venue country.

Four steps, first match, on an empty stored country. A stored country and a
stored V1 country are not replaced. The steps:

G7   A Hangul name starts with a Korean place stem of two or more syllables,
     or with 한국 or 대한민국. Bare 국립 is not a marker. A longer non-KR city,
     a province syllable that is not itself a Korean place, a conjunction, or
     a hall word (순회) blocks the fill. The region is the place-table fold
     only when stored KR halls that share the prefix already carry that fold
     as their unique mode (the hall guard). No halls: keep the fold. A mode
     that is not the fold, or a stem whose places disagree (광주), leaves the
     region blank. Markers have no region.
G8   A spelling equals a frame ``name_ko`` or ``name_en``, or a Hangul string
     of at least five syllables is a prefix of ``name_ko`` or ``name_ko`` is a
     prefix of the spelling. No event pattern, no parenthetical operator, no
     short token inside a frame name. The country does not need placed rows.
     The region is copied only when those rows are already unanimous KR.
G9   A place run sticks to the neighbouring institution. Two countries in the
     run abstain. An empty country is filled only when every placed row is KR
     and there are at least ten. A solo majority is not a fill, and a placed
     majority that is not unanimous is not a fill. Two stored-country
     corrections use the same attachment and are not a bulk replacement of
     every stored-versus-adjacent disagreement: a multi-institution row whose
     stored foreign country has no solo support is set to KR when the
     neighbouring run is unanimous KR and G7 would have filled the name; a
     parenthetical city list that names two countries drops the first city's
     country.
G10  An empty institution written in parentheses copies a unanimous KR host
     when at least five pair-rows agree. A host that G7 or G8 will fill still
     votes. The region is copied only when every vote carries the same
     non-blank tag.
G11  The region written by G7–G10. Recorded only when the region is not blank.

The hall histogram is the stored snapshot, taken before any fill. A later
entity must not be tagged from a region this pass has just written.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

from giye.normalize.gazetteer import hangul_admin_places, place_key
from giye.normalize.language import LanguageModule, packaged_dir

# Placed rows that name one country. Below this the country stays empty.
PLACED_MIN = 10
# Parenthetical host rows. Below this the guest stays empty.
PAREN_MIN = 5
# A directional Hangul prefix is at least this many syllables. Equality is not gated.
PREFIX_SYLLABLES = 5
# Titles and unclassified text are skipped while walking to a neighbour.
# A funder, or any other kind, stops the walk.
SKIP_KINDS = frozenset({"title", "unclassified", "online"})
HANGUL_RE = re.compile(r"[가-힣]")
_QUOTE_RE = re.compile(r"[\"'“”‘’「」『』〈〉《》()（）]")


def _hangul(text: str) -> bool:
    return bool(HANGUL_RE.search(text or ""))


def _syllables(text: str) -> int:
    """Hangul syllable blocks. Jamo are not counted; one block is one syllable."""
    return len(HANGUL_RE.findall(text or ""))


def _squash(text: str) -> str:
    """Frame-name comparison: case-folded, brackets and quotes turned into spaces."""
    folded = unicodedata.normalize("NFC", text or "").casefold()
    return re.sub(r"\s+", " ", _QUOTE_RE.sub(" ", folded)).strip()


def _suffix_pattern(suffixes: tuple[str, ...]) -> re.Pattern[str] | None:
    ordered = tuple(item for item in sorted(suffixes, key=len, reverse=True) if item)
    if not ordered:
        return None
    return re.compile("(?:" + "|".join(re.escape(item) for item in ordered) + ")$")


def place_table(lang: LanguageModule) -> list[tuple[str, str, str]]:
    """Full Hangul place names plus stems, longest first: ``(name, region, kind)``.

    A stem is kept when two or more syllables remain and the name is not already
    a full row. Its region is the shared region of every full name that strips
    to it, or blank when those names disagree.
    """
    path = packaged_dir() / "kr_places.tsv"
    if not path.is_file():
        return []
    full: dict[str, set[str]] = defaultdict(set)
    for name, _admin, region in hangul_admin_places(path):
        full[name].add(region)
    pattern = _suffix_pattern(lang.venue_words.place_suffixes)
    stems: dict[str, set[str]] = defaultdict(set)
    if pattern is not None:
        for name, regions in full.items():
            stem = pattern.sub("", name)
            if len(stem) >= 2 and stem not in full and _hangul(stem):
                stems[stem].update(regions)
    rows: list[tuple[str, str, str]] = []
    for name, regions in full.items():
        region = next(iter(regions)) if len(regions) == 1 else ""
        rows.append((name, region, "full"))
    for stem, regions in stems.items():
        region = next(iter(regions)) if len(regions) == 1 else ""
        rows.append((stem, region, "stem"))
    rows.sort(key=lambda item: (-len(item[0]), item[0]))
    return rows


def _longer_foreign(name: str, prefix_len: int, cities: dict) -> str:
    """A non-KR gazetteer city that extends the Korean prefix, else ``''``."""
    limit = min(len(name), prefix_len + 12)
    for length in range(limit, prefix_len, -1):
        hit = cities.get(place_key(name[:length]))
        if hit and hit[1] != "KR":
            return name[:length]
    return ""


def _blocked(name: str, prefix: str, kind: str, lang: LanguageModule) -> str:
    """Why a G7 hit must abstain, or ``''`` when the fill stands."""
    words = lang.venue_words
    cities = lang.gazetteer.cities
    if kind in {"full", "stem"}:
        foreign = _longer_foreign(name, len(prefix), cities)
        if foreign:
            return "longer-foreign"
        rest_now = name[len(prefix):]
        for syllable in words.province_continuations:
            if syllable and rest_now.startswith(syllable):
                extended = prefix + syllable
                hit = cities.get(place_key(extended))
                if not hit or hit[1] != "KR":
                    return "province-continuation"
                break
    rest = name[len(prefix):]
    marks = tuple(mark for mark in words.conjunction_marks if mark)
    if marks and re.match(r"^\s*(?:" + "|".join(re.escape(mark) for mark in marks) + ")", rest):
        return "conjunction"
    stripped = rest.lstrip()
    for word in words.hall_words:
        if word and (stripped.startswith(word) or rest.startswith(" " + word)):
            return "hall-word"
    return ""


def _g7_hit(
    name: str, table: list[tuple[str, str, str]], markers: tuple[str, ...], lang: LanguageModule
) -> tuple[str, str, str] | None:
    """``(prefix, region, kind)`` for the longest unblocked place, else a marker.

    ``region`` here is the place-table fold, before the hall guard.
    """
    if not _hangul(name):
        return None
    for place, region, kind in table:
        if name.startswith(place):
            if _blocked(name, place, kind, lang):
                return None
            return place, region, kind
    for marker in markers:
        if marker and name.startswith(marker) and not _blocked(name, marker, "marker", lang):
            return marker, "", "marker"
    return None


def _hall_modes(
    snapshot: dict[str, dict[str, str]], table: list[tuple[str, str, str]]
) -> tuple[dict[str, str], dict[str, str]]:
    """Region to write for each full name and each stem, from the stored snapshot.

    No stored hall: keep the fold. A unique mode that is the fold: keep it.
    Any other mode, a tie, or a disagreed fold: blank. An institution that
    starts with a longer full name is not a hall of the shorter prefix.
    """
    full_names = [name for name, _region, kind in table if kind == "full"]
    institutions = [row for row in snapshot.values() if row["kind"] == "institution"]

    def mode_for(prefix: str, fold: str) -> str:
        if not fold:
            return ""
        counts: Counter[str] = Counter()
        for row in institutions:
            if row["country"] != "KR" or not row["name"].startswith(prefix):
                continue
            if any(row["name"].startswith(full) and len(full) > len(prefix) for full in full_names):
                continue
            if row["kr_region"]:
                counts[row["kr_region"]] += 1
        if not counts:
            return fold
        top = counts.most_common()
        if len(top) > 1 and top[0][1] == top[1][1]:
            return ""
        return fold if top[0][0] == fold else ""

    full_mode = {name: mode_for(name, region) for name, region, kind in table if kind == "full"}
    stem_mode = {name: mode_for(name, region) for name, region, kind in table if kind == "stem"}
    return full_mode, stem_mode


def _frame_lists(
    frames: list[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, int]], list[tuple[str, str]]]:
    """``(code, squashed name_ko, syllables)`` and ``(code, squashed name_en)``."""
    korean: list[tuple[str, str, int]] = []
    english: list[tuple[str, str]] = []
    for code, name_ko, name_en in frames:
        ko = _squash(name_ko)
        en = _squash(name_en)
        if ko:
            korean.append((code, ko, _syllables(ko)))
        if en:
            english.append((code, en))
    return korean, english


def _g8_hit(spellings: list[str], korean: list[tuple[str, str, int]], english: list[tuple[str, str]]) -> str:
    """Frame equality, or a directional Hangul prefix of at least five syllables."""
    for raw in spellings:
        spelling = _squash(raw)
        if not spelling:
            continue
        for code, text in english:
            if spelling == text:
                return code
        for code, text, syls in korean:
            if spelling == text:
                return code
            if syls >= PREFIX_SYLLABLES and spelling.startswith(text):
                return code
            if text.startswith(spelling) and _syllables(spelling) >= PREFIX_SYLLABLES and spelling != text:
                return code
    return ""


def _spellings(row: dict[str, str]) -> list[str]:
    aliases = [part for part in (row.get("aliases") or "").split("|") if part]
    return [row.get("name") or "", *aliases]


def _countries(run: list[Any]) -> set[str]:
    return {fragment.place.country for fragment in run if fragment.place and fragment.place.country}


def _first_detail(run: list[Any], country: str) -> tuple[str, str, str]:
    for fragment in run:
        place = fragment.place
        if place and place.country == country:
            return place.city, place.country, place.kr_region
    return "", country, ""


def _neighbouring(indexed: list[tuple[Any, str]], start: int, step: int) -> int | None:
    index = start
    while 0 <= index < len(indexed):
        fragment, venue_id = indexed[index]
        if fragment.kind == "institution" and venue_id:
            return index
        if fragment.kind in SKIP_KINDS:
            index += step
            continue
        return None
    return None


def _place_runs(indexed: list[tuple[Any, str]]):
    """Contiguous place runs, each attached to the preceding institution, else the following one."""
    index = 0
    while index < len(indexed):
        fragment = indexed[index][0]
        if fragment.kind != "place" or not fragment.place or not fragment.place.country:
            index += 1
            continue
        end = index
        run: list[Any] = []
        while end < len(indexed):
            current = indexed[end][0]
            if current.kind != "place" or not current.place or not current.place.country:
                break
            run.append(current)
            end += 1
        host = _neighbouring(indexed, index - 1, -1)
        if host is None:
            host = _neighbouring(indexed, end, +1)
        yield host, run
        index = end


def _index_row(
    row: Any,
    roots: list[tuple[str, str, str, str]],
    annotation: dict[str, str],
    id_of: dict[str, str],
    redirect: dict[str, str],
) -> list[tuple[Any, str]]:
    """Fragments of one row, with the final venue id of each institution or funder."""
    named = iter(roots)
    indexed: list[tuple[Any, str]] = []
    primary = True
    for fragment in row.fragments:
        if fragment.kind not in {"institution", "funder"}:
            indexed.append((fragment, ""))
            continue
        try:
            kind, root, _key, _text = next(named)
        except StopIteration:
            indexed.append((fragment, ""))
            continue
        base = id_of.get(root, "")
        if kind == "institution" and primary:
            venue_id = annotation.get("venue_id") or base
            primary = False
        else:
            venue_id = redirect.get(base, base)
        indexed.append((fragment, venue_id))
    return indexed


def _parenthetical_conflict(norm: str, run: list[Any]) -> bool:
    """True when every place in ``run`` sits inside one parenthesis and the countries disagree.

    A comma-separated city in front of the name is not this shape. The first
    city of a parenthetical list is not the institution's country when the
    list names two countries.
    """
    if len(_countries(run)) < 2:
        return False
    spans = re.findall(r"[（(]([^）)]*)[）)]", norm or "")
    if not spans:
        return False
    inside = " ".join(spans)
    return all(fragment.text and fragment.text in inside for fragment in run)


def _majority_country(votes: Counter[str], denominator: int) -> tuple[str, int]:
    """Mode when it covers at least half of ``denominator``. Ties break on the code."""
    if denominator <= 0 or not votes:
        return "", 0
    country, count = min(votes.items(), key=lambda item: (-item[1], item[0]))
    if count / denominator >= 0.5:
        return country, count
    return "", 0


def _unanimous_region(place_votes: Counter[tuple[str, str, str]]) -> str:
    """One KR region when the non-blank placed rows all say it, and it has the floor.

    A blank region abstains. A second region, or any non-KR country, leaves it blank.
    """
    votes: Counter[str] = Counter()
    non_kr = 0
    for (_city, country, region), count in place_votes.items():
        if country != "KR":
            non_kr += count
            continue
        if region:
            votes[region] += count
    if non_kr or len(votes) != 1:
        return ""
    region, count = next(iter(votes.items()))
    if count < PLACED_MIN:
        return ""
    return region


def _join_rules(existing: str, extra: set[str], order: tuple[str, ...]) -> str:
    found = {part for part in (existing or "").split("|") if part}
    found |= extra
    return "|".join(rule for rule in order if rule in found)


def apply_country_fill(
    parsed: list[Any],
    annotations: dict[str, dict[str, str]],
    venue_rows: list[dict],
    row_roots: dict[str, list[tuple[str, str, str, str]]],
    redirect: dict[str, str],
    entity_by_root: dict[str, dict],
    lang: LanguageModule,
    frames: list[tuple[str, str, str]] | None,
    *,
    key_of: Callable[[str], str],
    pairs_of: Callable[[str], list[tuple[str, str]]],
    rule_order: tuple[str, ...],
) -> dict[str, Any]:
    """Write G7–G11 onto empty institution countries. Return the CV fill map and the audit.

    ``cv_fills`` is only entities whose stored country was empty. A correction
    of a stored country is not copied onto a CV row.
    """
    snapshot = {
        row["venue_id"]: {
            "name": row.get("name") or "",
            "kind": row.get("kind") or "",
            "country": row.get("country") or "",
            "kr_region": row.get("kr_region") or "",
            "city": row.get("city") or "",
            "aliases": row.get("aliases") or "",
        }
        for row in venue_rows
    }
    table = place_table(lang)
    markers = tuple(lang.venue_words.country_markers)
    full_mode, stem_mode = _hall_modes(snapshot, table)
    korean_frames, english_frames = _frame_lists(frames or [])

    g7: dict[str, tuple[str, str]] = {}
    g8: dict[str, str] = {}
    for venue_id, row in snapshot.items():
        if row["kind"] != "institution":
            continue
        hit = _g7_hit(row["name"], table, markers, lang)
        if hit:
            prefix, _fold, kind = hit
            if kind == "full":
                region = full_mode.get(prefix, "")
            elif kind == "stem":
                region = stem_mode.get(prefix, "")
            else:
                region = ""
            g7[venue_id] = (region, kind)
        reason = _g8_hit(_spellings({"name": row["name"], "aliases": row["aliases"]}), korean_frames, english_frames)
        if reason:
            g8[venue_id] = reason

    id_of = {root: entity["venue_id"] for root, entity in entity_by_root.items()}
    adj_country: dict[str, Counter[str]] = defaultdict(Counter)
    adj_place: dict[str, Counter[tuple[str, str, str]]] = defaultdict(Counter)
    adj_conflict: Counter[str] = Counter()
    solo_n: Counter[str] = Counter()
    mention_n: Counter[str] = Counter()
    paren_conflict_ids: set[str] = set()

    for row in parsed:
        annotation = annotations.get(row.activity_id)
        if annotation is None:
            continue
        indexed = _index_row(row, row_roots.get(row.activity_id, []), annotation, id_of, redirect)
        attached: dict[int, list[Any]] = defaultdict(list)
        for host, run in _place_runs(indexed):
            if host is not None:
                attached[host].extend(run)
        inst_at = [index for index, (fragment, venue_id) in enumerate(indexed) if fragment.kind == "institution" and venue_id]
        seen: set[str] = set()
        for at in inst_at:
            venue_id = indexed[at][1]
            if not venue_id or venue_id in seen or venue_id not in snapshot:
                continue
            seen.add(venue_id)
            mention_n[venue_id] += 1
            if len(inst_at) == 1:
                run = [
                    fragment
                    for fragment, _venue in indexed
                    if fragment.kind == "place" and fragment.place and fragment.place.country
                ]
                solo_n[venue_id] += 1
                if _parenthetical_conflict(row.norm, run):
                    paren_conflict_ids.add(venue_id)
            else:
                run = attached.get(at, [])
            codes = _countries(run)
            if len(codes) == 1:
                country = next(iter(codes))
                adj_country[venue_id][country] += 1
                adj_place[venue_id][_first_detail(run, country)] += 1
            elif len(codes) > 1:
                adj_conflict[venue_id] += 1

    def placed_region(venue_id: str) -> str:
        """Region only when the placed-country rule already holds and the region is unanimous."""
        votes = adj_country[venue_id]
        denominator = sum(votes.values()) + adj_conflict[venue_id]
        country, count = _majority_country(votes, denominator)
        if country != "KR" or count != denominator or count < PLACED_MIN:
            return ""
        return _unanimous_region(adj_place[venue_id])

    def placed_kr(venue_id: str) -> bool:
        votes = adj_country[venue_id]
        denominator = sum(votes.values()) + adj_conflict[venue_id]
        country, count = _majority_country(votes, denominator)
        return country == "KR" and count == denominator and count >= PLACED_MIN

    owners: dict[str, set[str]] = defaultdict(set)
    for venue_id, row in snapshot.items():
        for text in _spellings(row):
            key = key_of(text)
            if key:
                owners[key].add(venue_id)
    paren_votes: dict[str, Counter[str]] = defaultdict(Counter)
    paren_region_votes: dict[str, Counter[str]] = defaultdict(Counter)
    paren_region_blank: Counter[str] = Counter()
    for row in parsed:
        if "(" not in (row.norm or "") and "（" not in (row.norm or ""):
            continue
        for outside, inside in pairs_of(row.norm):
            out_ids = owners.get(key_of(outside), set())
            in_ids = owners.get(key_of(inside), set())
            if len(out_ids) != 1 or len(in_ids) != 1:
                continue
            guest_id = next(iter(in_ids))
            host_id = next(iter(out_ids))
            guest = snapshot.get(guest_id)
            host = snapshot.get(host_id)
            if not guest or not host or guest["kind"] != "institution" or guest["country"]:
                continue
            if host["country"]:
                paren_votes[guest_id][host["country"]] += 1
                if host["country"] == "KR" and host["kr_region"]:
                    paren_region_votes[guest_id][host["kr_region"]] += 1
                else:
                    paren_region_blank[guest_id] += 1
            elif host_id in g7 or host_id in g8:
                paren_votes[guest_id]["KR"] += 1
                region = g7[host_id][0] if host_id in g7 else placed_region(host_id)
                if region:
                    paren_region_votes[guest_id][region] += 1
                else:
                    paren_region_blank[guest_id] += 1
    paren_country: dict[str, str] = {}
    paren_region: dict[str, str] = {}
    for venue_id, votes in paren_votes.items():
        country, count = min(votes.items(), key=lambda item: (-item[1], item[0]))
        if len(votes) == 1 and count >= PAREN_MIN and country == "KR":
            paren_country[venue_id] = country
            region_votes = paren_region_votes.get(venue_id, Counter())
            if paren_region_blank[venue_id] == 0 and len(region_votes) == 1:
                paren_region[venue_id] = next(iter(region_votes))

    # Corrections of a stored country. Not applied to a CV row.
    corrections: dict[str, tuple[str, str, str]] = {}
    for venue_id, row in snapshot.items():
        if row["kind"] != "institution" or not row["country"]:
            continue
        votes = adj_country[venue_id]
        denominator = sum(votes.values()) + adj_conflict[venue_id]
        country, count = _majority_country(votes, denominator)
        leaked = (
            venue_id in g7
            and solo_n[venue_id] == 0
            and row["country"] != "KR"
            and country == "KR"
            and count == denominator
            and denominator >= 1
        )
        if leaked:
            corrections[venue_id] = ("KR", placed_region(venue_id) if count >= PLACED_MIN else "", "leak")
            continue
        parenthetical = (
            venue_id in paren_conflict_ids
            and solo_n[venue_id] == mention_n[venue_id]
            and mention_n[venue_id] >= 1
            and adj_conflict[venue_id] == mention_n[venue_id]
            and not votes
        )
        if parenthetical:
            corrections[venue_id] = ("", "", "abstain")

    entity_of = {entity["venue_id"]: entity for entity in entity_by_root.values()}
    cv_fills: dict[str, tuple[str, str, str]] = {}
    counts: Counter[str] = Counter()
    correction_lines: list[str] = []

    def write(venue_id: str, country: str, region: str, rules: set[str], *, city: str | None) -> None:
        for row in venue_rows:
            if row["venue_id"] != venue_id:
                continue
            row["country"] = country
            row["kr_region"] = region
            if city is not None:
                row["city"] = city
            row["rules"] = _join_rules(row.get("rules") or "", rules, rule_order)
            break
        entity = entity_of.get(venue_id)
        if entity is not None:
            entity["country"] = country
            entity["kr_region"] = region
            if city is not None:
                entity["city"] = city

    for venue_id, (country, region, kind) in sorted(corrections.items()):
        rules = {"G9"}
        if region:
            rules.add("G11")
        # The leaked city belonged to the other country. The neighbouring run's
        # country name (한국) has no city of its own, so the city is cleared.
        write(venue_id, country, region, rules, city="")
        counts[f"correct-{kind}"] += 1
        correction_lines.append(
            f"- G9 {kind} · `{snapshot[venue_id]['name']}` {snapshot[venue_id]['country'] or '(blank)'} → {country or '(blank)'}"
        )

    for venue_id, row in snapshot.items():
        if row["kind"] != "institution" or row["country"] or venue_id in corrections:
            continue
        rule = ""
        region = ""
        if venue_id in g7:
            rule, region = "G7", g7[venue_id][0]
        elif venue_id in g8:
            rule, region = "G8", placed_region(venue_id)
        elif placed_kr(venue_id):
            rule, region = "G9", placed_region(venue_id)
        elif venue_id in paren_country:
            rule, region = "G10", paren_region.get(venue_id, "")
        if not rule:
            continue
        rules = {rule}
        if region:
            rules.add("G11")
        write(venue_id, "KR", region, rules, city=None)
        token = "|".join(item for item in ("G7", "G8", "G9", "G10", "G11") if item in rules)
        cv_fills[venue_id] = ("KR", region, token)
        counts[rule] += 1
        if region:
            counts["G11"] += 1

    audit = [
        "## 8. Country and region fill",
        "",
        (
            "Empty institution countries only, except the G9 corrections listed below. "
            "A stored country is not replaced by G7, G8, a solo majority, or a placed majority that is not unanimous KR. "
            "G11 is the region, written only when it is not blank. The hall guard uses the stored snapshot from before this fill."
        ),
        "",
        (
            f"- G7 place prefix: {counts['G7']} institutions"
            f" · G8 frame name: {counts['G8']}"
            f" · G9 neighbour, unanimous KR: {counts['G9']}"
            f" · G10 parenthetical host: {counts['G10']}"
            f" · G11 region: {counts['G11']}"
        ),
        (
            f"- G9 corrections: {counts['correct-leak']} neighbour leaks set to KR, "
            f"{counts['correct-abstain']} parenthetical lists cleared"
        ),
        "",
        *correction_lines,
        "",
    ]
    return {"cv_fills": cv_fills, "audit": audit, "counts": dict(counts)}
