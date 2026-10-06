# SPDX-License-Identifier: AGPL-3.0-only
"""Split a roster membership off a record: the reverse of a wrong attachment.

Attachment (A1–A6, ``giye.resolve.attach``) joins a roster line to a record
that already exists. When a person judges that the line names someone else,
:func:`split_membership` gives that line its own record:

- a new record with a fresh permanent ``gy_id`` (one past the highest ever
  issued, retired ids included, so no number is reused). Its name is the
  roster line's printed name (``<work>/rosters/*.csv``), else the name of the
  record the line was attached to, unless the caller states one;
- the membership ``(ledger_id, frame_code)`` and the activity rows whose
  ``origin`` is that edition code move to the new record. Their activity ids
  stay, as they do in a merge. CV rows (``origin`` ``cv:<source_id>``) are
  never moved: a CV row belongs to the owner of its CV source
  (``cv_sources``), and that does not change;
- both records' ``reviewer_note`` gain ``split <ledger_id>@<frame_code> to
  <new ledger id>; split_evidence=H …; rule=H``;
- a ``possible_same_person`` queue item for the pair is closed
  ``decided=different``, so ``giye resolve`` never merges them again unless a
  person overrides that decision (``override_distinct``), and a later
  collection of the same edition puts the line on the new record
  (``giye.ledger.ledger``, split redirect).

The old record keeps its ``gy_id`` and its page. A split is refused for a
record hidden by request, for the record's only membership (the record would
be left with no roster), and for evidence that is not a valid ``H``: a reason
of at least three words and an ISO date that is a real date no later than
today, the same check a manual ``H`` merge runs. Why only ``H``: the E-rules
are evidence that two records are one person; there is no rule yet that
proves a line names a different person, so the split is a dated judgement.

A batch (:func:`split_memberships`, ``giye split --from``) plans every row on
the tables in memory first and writes each table once, so one dated backup
covers the whole batch and a refused row writes nothing.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from giye.config import GiyeError
from giye.ledger.ids import allocate_gy_id
from giye.ledger.ledger import HIDDEN, Ledger, _new_ledger_id, _spelling, _stored_name
from giye.ledger.schemas import ARTISTS_FIELDS, empty_row, split_pipe
from giye.resolve.decide import _H_EVIDENCE, _has_date, _person, _stamp_evidence, _verify_h

# The backup label of every table a split writes: ``<file>-<date>-before-split.csv.gz``.
TASK = "split"

# Columns of a ``giye split --from`` file. ``name_ko`` and ``name_en`` are optional.
BATCH_FIELDS = ("membership_id", "evidence", "name_ko", "name_en")

# ``split <ledger_id>@<frame_code> to <new ledger id>`` in a reviewer note.
SPLIT_NOTE = re.compile(r"^split\s+(\S+?)@(\S+)\s+to\s+(\S+)$")


@dataclass(frozen=True)
class SplitRequest:
    """One membership to split, the H evidence, and an optional stated name."""

    membership: str | tuple[str, str]
    evidence: str
    name_ko: str = ""
    name_en: str = ""


@dataclass
class SplitResult:
    """What one split did (or, in a dry run, would do)."""

    membership_id: str
    old_ledger_id: str
    old_gy_id: str
    new_ledger_id: str
    new_gy_id: str
    name_ko: str
    name_en: str
    name_from: str
    moved_activities: list[str] = field(default_factory=list)


def split_membership(
    ledger: Ledger,
    membership: str | tuple[str, str],
    *,
    evidence: str,
    name_ko: str = "",
    name_en: str = "",
    dry_run: bool = False,
) -> SplitResult:
    """Split one membership to a new record. Returns what was done; raises ``GiyeError`` when refused.

    ``membership`` is ``"<ledger_id>@<frame_code>"`` or ``(ledger_id,
    frame_code)``; a ``gy_id`` may stand for the ledger id. ``evidence`` is
    ``H <reason>; <YYYY-MM-DD>``. ``name_ko`` / ``name_en``: when either is
    given, the new record takes exactly these names instead of the roster
    line's. ``dry_run`` plans the split and writes nothing.
    """
    request = SplitRequest(membership, evidence, name_ko, name_en)
    return split_memberships(ledger, [request], dry_run=dry_run)[0]


def split_memberships(
    ledger: Ledger, requests: Sequence[SplitRequest], *, dry_run: bool = False
) -> list[SplitResult]:
    """Split several memberships under one backup. Every row is checked before anything is written."""
    if not requests:
        raise GiyeError("split needs at least one membership")
    tables = _Tables.load(ledger)
    results: list[SplitResult] = []
    for number, request in enumerate(requests, start=1):
        try:
            results.append(_plan(ledger, tables, request))
        except GiyeError as exc:
            if len(requests) == 1:
                raise
            raise GiyeError(f"row {number}: {exc}") from None
    if dry_run:
        return results
    ledger.write("artists", tables.artists, task=TASK)
    ledger.write("activities", tables.activities, task=TASK)
    ledger.write("frame_membership", tables.membership, task=TASK)
    ledger.write("review_queue", tables.review, task=TASK)
    # Record the E-rules that already hold for each pair, as a distinct
    # decision does, so only a rule that did not hold today reopens the item.
    review = ledger.read("review_queue")
    wanted = set(tables.new_items)
    for item in review:
        if item.get("queue_id") in wanted:
            _stamp_evidence(ledger, item)
    ledger.write("review_queue", review, task=TASK)
    return results


def read_batch(path: str | Path) -> list[SplitRequest]:
    """Requests from a CSV with ``membership_id`` and ``evidence`` (``name_ko``, ``name_en`` optional)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise GiyeError(f"cannot read split batch {path}: {exc}") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    columns = set(reader.fieldnames or [])
    missing = {"membership_id", "evidence"} - columns
    if missing:
        raise GiyeError(f"split batch {path} lacks column(s): {', '.join(sorted(missing))}")
    # An unknown column (a typo of name_en) would be dropped without a word.
    unknown = columns - set(BATCH_FIELDS)
    if unknown:
        raise GiyeError(f"split batch {path} has unknown column(s): {', '.join(sorted(unknown))}")
    return [
        SplitRequest(
            (row.get("membership_id") or "").strip(),
            row.get("evidence") or "",
            (row.get("name_ko") or "").strip(),
            (row.get("name_en") or "").strip(),
        )
        for row in reader
    ]


