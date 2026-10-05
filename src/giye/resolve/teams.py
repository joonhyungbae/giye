# SPDX-License-Identifier: AGPL-3.0-only
"""Team rows and the team/person guard (rule T1).

T1. A row that looks like a team or collective is never merged with a person.
``team_like`` says why a row is a team (a ``members=`` or ``rep=`` note, two or
more person-shaped aliases, or a team word in the name). Exactly one side being
a team blocks the merge. Two team rows may still merge with each other when E1
holds. X1 is stricter: either side being a team drops the pair, because that
loop is about personal names.

Whether a name is a bare personal name is the language module's
``personal_name`` (the Korean–English module: two to four Hangul syllables
starting with a listed surname). Callers with a config pass that module;
without one the default Korean–English module is used.

A team line also names its members. Each member is on that roster edition in
their own right. Expanding them adds those rows and does not merge anyone into
the team. Re-running does not add the same member twice.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from giye.ledger.ids import activity_id_for, activity_id_key, allocate_gy_id
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import ACTIVITIES_FIELDS, ARTISTS_FIELDS, MEMBERSHIP_FIELDS, empty_row

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
    """
    return bool(team_like(row_a, words=words, language=language)) != bool(
        team_like(row_b, words=words, language=language)
    )


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


def members_of(artist: Mapping[str, object], language: LanguageModule | None = None) -> list[dict[str, str]]:
    """Members to give their own rows. A person who was already split out of a duo is not a team."""
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
    return []


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

    ``team_prefix`` is the field file's E4 marker. The default is the production
    spelling so a caller without a field file still writes a credit E4 can read.
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


def expand_teams(ledger: Ledger, *, dry_run: bool = False) -> list[str]:
    """Give every named team member their own roster row. Returns new ledger ids.

    The team row stays. Members are not merged with the team (T1). A member who
    already has a row under that Hangul name is reused. A second run adds nothing.
    """
    artists = ledger.read("artists")
    activities = ledger.read("activities")
    membership = ledger.read("frame_membership")
    created: list[str] = []
    changed = False
    issued = [row.get("gy_id", "") for row in artists]
    issued += [row.get("gy_id", "") for row in ledger.read("gy_retired")]
    taken = {row["ledger_id"] for row in artists}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    from giye.normalize.language import language_for

    language = language_for(ledger.config)
    for artist in list(artists):
        members = members_of(artist, language)
        if not members:
            continue
        team_lid = artist["ledger_id"]
        for mem in [row for row in membership if row["ledger_id"] == team_lid]:
            frame = mem["frame_code"]
            team_acts = [
                row for row in activities if row["ledger_id"] == team_lid and row.get("origin") == frame
            ]
            source = mem.get("source_url") or artist.get("source_url") or ""
            collected = (mem.get("collected_at") or artist.get("collected_at") or "")[:10]
            shaped = {
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
            for row in member_rows(
                shaped,
                team_lid,
                source_url=source,
                source_type=artist.get("source_type") or "PUBLIC_RECORD",
                collected=collected,
                team_prefix=ledger.config.field_config.team_prefix or "팀:",
            ):
                existing = _find_member(artists, row)
                if existing is None and _ambiguous(artists, row):
                    continue
                if existing is None:
                    if dry_run:
                        created.append(row.get("name_ko") or row.get("name_en") or "")
                        continue
                    lid = _new_id(taken)
                    gy = allocate_gy_id(issued, prefix=ledger.config.id_prefix or "GY")
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
                    created.append(lid)
                    changed = True
                note = row.get("reviewer_note") or ""
                if note and note not in (existing.get("reviewer_note") or ""):
                    existing["reviewer_note"] = f"{existing.get('reviewer_note') or ''}; {note}".strip("; ")
                    changed = True
                if not any(item["ledger_id"] == existing["ledger_id"] and item["frame_code"] == frame for item in membership):
                    membership.append(
                        empty_row(
                            MEMBERSHIP_FIELDS,
                            ledger_id=existing["ledger_id"],
                            frame_code=frame,
                            source_url=source,
                            collected_at=collected,
                        )
                    )
                    changed = True
                for activity in row.get("activities") or []:
                    if _has_activity(activities, existing["ledger_id"], frame, activity):
                        continue
                    if dry_run:
                        continue
                    activities.append(
                        _member_activity(activities, existing["ledger_id"], frame, activity, source, collected)
                    )
                    changed = True
    if changed and not dry_run:
        ledger.write("artists", artists, task="expand-teams")
        ledger.write("activities", activities, task="expand-teams")
        ledger.write("frame_membership", membership, task="expand-teams")
    return created


def _split_pipe(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def _find_member(artists: list[dict], member: Mapping[str, str]) -> dict | None:
    ko = (member.get("name_ko") or "").strip()
    if not ko:
        en = (member.get("name_en") or "").strip()
        named = [row for row in artists if row.get("name_ko") == en and row.get("name_en") == en]
        return named[0] if len(named) == 1 else None
    named = [row for row in artists if row.get("name_ko") == ko]
    if len(named) == 1:
        return named[0]
    en = (member.get("name_en") or "").strip()
    exact = [row for row in named if row.get("name_en") == en]
    return exact[0] if len(exact) == 1 else None


def _ambiguous(artists: list[dict], member: Mapping[str, str]) -> bool:
    """True when several rows share the name and none is an exact match. Do not guess."""
    return _find_member(artists, member) is None and bool(
        [row for row in artists if row.get("name_ko") == (member.get("name_ko") or member.get("name_en") or "")]
    )


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
