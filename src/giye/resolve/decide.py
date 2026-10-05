# SPDX-License-Identifier: AGPL-3.0-only
"""The steps a person decides: the review queue, a merge, and hiding a page.

The pipeline queues a pair it will not merge. These functions record that
decision on the ledger the resolver already uses. A merge goes through
``Ledger.merge`` (the dropped ``gy_id`` is retired and redirects) after the
same team guard automatic merges use. CV files are joined and folded the same
way. Every write is a ledger write, so the dated backup already happens there.

A merge a person makes keeps the same documentary guarantee as an automatic
one (docs/RULES.md, manual merges). The evidence string names a rule and a
source: an E-code (``E1``-``E4``, or ``X1+E1``-``X1+E4``) followed by a
citation (an http(s) URL, a ``cv_sources`` source id, or a roster edition
code on the ledger), or ``H`` (a person's judgement) followed by the reason
and an ISO date. Free text alone is refused, because a merge nobody can trace
to a document or a dated judgement cannot be contested. A pair a person
decided ``distinct`` is not merged unless the caller passes
``override_distinct=True``; the evidence then records which decision it
overrides.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from giye.config import GiyeError
from giye.ledger.ledger import Ledger
from giye.normalize.language import language_for
from giye.resolve.candidates import (
    absorption_map,
    evidence_rule_id,
    explicit_decision,
    review_id_set,
    set_evidence_snapshot,
)
from giye.resolve.cv import fold_merged_cvs, move_extract_file
from giye.resolve.teams import team_person_mismatch


def list_queue(ledger: Ledger, *, kind: str | None = None, status: str | None = "open") -> list[dict[str, str]]:
    """Review rows. ``kind`` is the ``reason`` column. ``status`` None lists every status."""
    rows = ledger.read("review_queue") if ledger.path("review_queue").exists() else []
    if kind:
        rows = [row for row in rows if row.get("reason") == kind]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    return rows


def decide_queue(
    ledger: Ledger,
    item_id: str,
    decision: str,
    *,
    evidence: str = "",
    note: str = "",
    override_distinct: bool = False,
) -> dict[str, str]:
    """Close one queue item.

    ``merge`` joins the two ledger ids the item names. Evidence is required,
    because ``Ledger.merge`` refuses an empty string. ``distinct`` records
    ``decided=different`` and does not merge. ``dismiss`` records
    ``decided=dismissed`` and sets the status to ``dismissed``. Either decision
    stays on that pair: a later resolve does not open a new item for the same
    unordered pair, including after a merge absorbs one side. The detail also
    records ``evidence_at_decision=`` for the E-rules that already hold. An
    E-rule that did not hold then reopens this same item; it does not merge
    over the decision.

    A merge needs evidence in the form :func:`check_merge_evidence` accepts.
    ``override_distinct`` is passed to :func:`merge_people`.
    """
    if decision not in ("merge", "distinct", "dismiss"):
        raise GiyeError("decision must be merge, distinct, or dismiss")
    review = ledger.read("review_queue") if ledger.path("review_queue").exists() else []
    item = next((row for row in review if row.get("queue_id") == item_id), None)
    if item is None:
        raise GiyeError(f"no review item {item_id}")
    if decision == "merge":
        _decide_merge(ledger, item, evidence=evidence, note=note, override_distinct=override_distinct)
        return item
    if decision == "distinct":
        if item.get("reason") != "possible_same_person":
            raise GiyeError(f"{item_id} is {item.get('reason')}, not possible_same_person")
        _mark(item, decision="different", note=note, status="done")
    else:
        _mark(item, decision="", note=note, status="dismissed")
        if "decided=dismissed" not in (item.get("detail") or ""):
            item["detail"] = f"{item.get('detail') or ''}; decided=dismissed".strip("; ")
    _stamp_evidence(ledger, item)
    ledger.write("review_queue", review, task="decide")
    return item


def merge_people(
    ledger: Ledger,
    keep: str,
    drop: str,
    *,
    evidence: str,
    override_distinct: bool = False,
) -> tuple[str, str]:
    """Merge two people. Returns ``(kept ledger id, dropped ledger id)``.

    ``keep`` and ``drop`` may be a ``ledger_id`` or a ``gy_id``. A team and a
    person are refused (T1). ``evidence`` must name a rule and a source (see
    :func:`check_merge_evidence`); its code is stored as the merge's ``rule``.
    A pair decided ``distinct`` on the review queue is refused unless
    ``override_distinct`` is set; the stored evidence then says
    "overrides distinct decision of <date>" and that queue item is rewritten
    so it no longer reads ``decided=different``. The dropped ``gy_id`` is
    retired with a redirect, the same path an automatic merge uses.
    """
    code = check_merge_evidence(ledger, evidence)
    artists = ledger.read("artists")
    kept = _person(artists, keep)
    dropped = _person(artists, drop)
    if kept["ledger_id"] == dropped["ledger_id"]:
        raise GiyeError("merge needs two different people")
    language = language_for(ledger.config)
    words = ledger.config.field_config.compiled_team_words()
    if team_person_mismatch(kept, dropped, words=words, language=language):
        raise GiyeError("merge refused: a team and a person are not the same record (T1)")
    keep_id, drop_id = kept["ledger_id"], dropped["ledger_id"]
    distinct = _distinct_items(ledger, artists, keep_id, drop_id)
    text = evidence.strip()
    if distinct and not override_distinct:
        dates = ", ".join(_decided_on(item) for item in distinct)
        raise GiyeError(
            f"merge refused: this pair was decided distinct ({dates}); "
            "pass override_distinct to merge over that decision"
        )
    if distinct:
        dates = ", ".join(_decided_on(item) for item in distinct)
        # A comma, not a semicolon: the kept row's note is split on ";".
        text = f"{text}, overrides distinct decision of {dates}"
    move_extract_file(ledger.config, keep_id, drop_id)
    ledger.merge(keep_id, drop_id, evidence=text, rule=code)
    if distinct:
        _clear_distinct(ledger, {item.get("queue_id") or "" for item in distinct}, dates)
    fold_merged_cvs(ledger)
    return keep_id, drop_id


# An E-rule code (X1 is the shared-key precondition, never a reason on its own)
# or H, a person's judgement. Separators between the code and the rest are loose
# so "E2: https://…" and "E2 https://…" are the same statement.
_E_EVIDENCE = re.compile(r"^(?P<code>(?:X1\+)?E[1-4])(?![0-9A-Za-z])[\s:;,.\-–—]*(?P<rest>.*)$", re.DOTALL)
_H_EVIDENCE = re.compile(r"^(?P<code>H)(?![0-9A-Za-z])[\s:;,.\-–—]*(?P<rest>.*)$", re.DOTALL)
_URL = re.compile(r"https?://[^\s,;()<>]+\.[^\s,;()<>]+")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TOKEN_STRIP = "()[]{}<>,;:.'\"“”‘’"
_EVIDENCE_FORM = (
    "merge evidence must name a rule and a source: an E-code (E1-E4 or X1+E1..E4) followed by a "
    "citation (an http(s) URL, a CV source id, or a roster edition code), or H followed by the reason "
    "and the date (YYYY-MM-DD)"
)


def check_merge_evidence(ledger: Ledger, evidence: str) -> str:
    """Return the rule code of a well-formed manual merge evidence string, or raise ``GiyeError``.

    ``E1``-``E4`` and ``X1+E1``-``X1+E4`` need a citation in the rest of the
    string: an http(s) URL, a ``source_id`` registered in ``cv_sources``, or a
    roster edition (a ``frame_code`` on the ledger, or ``<frame_code>-<YYYY>``).
    The citation is what a reader follows to check the merge. ``H`` needs a
    reason and a real calendar date, so a judgement is at least attributable
    to a moment. Anything else, including free text, is refused.
    """
    if not isinstance(evidence, str) or not evidence.strip():
        raise GiyeError(f"merge refused without an evidence string; {_EVIDENCE_FORM}")
    text = evidence.strip()
    matched = _E_EVIDENCE.match(text)
    if matched:
        rest = matched.group("rest").strip()
        if not _has_citation(ledger, rest):
            raise GiyeError(f"merge refused: {matched.group('code')} names no citation; {_EVIDENCE_FORM}")
        return matched.group("code")
    matched = _H_EVIDENCE.match(text)
    if matched:
        rest = matched.group("rest").strip()
        if not _has_date(rest):
            raise GiyeError(f"merge refused: H needs the date of the judgement; {_EVIDENCE_FORM}")
        if not re.search(r"\w", _ISO_DATE.sub("", rest)):
            raise GiyeError(f"merge refused: H needs the reason for the judgement; {_EVIDENCE_FORM}")
        return "H"
    raise GiyeError(f"merge refused: {_EVIDENCE_FORM}")


def _has_date(text: str) -> bool:
    """True when ``text`` contains a valid ISO calendar date."""
    for year, month, day in _ISO_DATE.findall(text):
        try:
            date(int(year), int(month), int(day))
        except ValueError:
            continue
        return True
    return False


def _has_citation(ledger: Ledger, text: str) -> bool:
    """An http(s) URL, a registered CV source id, or a roster edition code on this ledger."""
    if _URL.search(text):
        return True
    tokens = {token.strip(_TOKEN_STRIP) for token in text.split()} - {""}
    if not tokens:
        return False
    if ledger.path("cv_sources").exists():
        sources = {row.get("source_id") or "" for row in ledger.read("cv_sources")} - {""}
        if tokens & sources:
            return True
    codes: set[str] = set()
    if ledger.path("frame_membership").exists():
        codes = {row.get("frame_code") or "" for row in ledger.read("frame_membership")} - {""}
    for token in tokens:
        if token in codes:
            return True
        base, _, year = token.rpartition("-")
        if base in codes and len(year) == 4 and year.isdigit():
            return True
    return False


def _distinct_items(ledger: Ledger, artists: list[dict[str, str]], left: str, right: str) -> list[dict[str, str]]:
    """Queue items where a person decided this pair ``distinct``, merge chains followed."""
    if not ledger.path("review_queue").exists():
        return []
    absorbed = absorption_map(artists)

    def canon(item: str) -> str:
        return absorbed.get(item, item)

    pair = {canon(left), canon(right)}
    found = []
    for item in ledger.read("review_queue"):
        if item.get("reason") != "possible_same_person":
            continue
        if explicit_decision(item.get("detail") or "") != "different":
            continue
        if pair <= {canon(other) for other in review_id_set(item)}:
            found.append(item)
    return found


def _decided_on(item: dict[str, str]) -> str:
    """The date a decision was recorded (``decided_at=``), or ``undated`` for an older item."""
    matched = _DECIDED_AT.search(item.get("detail") or "")
    return matched.group(1) if matched else "undated"


def _clear_distinct(ledger: Ledger, queue_ids: set[str], dates: str) -> None:
    """Rewrite overridden distinct items so the queue no longer says ``decided=different``."""
    review = ledger.read("review_queue")
    for item in review:
        if item.get("queue_id") not in queue_ids:
            continue
        parts = [part.strip() for part in (item.get("detail") or "").split(";")]
        kept = [part for part in parts if part and part != "decided=different" and not _DECIDED_AT.fullmatch(part)]
        kept.append(f"overrides distinct decision of {dates}")
        if "decided=same" not in kept:
            kept.append("decided=same")
            kept.append(f"decided_at={_today()}")
        item["detail"] = "; ".join(kept)
        item["status"] = "done"
    ledger.write("review_queue", review, task="decide")


_DECIDED_AT = re.compile(r"decided_at=(\d{4}-\d{2}-\d{2})")


def _today() -> str:
    """UTC date written beside a decision."""
    return datetime.now(timezone.utc).date().isoformat()


def hide_person(ledger: Ledger, gy_id: str, *, reason: str) -> None:
    """Set ``HIDDEN_BY_REQUEST``. Publish already writes a nameless tombstone for that status."""
    if not isinstance(reason, str) or not reason.strip():
        raise GiyeError("hide needs a reason")
    artists = ledger.read("artists")
    row = _person(artists, gy_id, gy_only=True)
    if row.get("status") == "HIDDEN_BY_REQUEST":
        raise GiyeError(f"{row.get('gy_id')} is already hidden")
    row["status"] = "HIDDEN_BY_REQUEST"
    marker = f"hidden={reason.strip()}"
    note = row.get("reviewer_note") or ""
    if marker not in note:
        row["reviewer_note"] = f"{note}; {marker}".strip("; ")
    ledger.write("artists", artists, task="hide")


def unhide_person(ledger: Ledger, gy_id: str) -> None:
    """Clear ``HIDDEN_BY_REQUEST`` so the next publish includes the page again."""
    artists = ledger.read("artists")
    row = _person(artists, gy_id, gy_only=True)
    if row.get("status") != "HIDDEN_BY_REQUEST":
        raise GiyeError(f"{row.get('gy_id')} is not hidden")
    row["status"] = "PUBLISHED" if row.get("cv_link_ok") == "yes" else "STAGED"
    ledger.write("artists", artists, task="unhide")


def _decide_merge(
    ledger: Ledger, item: dict[str, str], *, evidence: str, note: str, override_distinct: bool = False
) -> None:
    """Merge the pair a queue item names, then record ``decided=same`` on that item."""
    if item.get("reason") != "possible_same_person":
        raise GiyeError(f"{item.get('queue_id')} is {item.get('reason')}, not possible_same_person")
    check_merge_evidence(ledger, evidence)
    others = _other_ids(item)
    if len(others) != 1:
        raise GiyeError(f"{item.get('queue_id')} does not name exactly one other person")
    merge_people(
        ledger, item.get("ledger_id") or "", others[0], evidence=evidence, override_distinct=override_distinct
    )
    # merge rewrote the queue. Read it again and record the person's decision
    # so a later resolve does not treat the close as accidental.
    review = ledger.read("review_queue")
    current = next((row for row in review if row.get("queue_id") == item.get("queue_id")), None)
    if current is None:
        return
    _mark(current, decision="same", note=note, status="done")
    ledger.write("review_queue", review, task="decide")
    item.update(current)


def _stamp_evidence(ledger: Ledger, item: dict[str, str]) -> None:
    """Record the E-rules that already hold, so a later one can reopen this item."""
    from giye.resolve.service import evidence_for_pair

    others = _other_ids(item)
    rules: set[str] = set()
    left = item.get("ledger_id") or ""
    if left and len(others) == 1:
        rule = evidence_rule_id(evidence_for_pair(ledger, left, others[0]))
        if rule:
            rules.add(rule)
    item["detail"] = set_evidence_snapshot(item.get("detail") or "", rules)


def _mark(item: dict[str, str], *, decision: str, note: str, status: str) -> None:
    """Write ``decided=`` and a note onto the item, and set its status. A second decision is refused."""
    detail = item.get("detail") or ""
    if decision and explicit_decision(detail) not in ("", decision):
        raise GiyeError(f"{item.get('queue_id')} is already decided={explicit_decision(detail)}")
    if decision and f"decided={decision}" not in detail:
        detail = f"{detail}; decided={decision}; decided_at={_today()}".strip("; ")
    if note.strip():
        detail = f"{detail}; {note.strip()}".strip("; ")
    item["detail"] = detail
    item["status"] = status


def _other_ids(item: dict[str, str]) -> list[str]:
    """Ledger ids in the detail other than the item's own id."""
    own = item.get("ledger_id") or ""
    return sorted(review_id_set(item) - {own})


def _person(artists: list[dict[str, str]], token: str, *, gy_only: bool = False) -> dict[str, str]:
    """The artist row for a ``gy_id`` or, unless ``gy_only``, a ledger id."""
    text = (token or "").strip()
    if not text:
        raise GiyeError("missing person id")
    for row in artists:
        if row.get("gy_id") == text or (not gy_only and row.get("ledger_id") == text):
            return row
    raise GiyeError(f"no person {text}")