def check_split_evidence(evidence: str) -> str:
    """The evidence a split stores, or raise ``GiyeError``: ``H``, a reason of three words, a past ISO date."""
    if not isinstance(evidence, str) or not evidence.strip():
        raise GiyeError("split refused without an evidence string; give H with the reason and the date (YYYY-MM-DD)")
    text = evidence.strip()
    matched = _H_EVIDENCE.match(text)
    if not matched:
        raise GiyeError("split refused: the evidence must be H followed by the reason and the date (YYYY-MM-DD)")
    rest = matched.group("rest").strip()
    if not _has_date(rest):
        raise GiyeError("split refused: H needs the date of the judgement (YYYY-MM-DD)")
    try:
        # The same reason and date checks as a manual H merge.
        _verify_h(rest)
    except GiyeError as exc:
        raise GiyeError(str(exc).replace("merge refused", "split refused", 1)) from None
    return text


def split_targets(artists: Iterable[dict[str, str]]) -> dict[tuple[str, str], str]:
    """``(old ledger id, frame_code)`` → new ledger id, from the ``split … to …`` notes."""
    found: dict[tuple[str, str], str] = {}
    for row in artists:
        for part in (row.get("reviewer_note") or "").split(";"):
            matched = SPLIT_NOTE.match(part.strip())
            if matched:
                found[(matched.group(1), matched.group(2))] = matched.group(3)
    return found


