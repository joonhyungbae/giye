# SPDX-License-Identifier: AGPL-3.0-only
"""Team rows, the team/person guard (rule T1), and alias members (rule T2).

T1. A row that looks like a team or collective is never merged with a person.
``team_like`` says why a row is a team (a ``members=`` or ``rep=`` note, two or
more person-shaped aliases, or a team word in the name). Exactly one side being
a team blocks the merge. A row recorded as a member of the other row (its
``team=`` or ``팀 구성원:`` note names it, or the other row's ``members=``
lists it) also blocks the merge, whatever the team's name looks like. Two team rows may still merge with each other when E1
holds. X1 is stricter: either side being a team drops the pair, because that
loop is about personal names.

Whether a name is a bare personal name is the language module's
``personal_name`` (the Korean–English module: a Hangul name starting with a
listed surname, spaced or not, or a Latin-only name of two to six tokens). Callers with a config pass that module;
without one the default Korean–English module is used.

A team line also names its members. Each member is on that roster edition in
their own right. Expanding them adds those rows and does not merge anyone into
the team. Re-running does not add the same member twice.

T2. A group record that stores its members only as aliases (two or more
distinct personal names, no ``members=`` list) names them too; see
``alias_members``.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from giye.field import frame_family
from giye.ledger.ids import activity_id_for, activity_id_key, allocate_gy_id
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import ACTIVITIES_FIELDS, ARTISTS_FIELDS, MEMBERSHIP_FIELDS, REVIEW_FIELDS, empty_row

if TYPE_CHECKING:  # pragma: no cover
    from giye.normalize.language import LanguageModule


def _default_team_words() -> re.Pattern[str]:
    """T1 words from the shipped field file. A configured archive passes its own."""
    from giye.field import shipped_field

    return shipped_field().compiled_team_words()

_HANGUL = re.compile(r"[가-힣]")


def person_like(name_ko: str, language: LanguageModule | None = None) -> bool:
    """A bare personal name — the kind that collides across people — by the language module."""
    if language is None:
        # Imported here: the language module imports giye.resolve.names, whose package imports this file.
        from giye.normalize.language import default_language

        language = default_language()
    return language.personal_name(name_ko or "")


def team_like(
    artist: Mapping[str, str],
    *,
    words: re.Pattern[str] | None = None,
    language: LanguageModule | None = None,
) -> str:
    """Why this row is a team or group, or "" when it is a person (T1).

    ``team=`` is also written on a person (the team they belong to), so it does
    not mark a team row. A team row carries ``members=`` or ``rep=``, or a
    non-person name with two or more person-shaped aliases, or a team word.
    ``words`` is the archive's field-file pattern and ``language`` its language
    module; every pipeline caller passes both. Without them the shipped Korean
    media-art list and the Korean–English module are used.
    """
    note = artist.get("reviewer_note") or ""
    for mark in ("members=", "rep="):
        if mark in note:
            return mark.rstrip("=")
    name = artist.get("name_ko") or ""
    pattern = words if words is not None else _default_team_words()
    # A team word inside the primary name itself wins over the personal-name
    # shape: "태별그룹" is four Hangul syllables starting with a listed surname,
    # yet the word 그룹 says it is a group. The English name is only read when
    # the primary name is not personal, because a person's English name may
    # mention a studio or lab they run.
    if pattern.search(name):
        return "team_name"
    members = [alias for alias in _split_pipe(artist.get("aliases") or "") if person_like(alias, language)]
    personal = person_like(name, language)
    if not personal and len(members) >= 2:
        return "aliases"
    if not personal and pattern.search(f"{name} {artist.get('name_en') or ''}"):
        return "team_name"
    return ""


def team_person_mismatch(
    row_a: Mapping[str, str],
    row_b: Mapping[str, str],
    *,
    words: re.Pattern[str] | None = None,
    language: LanguageModule | None = None,
) -> bool:
    """True when one row is a team and the other is a person (T1).

    Two team rows are not a mismatch: E1 may still merge them with each other.
    A row that records the other as its team, or a team row that lists the
    other as a member, is also a mismatch (``member_of_team``). That holds
    even when the team's own name has the shape of a personal name, which
    ``team_like`` cannot see.
    """
    if member_of_team(row_a, row_b) or member_of_team(row_b, row_a):
        return True
    return bool(team_like(row_a, words=words, language=language)) != bool(
        team_like(row_b, words=words, language=language)
    )


def _name_key(value: str) -> str:
    """Case-folded name with spaces and punctuation removed, for comparing a recorded team name."""
    return "".join(ch for ch in (value or "").casefold() if ch.isalnum())


def member_of_team(member: Mapping[str, str], team: Mapping[str, str]) -> bool:
    """True when ``member`` is recorded as belonging to ``team`` (T1).

    A team record and one of its members are two records, whatever the team's
    name looks like. A Latin team name can have the shape of a personal name,
    so ``team_like`` reads it as a person and E1 (a shared website and an
    overlapping alias) would join the team to its member. The ledger already
    says who belongs to which team, in three places:

    - ``team=<name>`` on the member: the team the collector recorded.
    - ``팀 구성원: <name> (<ledger id>)`` on the member: written by
      ``expand_teams`` (``member_rows``).
    - ``members=`` on the team: the team's own member list.

    A name is compared on the other record's primary names (``name_ko``,
    ``name_en``), not its aliases: an alias may be another spelling of a
    member. The comparison ignores case, spaces, and punctuation.
    """
    team_names = {_name_key(team.get("name_ko") or ""), _name_key(team.get("name_en") or "")} - {""}
    member_names = {_name_key(member.get("name_ko") or ""), _name_key(member.get("name_en") or "")} - {""}
    note = member.get("reviewer_note") or ""
    for match in re.finditer(r"(?:^|;)\s*team=([^;]*)", note):
        value = match.group(1).strip()
        recorded = {_name_key(value), *(_name_key(part) for part in re.split(r"\s*[|,]\s*", value))} - {""}
        if recorded & team_names:
            return True
    for match in re.finditer(r"팀 구성원:\s*(.*?)\s*\(([^()]*)\)", note):
        if match.group(2).strip() == (team.get("ledger_id") or "") or _name_key(match.group(1)) in team_names:
            return True
    if re.search(r"(?:^|;\s*)members=", team.get("reviewer_note") or ""):
        for listed in team_members(team):
            if {_name_key(listed["name_ko"]), _name_key(listed["name_en"])} & member_names:
                return True
    return False


def team_members(person: Mapping[str, object]) -> list[dict[str, str]]:
    """People a team row names, as ``{name_ko, name_en}``.

    From an explicit ``members`` list, else from the ``members=`` part of
    ``reviewer_note`` (``|`` or ``,`` separated).
    """
    raw = person.get("members")
    if raw is None:
        match = re.search(r"(?:^|;\s*)members=([^;]*)", str(person.get("reviewer_note") or ""))
        raw = [part for part in re.split(r"\s*[|,]\s*", match.group(1)) if part.strip()] if match else []
    found: list[dict[str, str]] = []
    for item in raw:  # type: ignore[union-attr]
        if isinstance(item, dict):
            ko = str(item.get("name_ko") or "").strip()
            en = str(item.get("name_en") or "").strip()
        else:
            text = " ".join(str(item).split())
            ko, en = (text, "") if _HANGUL.search(text) else ("", text)
        if ko or en:
            found.append({"name_ko": ko, "name_en": en})
    return found


def parse_entry(raw: str, language: LanguageModule | None = None) -> dict[str, object]:
    """One roster credit → name fields.

    A single personal name in parentheses is that person. Several names, or a
    credit joined by ``&``, are a group and the names are its members.
    """
    text = " ".join((raw or "").split())
    match = re.match(r"^(.*?)\s*[\(（]([^\)）]+)[\)）]\s*$", text)
    name, aliases, kind = text, [], "individual"
    if match:
        outer, inner = match.group(1).strip(), match.group(2).strip()
        members = [part.strip() for part in re.split(r"[,、]", inner) if part.strip()]
        if len(members) == 1 and person_like(members[0], language):
            name, aliases = members[0], [outer]
        elif len(members) == 1 and person_like(outer, language):
            name, aliases = outer, members
        elif len(members) == 1 and re.fullmatch(r"[가-힣]{2,4}", members[0]):
            name, aliases = members[0], [outer]
        else:
            name, aliases, kind = outer, members, "group"
    elif re.search(r"\s[&+x×]\s|&", text):
        kind = "group"
    hangul = bool(_HANGUL.search(name))
    return {
        "name_ko": name if hangul else "",
        "name_en": "" if hangul else name,
        "aliases": aliases,
        "kind": kind,
        "raw": raw,
    }


def group_members(entry: Mapping[str, object]) -> list[str]:
    """A group credit's members: the parenthetical names, or the parts of an ``A & B`` credit."""
    if entry.get("kind") != "group":
        return []
    aliases = entry.get("aliases") or []
    if aliases:
        return [str(item) for item in aliases]  # type: ignore[union-attr]
    parts = [
        part.strip()
        for part in re.split(r"\s*(?:&|\sx\s|\sX\s|·|\+)\s*", str(entry.get("raw") or ""))
        if part.strip()
    ]
    return parts if len(parts) >= 2 else []


