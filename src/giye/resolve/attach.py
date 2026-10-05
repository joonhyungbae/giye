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
    group whose English names agree is A2.

A3. The name is not a bare personal name.
    ``person_like`` asks the language module (``personal_name``); the
    Korean–English module takes two to four Hangul syllables starting with a
    listed surname. A group, a studio, or any other spelling is not that pattern, so
    the first same-key row is reused. A Latin-only name that failed the
    two-token test takes this branch for the same reason.

A4. A same-key row exists and it has no roster membership yet.
    The first roster attaches to it. Only a bare Korean personal name reaches
    this branch; other names have already joined at A3.

A5. The collector's identity key matches a key already stored on a row.
    Checked before the name rules. A miss does not fall through, so two people
    the roster pinned apart stay apart.

A6. The same own website is owned by exactly one existing row.
    Used when the name rules and the identity key did not choose a row. A
    non-social link owned by one row can join a different spelling. A link
    owned by two rows is not used. Two different bare Korean personal names
    are not joined by a shared link.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from giye.field import Field, frame_family
from giye.ledger.schemas import split_pipe
from giye.resolve.evidence import url_key
from giye.resolve.teams import person_like

if TYPE_CHECKING:  # pragma: no cover
    from giye.normalize.language import LanguageModule

_HANGUL = re.compile(r"[가-힣]+")
_LATIN = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class Attachment:
    """Who a roster row joins, which rule fired, and the same-name rows it did not take."""

    ledger_id: str | None
    rule: str | None
    ambiguous: tuple[str, ...]


def hangul_compact(text: str) -> str:
    """Hangul syllables of ``text``, concatenated."""
    return "".join(_HANGUL.findall(text or ""))


def latin_tokens(text: str) -> list[str]:
    """Latin words of length at least 2, lower-cased, in order."""
    return [token.lower() for token in _LATIN.findall(text or "") if len(token) >= 2]


def name_keys(name_ko: str, name_en: str, aliases: str) -> set[str]:
    """Keys a roster name shares with an existing row.

    A Hangul run of at least two syllables, a sorted pair of Latin tokens, a
    single Latin word of at least three characters when the string has no
    Hangul, or the compact spelling when nothing else matched.
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
        elif not hangul_compact(part):
            spelling = "".join(char for char in part.lower() if char.isalnum())
            if len(spelling) >= 3:
                keys.add("lx:" + spelling)
    if not keys:
        for part in (name_ko, name_en):
            spelling = "".join(char for char in (part or "").lower() if char.isalnum())
            if spelling:
                keys.add("cx:" + spelling)
    return keys


def _member_list(artist: dict, language: LanguageModule | None = None) -> bool:
    """A group row whose aliases name two or more people."""
    if person_like(artist.get("name_ko") or "", language):
        return False
    return sum(1 for alias in split_pipe(artist.get("aliases") or "") if person_like(alias, language)) >= 2


def match_artist(
    artists: list[dict],
    families_by_lid: dict[str, set[str]],
    frame_code: str,
    name_ko: str,
    name_en: str,
    aliases: str,
    field: Field,
    language: LanguageModule | None = None,
) -> Attachment:
    """A1–A4. ``rule`` is None when nothing attaches; ``ambiguous`` is the near-miss."""
    primary = name_keys(name_ko, name_en, "")
    candidates = [
        artist
        for artist in artists
        if primary & name_keys(artist.get("name_ko") or "", artist.get("name_en") or "", "")
    ]
    if not candidates:
        alias_hits = [
            artist["ledger_id"]
            for artist in artists
            if primary
            & {key for alias in split_pipe(artist.get("aliases") or "") for key in name_keys(alias, alias, "")}
            and not _member_list(artist, language)
        ]
        if len(set(alias_hits)) == 1:
            candidates = [artist for artist in artists if artist["ledger_id"] == alias_hits[0]]
    if not candidates:
        return Attachment(None, None, ())
    family = frame_family(frame_code, field)
    incoming = set(latin_tokens(name_en))
    for artist in candidates:
        if family in families_by_lid.get(artist["ledger_id"], set()):
            return Attachment(artist["ledger_id"], "A1", ())
    for artist in candidates:
        spellings = [artist.get("name_en") or ""] + split_pipe(artist.get("aliases") or "")
        if len(incoming) >= 2 and any(incoming == set(latin_tokens(text)) for text in spellings):
            return Attachment(artist["ledger_id"], "A2", ())
    if not person_like(name_ko or "", language):
        return Attachment(candidates[0]["ledger_id"], "A3", ())
    for artist in candidates:
        if not families_by_lid.get(artist["ledger_id"]):
            return Attachment(artist["ledger_id"], "A4", ())
    return Attachment(None, None, tuple(artist["ledger_id"] for artist in candidates))


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
) -> Attachment:
    """A5, then A1–A4, then A6. The same order as production ``upsert_people``."""
    if identity:
        marker = f"identity={identity}"
        found = next((artist["ledger_id"] for artist in artists if marker in (artist.get("reviewer_note") or "")), None)
        ambiguous = () if found else tuple(
            artist["ledger_id"] for artist in artists if artist.get("name_ko") and artist.get("name_ko") == name_ko
        )
        return Attachment(found, "A5" if found else None, ambiguous)
    decision = match_artist(artists, families_by_lid, frame_code, name_ko, name_en, aliases, field, language)
    if decision.ledger_id and decision.ledger_id == team_lid:
        others = [artist for artist in artists if artist["ledger_id"] != decision.ledger_id]
        decision = match_artist(others, families_by_lid, frame_code, name_ko, name_en, aliases, field, language)
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
    if person_like(name_ko, language):
        owners = {
            owner
            for owner in owners
            if not (
                person_like(by_id.get(owner, {}).get("name_ko") or "", language)
                and hangul_compact(by_id[owner]["name_ko"]) != hangul_compact(name_ko)
            )
        }
    if len(owners) == 1:
        return Attachment(owners.pop(), "A6", ())
    return decision