@dataclass
class _Tables:
    """The tables a split edits, read once and written once."""

    artists: list[dict[str, str]]
    activities: list[dict[str, str]]
    membership: list[dict[str, str]]
    review: list[dict[str, str]]
    issued: list[str]
    rosters: list[dict[str, str]]
    new_items: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, ledger: Ledger) -> _Tables:
        artists = ledger.read("artists")
        issued = [row.get("gy_id") or "" for row in artists]
        issued += [row.get("gy_id") or "" for row in ledger.read("gy_retired")]
        return cls(
            artists=artists,
            activities=ledger.read("activities"),
            membership=ledger.read("frame_membership"),
            review=ledger.read("review_queue") if ledger.path("review_queue").exists() else [],
            issued=issued,
            rosters=_roster_rows(ledger),
        )


def _roster_rows(ledger: Ledger) -> list[dict[str, str]]:
    """Every collection report row (``<work>/rosters/*.csv``): the printed names of each edition."""
    from giye.ledger.io import read_csv

    folder = Path(ledger.config.work) / "rosters"
    if not folder.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for path in sorted(folder.glob("*.csv")):
        rows.extend(read_csv(path))
    return rows


def _parse_membership(membership: str | tuple[str, str]) -> tuple[str, str]:
    if isinstance(membership, tuple):
        if len(membership) != 2:
            raise GiyeError("a membership is (ledger_id, frame_code)")
        person, frame = (str(part).strip() for part in membership)
    else:
        person, _, frame = str(membership or "").strip().partition("@")
        person, frame = person.strip(), frame.strip()
    if not person or not frame:
        raise GiyeError(f"a membership is <ledger_id>@<frame_code>, got {membership!r}")
    return person, frame


def _plan(ledger: Ledger, tables: _Tables, request: SplitRequest) -> SplitResult:
    """Apply one split to the in-memory tables, after every refusal check."""
    text = check_split_evidence(request.evidence)
    person, frame = _parse_membership(request.membership)
    old = _person(tables.artists, person)
    lid = old["ledger_id"]
    if old.get("status") == HIDDEN:
        raise GiyeError(
            f"split refused: {lid} is hidden by request; a split would publish part of its data "
            "(run giye unhide first if the person asks for the split)"
        )
    own = [row for row in tables.membership if row.get("ledger_id") == lid]
    target = next((row for row in own if row.get("frame_code") == frame), None)
    if target is None:
        raise GiyeError(f"no membership {lid}@{frame}")
    if len(own) == 1:
        raise GiyeError(f"split refused: {lid}@{frame} is the record's only membership; nothing would remain")

    name_ko, name_en, name_from = _new_name(tables, old, target, request)
    new_lid = _new_ledger_id({row["ledger_id"] for row in tables.artists})
    gy = allocate_gy_id(tables.issued, prefix=ledger.config.id_prefix or "GY")
    tables.issued.append(gy)
    stamp = _now()
    mid = f"{lid}@{frame}"
    # A comma, not a semicolon: a note is split on ";", and the date must stay in its segment.
    stored = " ".join(text.replace(";", ",").split())
    marker = f"split {mid} to {new_lid}; split_evidence={stored}; rule=H"
    new = empty_row(
        ARTISTS_FIELDS,
        ledger_id=new_lid,
        gy_id=gy,
        name_ko=name_ko,
        name_en=name_en,
        cv_link_ok="no",
        frame_status="IN_FRAME",
        verification="UNVERIFIED",
        status="STAGED",
        source_url=target.get("source_url") or "",
        source_type="PUBLIC_RECORD",
        collected_at=target.get("collected_at") or "",
        reviewer_note=marker,
        updated_at=stamp,
    )
    tables.artists.append(new)
    old["reviewer_note"] = f"{old.get('reviewer_note') or ''}; {marker}".strip("; ")
    old["updated_at"] = stamp
    _reseat_source(old, target, [row for row in own if row is not target])

    # The membership keeps its source and date. Its rule says it was split,
    # and from which attachment, so an audit of A-rule attachments does not
    # count it as an attachment to an existing record.
    target["ledger_id"] = new_lid
    target["attach_rule"] = f"split:{(target.get('attach_rule') or '').strip() or 'unrecorded'}"
    moved: list[str] = []
    for row in tables.activities:
        origin = row.get("origin") or ""
        if row.get("ledger_id") == lid and origin == frame and not origin.startswith("cv:"):
            row["ledger_id"] = new_lid
            moved.append(row.get("activity_id") or "")

    item = empty_row(
        ledger.fields("review_queue"),
        queue_id=_queue_id(tables.review),
        ledger_id=lid,
        reason="possible_same_person",
        detail=f"split {mid} to {new_lid}: different people; decided=different; decided_at={stamp[:10]}; rule=H",
        status="done",
        created_at=stamp,
    )
    tables.review.append(item)
    tables.new_items.append(item["queue_id"])
    return SplitResult(
        membership_id=mid,
        old_ledger_id=lid,
        old_gy_id=old.get("gy_id") or "",
        new_ledger_id=new_lid,
        new_gy_id=gy,
        name_ko=name_ko,
        name_en=name_en,
        name_from=name_from,
        moved_activities=moved,
    )