def members_of(
    artist: Mapping[str, object],
    language: LanguageModule | None = None,
    *,
    words: re.Pattern[str] | None = None,
) -> list[dict[str, str]]:
    """Members to give their own rows. A person who was already split out of a duo is not a team.

    In order: the ``members=`` list, the parts of a ``; group;`` raw credit,
    then the personal-name aliases of a group record (rule T2,
    ``alias_members``). ``words`` is the field file's team-word pattern.
    """
    found = team_members(artist)
    if found:
        return found
    note = str(artist.get("reviewer_note") or "")
    match = re.search(r"(?:^|;\s*)raw=([^;]*)", note)
    if "; group;" in note and match:
        names = group_members(parse_entry(match.group(1).strip(), language))
        # A duo already split into people: the person row is not the team.
        if {artist.get("name_ko"), artist.get("name_en")} & set(names):
            return []
        return [{"name_ko": name, "name_en": ""} if _HANGUL.search(name) else {"name_ko": "", "name_en": name} for name in names]
    return alias_members(artist, language, words=words)


def alias_members(
    artist: Mapping[str, object],
    language: LanguageModule | None = None,
    *,
    words: re.Pattern[str] | None = None,
) -> list[dict[str, str]]:
    """Members read from a group record's aliases (rule T2), or [] when the rule does not hold.

    Author's credit rule (2026-10-06): participation as a team is decomposed
    and returned to the individuals, so they share the credit. Collectors
    often stored the members of a team only as its aliases, with no
    ``members=`` list, so those people never received the team's credit.

    The rule holds when all of these are true:

    - the record is positively a group (``team_like``: a team word, a
      ``members=``/``rep=`` note, or a non-personal name with personal-name
      aliases);
    - neither of its own names (``name_ko``, ``name_en``) is a personal name.
      A person's record keeps other spellings of their own name as aliases,
      and those are not members;
    - its aliases hold two or more distinct personal names (the language
      module's ``personal_name``), leaving out an alias equal to one of the
      record's own names and an alias with a team word (another name of the
      team, such as its English name). Aliases that share a name key (``name_keys``: 김하늘,
      김 하늘 and Haneul Kim) are one person, written as one member with the
      first Hangul and the first Latin spelling. One person is not a team.
    """
    if language is None:
        from giye.normalize.language import default_language

        language = default_language()
    own = [str(artist.get(key) or "").strip() for key in ("name_ko", "name_en")]
    if any(name and language.personal_name(name) for name in own):
        return []
    if not team_like(artist, words=words, language=language):  # type: ignore[arg-type]
        return []
    own_keys = {_name_key(name) for name in own} - {""}
    pattern = words if words is not None else _default_team_words()
    groups: list[tuple[set[str], list[str]]] = []
    for alias in _split_pipe(str(artist.get("aliases") or "")):
        alias = " ".join(alias.split())
        # An alias with a team word is another name of the team (Noeul Studio), not a member.
        if _name_key(alias) in own_keys or not language.personal_name(alias) or pattern.search(alias):
            continue
        keys = set(language.name_keys(alias)) | {_name_key(alias)}
        hits = [group for group in groups if group[0] & keys]
        if hits:
            # Fold every group this alias touches into the first one.
            first = hits[0]
            for other in hits[1:]:
                first[0].update(other[0])
                first[1].extend(other[1])
                groups.remove(other)
            first[0].update(keys)
            first[1].append(alias)
        else:
            groups.append((keys, [alias]))
    if len(groups) < 2:
        return []
    found = []
    for _, spellings in groups:
        ko = next((name for name in spellings if _HANGUL.search(name)), "")
        en = next((name for name in spellings if not _HANGUL.search(name)), "")
        found.append({"name_ko": ko, "name_en": en})
    return found


