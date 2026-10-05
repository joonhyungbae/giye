# SPDX-License-Identifier: AGPL-3.0-only
"""The steps a person decides: the review queue, a merge, and hiding a page.

The pipeline queues a pair it will not merge. These functions record that
decision on the ledger the resolver already uses. A merge goes through
``Ledger.merge`` (the dropped ``gy_id`` is retired and redirects) after the
same team guard automatic merges use. CV files are joined and folded the same
way. Every write is a ledger write, so the dated backup already happens there.
"""

from __future__ import annotations

from giye.config import GiyeError
from giye.ledger.ledger import Ledger
from giye.normalize.language import language_for
from giye.resolve.candidates import evidence_rule_id, explicit_decision, set_evidence_snapshot
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
    """
    if decision not in ("merge", "distinct", "dismiss"):
        raise GiyeError("decision must be merge, distinct, or dismiss")
    review = ledger.read("review_queue") if ledger.path("review_queue").exists() else []
    item = next((row for row in review if row.get("queue_id") == item_id), None)
    if item is None:
        raise GiyeError(f"no review item {item_id}")
    if decision == "merge":
        _decide_merge(ledger, item, evidence=evidence, note=note)
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


def merge_people(ledger: Ledger, keep: str, drop: str, *, evidence: str) -> tuple[str, str]:
    """Merge two people. Returns ``(kept ledger id, dropped ledger id)``.

    ``keep`` and ``drop`` may be a ``ledger_id`` or a ``gy_id``. A team and a
    person are refused (T1). The dropped ``gy_id`` is retired with a redirect,
    the same path an automatic merge uses.
    """
    if not isinstance(evidence, str) or not evidence.strip():
        raise GiyeError("merge refused without an evidence string")
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
    move_extract_file(ledger.config, keep_id, drop_id)
    ledger.merge(keep_id, drop_id, evidence=evidence.strip(), rule="manual")
    fold_merged_cvs(ledger)
    return keep_id, drop_id


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


def _decide_merge(ledger: Ledger, item: dict[str, str], *, evidence: str, note: str) -> None:
    """Merge the pair a queue item names, then record ``decided=same`` on that item."""
    if item.get("reason") != "possible_same_person":
        raise GiyeError(f"{item.get('queue_id')} is {item.get('reason')}, not possible_same_person")
    if not evidence.strip():
        raise GiyeError("merge refused without an evidence string")
    others = _other_ids(item)
    if len(others) != 1:
        raise GiyeError(f"{item.get('queue_id')} does not name exactly one other person")
    merge_people(ledger, item.get("ledger_id") or "", others[0], evidence=evidence)
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
        detail = f"{detail}; decided={decision}".strip("; ")
    if note.strip():
        detail = f"{detail}; {note.strip()}".strip("; ")
    item["detail"] = detail
    item["status"] = status


def _other_ids(item: dict[str, str]) -> list[str]:
    """Ledger ids in the detail other than the item's own id."""
    from giye.resolve.candidates import review_id_set

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