def _new_name(
    tables: _Tables, old: dict[str, str], target: dict[str, str], request: SplitRequest
) -> tuple[str, str, str]:
    """``(name_ko, name_en, where from)`` for the new record, stored as a roster insert stores it.

    A stated name wins. Otherwise the edition's roster line: a line of that
    edition code whose printed name is a spelling the old record carries
    (``name_ko``, ``name_en`` or an alias, as ``_keep_printed_names`` keeps
    them), from the membership's source page when one matches, and not a
    spelling of another record on the same edition. Exactly one such line is
    the name; none or several fall back to the old record's name, because a
    guess between two printed names would be a hand choice.
    """
    if request.name_ko.strip() or request.name_en.strip():
        ko, en = _stored_name(" ".join(request.name_ko.split()), " ".join(request.name_en.split()))
        return ko, en, "stated"
    frame = target.get("frame_code") or ""
    lines = [row for row in tables.rosters if (row.get("frame_code") or "") == frame]
    source = target.get("source_url") or ""
    if source and any((row.get("source_url") or "") == source for row in lines):
        lines = [row for row in lines if (row.get("source_url") or "") == source]
    carried = _spellings(old)
    others = [
        _spellings(row)
        for row in tables.artists
        if row is not old
        and any(m.get("ledger_id") == row["ledger_id"] and m.get("frame_code") == frame for m in tables.membership)
    ]
    found = []
    for line in lines:
        printed = {_spelling(" ".join((line.get(key) or "").split())) for key in ("name_ko", "name_en")} - {""}
        if printed and printed <= carried and not any(printed & other for other in others):
            found.append(line)
    pairs = {(" ".join((row.get("name_ko") or "").split()), " ".join((row.get("name_en") or "").split())) for row in found}
    if len(pairs) == 1:
        ko, en = _stored_name(*next(iter(pairs)))
        return ko, en, "roster"
    ko, en = _stored_name(old.get("name_ko") or "", old.get("name_en") or "")
    return ko, en, "record"


def _spellings(row: dict[str, str]) -> set[str]:
    values = [row.get("name_ko") or "", row.get("name_en") or "", *split_pipe(row.get("aliases"))]
    return {_spelling(value) for value in values} - {""}


def _reseat_source(old: dict[str, str], target: dict[str, str], remaining: list[dict[str, str]]) -> None:
    """Point the old record's ``source_url`` at a roster it is still on.

    Why: the record cited the split edition's page, which no longer lists this
    person. The new source is the remaining membership with the earliest
    ``collected_at`` (then ``frame_code``), so the choice is a rule. A source
    another remaining membership also cites is kept.
    """
    url = old.get("source_url") or ""
    if not url or url != (target.get("source_url") or ""):
        return
    if any((row.get("source_url") or "") == url for row in remaining):
        return
    cited = sorted(
        (row for row in remaining if (row.get("source_url") or "").startswith("http")),
        key=lambda row: (row.get("collected_at") or "9999", row.get("frame_code") or ""),
    )
    if cited:
        old["source_url"] = cited[0]["source_url"]
        old["collected_at"] = cited[0].get("collected_at") or old.get("collected_at") or ""


def _queue_id(review: list[dict[str, str]]) -> str:
    import uuid

    taken = {row.get("queue_id") for row in review}
    while True:
        value = str(uuid.uuid4())
        if value not in taken:
            return value


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