def member_rows(
    team: Mapping[str, object],
    team_lid: str,
    *,
    source_url: str,
    source_type: str,
    collected: str,
    team_prefix: str = "팀:",
) -> list[dict]:
    """Rows for a team's members: the team's activities credited as ``<prefix> <team>``.

    ``team_prefix`` is the field file's E4 marker (see docs/RULES.md). The default
    ``팀:`` is that marker's usual spelling, so a caller without a field file
    still writes a credit E4 can read.
    """
    name = str(team.get("name_ko") or "").strip() or str(team.get("name_en") or "").strip()
    # The first note segment is the collector's label. ``members=`` / ``rep=`` mark
    # the team itself (T1); copying them would make the member a team and the
    # next run would expand that member again.
    head = str(team.get("reviewer_note") or "").split(";")[0].strip()
    if head.startswith(("members=", "rep=")):
        head = ""
    rows = []
    for member in team_members(team):
        note = "; ".join(part for part in [head, f"팀 구성원: {name} ({team_lid})"] if part)
        activities = []
        for activity in team.get("activities") or []:  # type: ignore[union-attr]
            activities.append({**activity, "role": f"{team_prefix} {name}"})  # type: ignore[arg-type]
        rows.append(
            {
                **member,
                "source_url": source_url,
                "source_type": source_type,
                "collected_at": collected,
                "reviewer_note": note,
                "team_lid": team_lid,
                "activities": activities,
            }
        )
    return rows


