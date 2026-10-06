# SPDX-License-Identifier: AGPL-3.0-only
"""Roster attachment (rules A1–A6).

A roster row joins an existing person only when one of these rules fires.
The rule id is written on the membership row. A row that no rule attaches
becomes a new person (``attach_rule`` ``first``). A same-name candidate that
no rule takes is a ``possible_same_person`` review item. Merging two records
that already exist is E1–E4 and X1, not these rules.

A1. The same name keys are already on a row in this event family.
    A re-run and a later edition of the same programme repeat one participant.
    The family is ``frame_family`` (the field file's lineages). A Korean
    personal name shared with someone in a different programme is not the
    same person.

A2. The name keys match and the English names agree.
    Agreement is at least two Latin tokens, order ignored, against ``name_en``
    or a Latin alias. One Latin token is too common. Checked before A3, so a
    group whose English names agree is A2. A Latin-only personal name, on
    either side, is not joined here: the agreeing Latin string is the whole
    of the evidence. Two Hangul names whose English tokens agree are still A2.

A3. The name is positively a group.
    ``name_class`` reads the incoming row: a team word of the field file, a
    ``members=`` / ``rep=`` note, or person-shaped aliases on a non-personal
    name (``team_like``) make a group, and the first same-key row is reused.
    A name that is not a personal name and not a group (``other``: Han
    characters, a long transliteration) takes A3 only when a same-key row is a
    recorded group. Not fitting the personal shape is not evidence of a
    group, so such a name is otherwise queued.

A4. A same-key row exists and it has no roster membership yet.
    The first roster attaches to it. Only a bare Korean personal name reaches
    this branch.

A5. The collector's identity key matches a key already stored on a row.
    Checked before the name rules. A miss does not fall through, so two people
    the roster pinned apart stay apart.

A6. The same own website is owned by exactly one existing row.
    Used when the name rules and the identity key did not choose a row. A
    non-social link owned by one row can join a different spelling. A link
    owned by two rows is not used. The names must also meet
    (:func:`names_meet`: the same Hangul spelling, a shared name key, or a
    shared romanisation key, X1) unless one side is a group and neither side
    is a personal name (``name_class``). Why: a duo or a studio site is
    listed by people with different names, so a shared link alone joined
    two different people. A group spelling (``Lumen Lab`` / ``루멘 랩``)
    still joins on the link.

Two lines of one roster edition are two people: the ledger does not offer a
line the person an earlier line of the same edition was put on
(``giye.ledger.ledger``), and queues a same-name pair ``same edition``.

The roster row's printed name is never dropped: the ledger keeps a spelling
the person does not already carry as an alias (``giye.ledger.ledger``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from giye.field import Field, frame_family
from giye.ledger.schemas import split_pipe
from giye.resolve.evidence import url_key
from giye.resolve.names import fold_latin, latin_letter, latin_words
from giye.resolve.teams import person_like, team_like

if TYPE_CHECKING:  # pragma: no cover
    from giye.normalize.language import LanguageModule

_HANGUL = re.compile(r"[가-힣]+")


@dataclass(frozen=True)
class Attachment:
    """Who a roster row joins, which rule fired, and the same-name rows it did not take.

    ``miss`` is why a same-name pair was left for review. Empty for the Korean
    homonym path. ``latin name only`` when either side is a Latin-only
    personal name and no rule joined them.
    """

    ledger_id: str | None
    rule: str | None
    ambiguous: tuple[str, ...]
    miss: str = ""


def hangul_compact(text: str) -> str:
    """Hangul syllables of ``text``, concatenated."""
    return "".join(_HANGUL.findall(text or ""))


def latin_tokens(text: str) -> list[str]:
    """Latin-script words of length at least 2, accents folded and case folded, in order.

    A word is a run of Latin-script letters (``giye.resolve.names.latin_words``),
    so ``José García`` is ``jose garcia`` and not ``jos garc``. Folding is the
    design choice of ``fold_latin``: ``García`` meets ``Garcia`` (one name with
    and without its accent) and never meets ``Garcés`` (different letters).
    """
    tokens = (fold_latin(word) for word in latin_words(text))
    return [token for token in tokens if len(token) >= 2]


def _spelling_key(text: str) -> str:
    """Letters and digits of ``text``, Latin accents folded and case folded (the ``lx:``/``cx:`` keys)."""
    return "".join(char for char in fold_latin(text or "") if char.isalnum())


def name_keys(name_ko: str, name_en: str, aliases: str) -> set[str]:
    """Keys a roster name shares with an existing row.

    A Hangul run of at least two syllables, a sorted pair of Latin tokens, a
    single Latin word of at least three characters when the string has fewer
    than two Hangul syllables, or the compact spelling when nothing else
    matched.

    The single-word key is the compact spelling (letters and digits, lower
    case) of that part. A mixed-script name with one Hangul syllable (a Latin
    word plus one syllable) has no Hangul key and no Latin pair, so it takes
    that key too. Before, it fell to the last-resort compact key, which is
    only written when nothing else matched: a record that also had a
    two-token English name never carried it, and the same group came back as
    a second record on re-collection.
    """
    keys: set[str] = set()
    compact = hangul_compact(name_ko)
    if len(compact) >= 2:
        keys.add(f"hk:{compact}")
    for part in split_pipe(aliases) + [name_en, name_ko]:
        hangul = hangul_compact(part)
        if len(hangul) >= 2:
            keys.add(f"hk:{hangul}")
        tokens = latin_tokens(part)
        if len(tokens) >= 2:
            keys.add("lt:" + " ".join(sorted(tokens)))
        elif len(hangul_compact(part)) < 2:
            spelling = _spelling_key(part)
            if len(spelling) >= 3:
                keys.add("lx:" + spelling)
    if not keys:
        for part in (name_ko, name_en):
            spelling = _spelling_key(part)
            if spelling:
                keys.add("cx:" + spelling)
    return keys


def _all_latin(text: str) -> bool:
    """Every letter of ``text`` is a Latin-script letter (accents included), and there is one."""
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and all(latin_letter(char) for char in letters)


def _latin_only_personal(
    name_ko: str,
    name_en: str,
    language: LanguageModule | None,
    words: re.Pattern[str],
    artist: dict | None = None,
) -> bool:
    """A Latin-only personal name: the language module's shape, and not a group.

    Every letter on both fields must be Latin, accents included (``José
    García``). Hangul on either field is not this case (a Hangul row with an
    agreeing English name stays on A2), and neither is any other script. The
    field file's team words, a ``members=`` / ``rep=`` note, or person-shaped
    aliases make a group (``team_like``), which keeps A2 and A3. Group words
    are the archive's list, so they are not compiled into the language module.
    """
    ko = (name_ko or "").strip()
    en = (name_en or "").strip()
    if not _all_latin(f"{ko} {en}"):
        return False
    primary = en or ko
    row = {
        "name_ko": ko or primary,
        "name_en": en,
        "reviewer_note": (artist or {}).get("reviewer_note") or "",
        "aliases": (artist or {}).get("aliases") or "",
    }
    if team_like(row, words=words, language=language):
        return False
    return person_like(primary, language)


def name_class(
    name_ko: str,
    name_en: str,
    aliases: str,
    note: str,
    language: LanguageModule | None,
    words: re.Pattern[str],
) -> str:
    """``group``, ``personal`` or ``other``: how A3 and A6 read a name.

    ``group`` is positive evidence only: ``team_like`` (a team word in the
    name, a ``members=`` / ``rep=`` note, or two person-shaped aliases on a
    non-personal name). ``personal`` is a Latin-only name that is not a group,
    or a name the language module calls personal (``personal_name``: for
    Korean–English, the Hangul surname shape, spaced or not). Everything else
    is ``other``: a name the rules cannot place, such as Han characters or a
    long Hangul transliteration. Why ``other`` is not ``group``: a name outside
    the personal shape is not evidence of a group, and before 2026-10-06 A3
    joined such names across programmes on the name alone (``김 하늘``,
    ``독고영재``, ``알렉스 리``).
    """
    primary = (name_ko or "").strip() or (name_en or "").strip()
    row = {"name_ko": primary, "name_en": name_en or "", "aliases": aliases or "", "reviewer_note": note or ""}
    if team_like(row, words=words, language=language):
        return "group"
    if _latin_only_personal(name_ko, name_en, language, words):
        return "personal"
    if hangul_compact(name_ko) and person_like(name_ko or "", language):
        return "personal"
    return "other"


def _artist_class(artist: dict, language: LanguageModule | None, words: re.Pattern[str]) -> str:
    """:func:`name_class` of a stored row, its aliases and note included."""
    return name_class(
        artist.get("name_ko") or "",
        artist.get("name_en") or "",
        artist.get("aliases") or "",
        artist.get("reviewer_note") or "",
        language,
        words,
    )


def _hangul_spellings(*values: str) -> set[str]:
    """Compact Hangul spellings (two syllables or more) of the given names and pipe-separated alias lists."""
    found: set[str] = set()
    for value in values:
        for part in split_pipe(value or "") or [value or ""]:
            compact = hangul_compact(part)
            if len(compact) >= 2:
                found.add(compact)
    return found


def hangul_contradicts(name_ko: str, name_en: str, aliases: str, artist: dict) -> bool:
    """Both sides carry a Hangul name and no Hangul spelling is shared.

    Why: two different Hangul names can share one romanisation (``윤서정`` and
    ``윤서중`` are both ``Seojung Yoon``), so a Latin key is not a name match
    when the Hangul names say otherwise. Spaces do not count (``김 하늘`` is
    ``김하늘``), and a stored alias is a spelling of the row.
    """
    incoming = _hangul_spellings(name_ko, name_en, aliases)
    stored = _hangul_spellings(artist.get("name_ko") or "", artist.get("name_en") or "", artist.get("aliases") or "")
    return bool(incoming and stored and not incoming & stored)


def _member_list(artist: dict, language: LanguageModule | None = None) -> bool:
    """A group row whose aliases name two or more people."""
    if person_like(artist.get("name_ko") or "", language):
        return False
    return sum(1 for alias in split_pipe(artist.get("aliases") or "") if person_like(alias, language)) >= 2


def _alias_keys(artist: dict) -> set[str]:
    """Name keys of each stored alias. A roster spelling can meet an alias the row does not use as its name."""
    return {key for alias in split_pipe(artist.get("aliases") or "") for key in name_keys(alias, alias, "")}


def _same_key_rows(
    artists: list[dict],
    name_ko: str,
    name_en: str,
    language: LanguageModule | None,
) -> list[dict]:
    """Rows that share a name key, or the single row whose alias is the only hit.

    A group whose aliases name two or more people is not an alias hit: those
    names are members, not the group's own spelling.
    """
    primary = name_keys(name_ko, name_en, "")
    candidates = [
        artist
        for artist in artists
        if primary & name_keys(artist.get("name_ko") or "", artist.get("name_en") or "", "")
    ]
    if candidates or not primary:
        return candidates
    alias_hits = [
        artist["ledger_id"]
        for artist in artists
        if primary & _alias_keys(artist) and not _member_list(artist, language)
    ]
    if len(set(alias_hits)) == 1:
        return [artist for artist in artists if artist["ledger_id"] == alias_hits[0]]
    return []


def _a2_ledger_id(
    candidates: list[dict],
    name_en: str,
    incoming_latin: bool,
    language: LanguageModule | None,
    words: re.Pattern[str],
) -> str | None:
    """A2. First row whose Latin tokens agree, unless either side is a Latin-only personal name.

    Two Latin-only personal names are not the same person because the Latin
    string agrees (author decision 2026-10-05). The same holds when only one
    side is Latin-only: a Hangul record's English spelling agreeing with a
    Latin-only row is still the Latin string alone, so ``Doyun Lee`` on another
    programme is not joined to ``이도윤 / Doyun Lee``. Within a series A1
    joins them; elsewhere the pair is queued. A stored
    Latin-only row keeps that string in ``name_ko`` as well, so ``name_ko`` is
    one of the spellings.
    """
    incoming = set(latin_tokens(name_en))
    if len(incoming) < 2:
        return None
    for artist in candidates:
        spellings = [artist.get("name_en") or ""] + split_pipe(artist.get("aliases") or "")
        spellings.append(artist.get("name_ko") or "")
        if not any(incoming == set(latin_tokens(text)) for text in spellings):
            continue
        other_latin = _latin_only_personal(
            artist.get("name_ko") or "",
            artist.get("name_en") or "",
            language,
            words,
            artist,
        )
        if incoming_latin or other_latin:
            continue
        return artist["ledger_id"]
    return None


def _a4_ledger_id(candidates: list[dict], families_by_lid: dict[str, set[str]]) -> str | None:
    """A4. First same-key row that has no roster membership yet."""
    for artist in candidates:
        if not families_by_lid.get(artist["ledger_id"]):
            return artist["ledger_id"]
    return None


def match_artist(
    artists: list[dict],
    families_by_lid: dict[str, set[str]],
    frame_code: str,
    name_ko: str,
    name_en: str,
    aliases: str,
    field: Field,
    language: LanguageModule | None = None,
    note: str = "",
) -> Attachment:
    """A1–A4 (see docs/RULES.md). ``rule`` is None when nothing attaches; ``ambiguous`` is the near-miss."""
    candidates = _same_key_rows(artists, name_ko, name_en, language)
    if not candidates:
        return Attachment(None, None, ())
    # A Latin key shared by two different Hangul names is not the same name
    # (A1–A4). The pair is queued, not joined.
    contradicted = [artist for artist in candidates if hangul_contradicts(name_ko, name_en, aliases, artist)]
    candidates = [artist for artist in candidates if artist not in contradicted]
    if not candidates:
        return Attachment(None, None, tuple(artist["ledger_id"] for artist in contradicted), "hangul names differ")
    words = field.compiled_team_words()
    incoming_latin = _latin_only_personal(name_ko, name_en, language, words)
    family = frame_family(frame_code, field)
    for artist in candidates:
        if family in families_by_lid.get(artist["ledger_id"], set()):
            return Attachment(artist["ledger_id"], "A1", ())
    agreed = _a2_ledger_id(candidates, name_en, incoming_latin, language, words)
    if agreed:
        return Attachment(agreed, "A2", ())
    # A3 needs a positive group signal: the incoming name is a group, or it is
    # not a personal name and a same-key row is a recorded group.
    incoming_class = name_class(name_ko, name_en, aliases, note, language, words)
    if incoming_class == "group":
        return Attachment(candidates[0]["ledger_id"], "A3", ())
    if incoming_class == "other":
        group_row = next((artist for artist in candidates if _artist_class(artist, language, words) == "group"), None)
        if group_row is not None:
            return Attachment(group_row["ledger_id"], "A3", ())
    # A4 stays the Korean path. A Latin personal name stays unattached rather
    # than joining the first roster-less row on the name alone.
    if hangul_compact(name_ko) and person_like(name_ko or "", language):
        rosterless = _a4_ledger_id(candidates, families_by_lid)
        if rosterless:
            return Attachment(rosterless, "A4", ())
    latin_side = incoming_latin or any(
        _latin_only_personal(artist.get("name_ko") or "", artist.get("name_en") or "", language, words, artist)
        for artist in candidates
    )
    miss = "latin name only" if latin_side else ""
    return Attachment(None, None, tuple(artist["ledger_id"] for artist in candidates), miss)


def attach_row(
    *,
    artists: list[dict],
    families_by_lid: dict[str, set[str]],
    links: list[dict],
    frame_code: str,
    name_ko: str,
    name_en: str,
    aliases: str,
    identity: str,
    websites: list[str],
    field: Field,
    team_lid: str = "",
    language: LanguageModule | None = None,
    note: str = "",
) -> Attachment:
    """A5, then A1–A4, then A6 (see docs/RULES.md).

    Identity is checked first so two people the roster pinned apart stay apart.
    The website (A6) is used only when the name rules did not choose a row.
    """
    if identity:
        marker = f"identity={identity}"
        found = next((artist["ledger_id"] for artist in artists if marker in (artist.get("reviewer_note") or "")), None)
        ambiguous = () if found else tuple(
            artist["ledger_id"] for artist in artists if artist.get("name_ko") and artist.get("name_ko") == name_ko
        )
        return Attachment(found, "A5" if found else None, ambiguous)
    decision = match_artist(artists, families_by_lid, frame_code, name_ko, name_en, aliases, field, language, note)
    if decision.ledger_id and decision.ledger_id == team_lid:
        others = [artist for artist in artists if artist["ledger_id"] != decision.ledger_id]
        decision = match_artist(others, families_by_lid, frame_code, name_ko, name_en, aliases, field, language, note)
    if decision.ledger_id or not websites:
        return decision
    wanted = {url_key(url) for url in websites}
    by_id = {artist["ledger_id"]: artist for artist in artists}
    owners = {
        link["ledger_id"]
        for link in links
        if url_key(link.get("url") or "") in wanted and link.get("link_type") != "social"
    }
    if decision.ambiguous:
        owners &= set(decision.ambiguous)
    if len(owners) != 1:
        return decision
    owner = by_id.get(next(iter(owners)))
    if owner is None:
        return decision
    words = field.compiled_team_words()
    classes = {name_class(name_ko, name_en, aliases, note, language, words), _artist_class(owner, language, words)}
    # The link alone joins only a group spelling: one side is a recorded group
    # and neither side is a personal name. Any other pair must also meet by name.
    link_alone = "group" in classes and "personal" not in classes
    if not link_alone and not names_meet(name_ko, name_en, aliases, owner, language):
        return decision
    return Attachment(owner["ledger_id"], "A6", ())


def names_meet(
    name_ko: str,
    name_en: str,
    aliases: str,
    artist: dict,
    language: LanguageModule | None = None,
) -> bool:
    """True when a roster name and a stored row name one person by spelling (A6 guard).

    Two rows whose Hangul names differ never meet (:func:`hangul_contradicts`).
    Otherwise any of: the same Hangul syllables (spaces removed), a shared
    attachment name key (:func:`name_keys`, stored aliases included), or a shared
    romanisation key of the language module (X1, so ``윤가온`` meets
    ``Gaon Yoon``). This is the overlap the website rules require; it is not
    a reason to join on its own.
    """
    if hangul_contradicts(name_ko, name_en, aliases, artist):
        return False
    incoming = [value for value in (name_ko, name_en, *split_pipe(aliases)) if (value or "").strip()]
    stored = [
        value
        for value in (artist.get("name_ko") or "", artist.get("name_en") or "", *split_pipe(artist.get("aliases") or ""))
        if value.strip()
    ]
    hangul_in = {hangul_compact(value) for value in incoming} - {""}
    hangul_stored = {hangul_compact(value) for value in stored} - {""}
    if hangul_in & hangul_stored:
        return True
    if name_keys(name_ko, name_en, aliases) & (
        name_keys(artist.get("name_ko") or "", artist.get("name_en") or "", "") | _alias_keys(artist)
    ):
        return True
    if language is None:
        from giye.normalize.language import default_language

        language = default_language()
    left = set().union(*(language.name_keys(value) for value in incoming)) if incoming else set()
    right = set().union(*(language.name_keys(value) for value in stored)) if stored else set()
    return bool(left & right)