# Review-queue note on a member who got a new record next to a same-key record.
MEMBER_NAME_ONLY = "team member, name only"


def _index_families(membership: list[dict], field: object) -> dict[str, set[str]]:
    """Frame family of each roster row, so A1 can see who already belongs to this programme."""
    families: dict[str, set[str]] = {}
    for row in membership:
        families.setdefault(row["ledger_id"], set()).add(frame_family(row.get("frame_code", ""), field))
    return families


def _shaped_team(artist: Mapping[str, object], members: list[dict[str, str]], team_acts: list[dict]) -> dict:
    """Names, collector note, and this frame's activities, the fields ``member_rows`` reads."""
    return {
        "name_ko": artist.get("name_ko") or "",
        "name_en": artist.get("name_en") or "",
        "reviewer_note": artist.get("reviewer_note") or "",
        "members": members,
        "activities": [
            {
                key: row.get(key) or ""
                for key in ("title", "venue", "year", "activity_type", "source_url", "source_type", "publishable")
            }
            for row in team_acts
        ],
    }


def _ensure_frame_membership(
    membership: list[dict],
    ledger_id: str,
    frame: str,
    source: str,
    collected: str,
    rule: str,
) -> bool:
    """Add the member to this edition when they are not already on it. Returns whether a row was added.

    ``rule`` is written as ``attach_rule``: ``team:<team ledger id>``, so the
    membership says it came from expanding that team's credit, as every other
    membership names the rule that wrote it. A membership the member already
    has on this edition is left as it is (re-collection does not rewrite).
    """
    if any(item["ledger_id"] == ledger_id and item["frame_code"] == frame for item in membership):
        return False
    membership.append(
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id=ledger_id,
            frame_code=frame,
            source_url=source,
            collected_at=collected,
            attach_rule=rule,
        )
    )
    return True


def _copy_member_activities(
    activities: list[dict],
    ledger_id: str,
    frame: str,
    member_activities: list,
    source: str,
    collected: str,
    *,
    dry_run: bool,
) -> bool:
    """Copy this frame's team activities onto the member. A dry run writes none of them."""
    changed = False
    for activity in member_activities:
        if _has_activity(activities, ledger_id, frame, activity):
            continue
        if dry_run:
            continue
        activities.append(_member_activity(activities, ledger_id, frame, activity, source, collected))
        changed = True
    return changed


def _create_member(
    artists: list[dict],
    by_id: dict[str, dict],
    taken: set[str],
    issued: list[str],
    row: Mapping[str, str],
    source: str,
    collected: str,
    stamp: str,
    id_prefix: str,
) -> dict:
    """A new roster row for a member no existing person matched under A1–A4."""
    lid = _new_id(taken)
    gy = allocate_gy_id(issued, prefix=id_prefix)
    issued.append(gy)
    ko = (row.get("name_ko") or "").strip() or (row.get("name_en") or "").strip() or "unknown"
    existing = empty_row(
        ARTISTS_FIELDS,
        ledger_id=lid,
        gy_id=gy,
        name_ko=ko,
        name_en=(row.get("name_en") or "").strip(),
        cv_link_ok="no",
        frame_status="IN_FRAME",
        verification="UNVERIFIED",
        status="STAGED",
        source_url=source,
        source_type=row.get("source_type") or "PUBLIC_RECORD",
        collected_at=collected,
        reviewer_note=row.get("reviewer_note") or "",
        updated_at=stamp,
    )
    artists.append(existing)
    by_id[lid] = existing
    return existing


def _place_member(
    artists: list[dict],
    by_id: dict[str, dict],
    families: dict[str, set[str]],
    activities: list[dict],
    membership: list[dict],
    taken: set[str],
    issued: list[str],
    created: list[str],
    frame: str,
    row: Mapping[str, str],
    team_lid: str,
    field: object,
    language: LanguageModule | None,
    source: str,
    collected: str,
    stamp: str,
    id_prefix: str,
    review: list[dict],
    *,
    dry_run: bool,
) -> bool:
    """Attach or create one member on this edition. Returns whether the tables changed.

    A dry run that would create a person appends the name to ``created`` and
    stops, so the roster is not written. An existing person still receives the
    credit note and the membership row in memory; the caller skips the write.

    A member whose name keys hit records that A1–A4 do not take (a Latin-only
    personal name, or a common Korean name on another programme) gets a new
    record, as a roster row does, and the pair is queued as
    ``possible_same_person`` (``MEMBER_NAME_ONLY``). The member is never
    dropped and never joined on the name alone. A re-run attaches the member
    to that new record under A1, because it is now on this programme.
    """
    existing, near = _attach_member(artists, by_id, families, frame, row, team_lid, field, language)
    changed = False
    if existing is None:
        if dry_run:
            created.append(row.get("name_ko") or row.get("name_en") or "")
            return False
        existing = _create_member(artists, by_id, taken, issued, row, source, collected, stamp, id_prefix)
        created.append(existing["ledger_id"])
        changed = True
        if near:
            name = row.get("name_ko") or row.get("name_en") or ""
            detail = f"{name} ({frame}) shares a name with {', '.join(near)} ({MEMBER_NAME_ONLY})"
            review.append(
                empty_row(
                    REVIEW_FIELDS,
                    queue_id=str(uuid.uuid4()),
                    ledger_id=existing["ledger_id"],
                    reason="possible_same_person",
                    detail=detail,
                    status="open",
                    created_at=stamp,
                )
            )
    # The credit note goes only on a record this run created (_create_member
    # writes it there). An existing person's note is curated text and a re-run
    # must leave it as it was (docs/FIELD.md, Re-collection).
    if _ensure_frame_membership(membership, existing["ledger_id"], frame, source, collected, f"team:{team_lid}"):
        changed = True
    families.setdefault(existing["ledger_id"], set()).add(frame_family(frame, field))
    member_activities = row.get("activities") or []
    if _copy_member_activities(
        activities, existing["ledger_id"], frame, member_activities, source, collected, dry_run=dry_run
    ):
        changed = True
    return changed


def expand_teams(ledger: Ledger, *, dry_run: bool = False, frames: Iterable[str] | None = None) -> list[str]:
    """Give every named team member their own roster row. Returns new ledger ids.

    ``frames`` limits the work to team memberships whose ``frame_code`` is one
    of those codes (a collector passes the edition codes it just wrote), so
    no member note, membership or activity is written for any other frame.
    ``None`` expands every team in the ledger (``giye resolve``).

    The team row stays. Members are not merged with the team (T1). A member
    joins an existing person under the roster attachment rules (A1–A4,
    ``match_artist``), the same rules roster ingest uses. The team row itself
    is never that member. A bare personal name those rules do not take gets
    a new record and a ``possible_same_person`` item, not a guessed join. A
    second run adds nothing.
    """
    artists = ledger.read("artists")
    activities = ledger.read("activities")
    membership = ledger.read("frame_membership")
    review = ledger.read("review_queue") if ledger.path("review_queue").exists() else []
    queued_before = len(review)
    created: list[str] = []
    changed = False
    wanted = None if frames is None else set(frames)
    issued = [row.get("gy_id", "") for row in artists]
    issued += [row.get("gy_id", "") for row in ledger.read("gy_retired")]
    taken = {row["ledger_id"] for row in artists}
    by_id = {row["ledger_id"]: row for row in artists}
    field = ledger.config.field_config
    families = _index_families(membership, field)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    from giye.normalize.language import language_for

    language = language_for(ledger.config)
    team_prefix = ledger.config.field_config.team_prefix or "팀:"
    id_prefix = ledger.config.id_prefix or "GY"
    team_words = field.compiled_team_words()
    for artist in list(artists):
        members = members_of(artist, language, words=team_words)
        if not members:
            continue
        team_lid = artist["ledger_id"]
        for mem in [
            row
            for row in membership
            if row["ledger_id"] == team_lid and (wanted is None or row.get("frame_code") in wanted)
        ]:
            frame = mem["frame_code"]
            team_acts = [row for row in activities if row["ledger_id"] == team_lid and row.get("origin") == frame]
            source = mem.get("source_url") or artist.get("source_url") or ""
            collected = (mem.get("collected_at") or artist.get("collected_at") or "")[:10]
            for row in member_rows(
                _shaped_team(artist, members, team_acts),
                team_lid,
                source_url=source,
                source_type=artist.get("source_type") or "PUBLIC_RECORD",
                collected=collected,
                team_prefix=team_prefix,
            ):
                if _place_member(
                    artists,
                    by_id,
                    families,
                    activities,
                    membership,
                    taken,
                    issued,
                    created,
                    frame,
                    row,
                    team_lid,
                    field,
                    language,
                    source,
                    collected,
                    stamp,
                    id_prefix,
                    review,
                    dry_run=dry_run,
                ):
                    changed = True
    if changed and not dry_run:
        ledger.write("artists", artists, task="expand-teams")
        ledger.write("activities", activities, task="expand-teams")
        ledger.write("frame_membership", membership, task="expand-teams")
        if len(review) > queued_before:
            ledger.write("review_queue", review, task="expand-teams")
    return created


def _split_pipe(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def _attach_member(
    artists: list[dict],
    by_id: dict[str, dict],
    families: dict[str, set[str]],
    frame: str,
    member: Mapping[str, str],
    team_lid: str,
    field: object,
    language: LanguageModule | None,
) -> tuple[dict | None, tuple[str, ...]]:
    """The person A1–A4 attach this member to, and the same-key records no rule took.

    ``(None, ())`` means no candidate: the caller creates a row. ``(None,
    ids)`` means the name keys hit records the rules do not take (a bare
    personal name on another programme): the caller creates a row and queues
    the pair. The team row is never one of ``ids``.
    Imported here: ``attach`` imports this module.
    """
    from giye.field import Field
    from giye.resolve.attach import match_artist

    if not isinstance(field, Field):
        raise TypeError("expand_teams needs the archive field file")
    name_ko = member.get("name_ko") or ""
    name_en = member.get("name_en") or ""

    def decide(pool: list[dict]):
        """A1–A4 against this pool. The team row is excluded on the second call."""
        return match_artist(pool, families, frame, name_ko, name_en, "", field, language)

    decision = decide(artists)
    if decision.ledger_id == team_lid:
        decision = decide([row for row in artists if row["ledger_id"] != team_lid])
    if decision.ledger_id:
        return by_id.get(decision.ledger_id), ()
    return None, tuple(lid for lid in decision.ambiguous if lid != team_lid)


def _has_activity(activities: list[dict], ledger_id: str, frame: str, activity: Mapping[str, str]) -> bool:
    role = activity.get("role") or ""
    for row in activities:
        if row.get("ledger_id") != ledger_id or row.get("origin") != frame:
            continue
        if (
            row.get("title") == (activity.get("title") or "")
            and str(row.get("year") or "") == str(activity.get("year") or "")
            and (row.get("role") or "") == role
        ):
            return True
    return False


def _member_activity(
    existing_rows: list[dict],
    ledger_id: str,
    frame: str,
    activity: Mapping[str, str],
    source: str,
    collected: str,
) -> dict[str, str]:
    title = activity.get("title") or frame
    year = str(activity.get("year") or "")
    venue = activity.get("venue") or ""
    activity_type = activity.get("activity_type") or "other"
    url = activity.get("source_url") or source
    key = activity_id_key(
        ledger_id=ledger_id,
        source=url,
        title=title,
        year=year,
        activity_type=activity_type,
        venue=venue,
        origin=frame,
    )
    taken = {row.get("activity_id") for row in existing_rows}
    ordinal = 0
    activity_id = activity_id_for(key, ordinal)
    while activity_id in taken:
        ordinal += 1
        activity_id = activity_id_for(key, ordinal)
    return empty_row(
        ACTIVITIES_FIELDS,
        activity_id=activity_id,
        ledger_id=ledger_id,
        title=title,
        venue=venue,
        year=year,
        activity_type=activity_type,
        role=activity.get("role") or "",
        source_url=url,
        source_type=activity.get("source_type") or "PUBLIC_RECORD",
        collected_at=collected,
        publishable=activity.get("publishable") or ("yes" if str(url).startswith("http") else "no"),
        origin=frame,
    )


def _new_id(taken: set[str]) -> str:
    for _ in range(8):
        lid = f"LED-{uuid.uuid4().hex[:10]}"
        if lid not in taken:
            taken.add(lid)
            return lid
    raise RuntimeError("could not allocate a ledger id")
