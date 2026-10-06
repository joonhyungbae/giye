# SPDX-License-Identifier: AGPL-3.0-only
"""The steps a person decides: the review queue, a merge, and hiding a page.

The pipeline queues a pair it will not merge. These functions record that
decision on the ledger the resolver already uses. :func:`merge_people` is the
one checked merge path; the public ``Ledger.merge`` delegates to it. It runs
the same team guard automatic merges use, then writes the merge (the dropped
``gy_id`` is retired and redirects). CV files are joined and folded the same
way. Every write is a ledger write, so the dated backup already happens there.

A merge a person makes keeps the same documentary guarantee as an automatic
one (docs/RULES.md, manual merges). The evidence string names a rule and a
source: an E-code (``E1``-``E4``, or ``X1+E1``-``X1+E4``) followed by a
citation (an http(s) URL, a ``cv_sources`` source id, or a roster edition
code on the ledger), or ``H`` (a person's judgement) followed by the reason
and an ISO date. Free text alone is refused, because a merge nobody can trace
to a document or a dated judgement cannot be contested. The cited evidence is
then checked against the ledger with the same code the resolver uses
(:func:`verify_merge_evidence`), so a stored ``E1`` always means a website both
records list, not a label someone typed. A pair a person
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
    :func:`check_merge_evidence`) and hold on the ledger
    (:func:`verify_merge_evidence`); its code is stored as the merge's ``rule``.
    A pair decided ``distinct`` on the review queue is refused unless
    ``override_distinct`` is set; the stored evidence then says
    "overrides distinct decision of <date>" and that queue item is rewritten
    so it no longer reads ``decided=different``. The dropped ``gy_id`` is
    retired with a redirect, the same path an automatic merge uses.
    """
    check_merge_evidence(ledger, evidence)
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
    code = verify_merge_evidence(ledger, keep_id, drop_id, evidence)
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
    # verify_merge_evidence ran above, so the unchecked path writes the merge.
    ledger._merge_rows(keep_id, drop_id, evidence=text, rule=code)
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


# H: a reason is at least this many words besides the date. Why: "same" or
# "checked" alone says nothing a later reader can contest.
_H_MIN_WORDS = 3
_WORD = re.compile(r"[^\W\d_][\w'’-]*")


def verify_merge_evidence(ledger: Ledger, keep: str, drop: str, evidence: str) -> str:
    """Check a manual merge's evidence against the ledger. Returns the rule code or raises ``GiyeError``.

    The form comes first (:func:`check_merge_evidence`). Then the cited
    evidence must hold for this pair of ledger ids, by the same code the
    resolver runs (``giye.resolve.evidence``):

    - ``E1``: a cited URL's site (``url_key``, the E1 key) is a website of both records.
    - ``E2``: a cited CV source (``cv_sources`` id or its URL) belongs to one
      record, and that CV lists a roster edition of the other (``cv_mentions``
      with the edition years, ± one year).
    - ``E3``: the cited work title (normalised) is a bracketed work both rosters
      credit, or one roster credits and the other's CV lists, in the year window,
      and it is not a generic title (``evidence.generic_titles``).
    - ``E4``: the cited team name is a team both rosters credit with the team prefix.
    - ``X1+E*``: the two records' name keys (``LanguageModule.name_keys`` over
      ``name_ko`` and ``name_en``) intersect, and the E part holds as above.
    - ``H``: every ISO date in the string is a real date no later than today,
      and the reason has at least three words.
    """
    code = check_merge_evidence(ledger, evidence)
    text = evidence.strip()
    rest = text[len(code) :].strip()
    if code == "H":
        _verify_h(rest)
        return code
    # Imported here: giye.resolve.service imports this module's package.
    from giye.resolve.service import _State

    state = _State(ledger)
    for lid in (keep, drop):
        if lid not in state.by_id:
            raise KeyError(lid)
    if keep == drop:
        raise GiyeError("merge needs two different people")
    base = code
    if code.startswith("X1+"):
        base = code[3:]
        left, right = state.by_id[keep], state.by_id[drop]
        if not _name_keys_of(left, state.language) & _name_keys_of(right, state.language):
            raise GiyeError(f"merge refused: {code} needs the two records' name keys to meet (X1), and they do not")
    check = {"E1": _verify_e1, "E2": _verify_e2, "E3": _verify_e3, "E4": _verify_e4}[base]
    problem = check(state, keep, drop, rest)
    if problem:
        raise GiyeError(f"merge refused: {code} does not hold on the ledger: {problem}")
    return code


def _verify_h(rest: str) -> None:
    """H: real dates, none in the future, and a reason of at least ``_H_MIN_WORDS`` words."""
    # The local calendar day, so a judgement dated today where the person sits is accepted.
    today = datetime.now(timezone.utc).astimezone().date()
    for year, month, day in _ISO_DATE.findall(rest):
        try:
            when = date(int(year), int(month), int(day))
        except ValueError:
            raise GiyeError(f"merge refused: H date {year}-{month}-{day} is not a calendar date") from None
        if when > today:
            raise GiyeError(f"merge refused: H date {when.isoformat()} is in the future")
    words = _WORD.findall(_ISO_DATE.sub(" ", rest))
    if len(words) < _H_MIN_WORDS:
        raise GiyeError(f"merge refused: H needs a reason of at least {_H_MIN_WORDS} words; {_EVIDENCE_FORM}")


def _name_keys_of(row: dict[str, str], language) -> set[str]:
    """Personal-name keys of a record's names, through the language module (X1)."""
    keys: set[str] = set()
    for value in (row.get("name_ko") or "", row.get("name_en") or ""):
        if value.strip():
            keys |= language.name_keys(value)
    return keys


def _cited_urls(rest: str) -> list[str]:
    return [url.rstrip(".,;:)") for url in _URL.findall(rest)]


def _verify_e1(state, keep: str, drop: str, rest: str) -> str:
    from giye.resolve.evidence import url_key

    keys = {url_key(url) for url in _cited_urls(rest)} - {""}
    if not keys:
        return "E1 cites no website URL"
    shared = state.sites.get(keep, set()) & state.sites.get(drop, set())
    if keys & shared:
        return ""
    return f"the cited site ({', '.join(sorted(keys))}) is not a website of both records"


def _verify_e2(state, keep: str, drop: str, rest: str) -> str:
    from giye.resolve.evidence import cv_mentions, edition_years

    ledger = state.ledger
    sources = ledger.read("cv_sources") if ledger.path("cv_sources").exists() else []
    tokens = {token.strip(_TOKEN_STRIP) for token in rest.split()} - {""}
    urls = set(_cited_urls(rest))
    cited = [
        row
        for row in sources
        if (row.get("source_id") or "") in tokens
        or (row.get("url") or "") in urls
        or ((row.get("fetch_url") or "") and row.get("fetch_url") in urls)
    ]
    if not cited:
        return "E2 cites no CV source on the ledger (a cv_sources id or its URL)"
    pair = {keep: drop, drop: keep}
    owned = [row for row in cited if row.get("ledger_id") in pair]
    if not owned:
        return "the cited CV source belongs to neither record"
    for row in owned:
        owner = row["ledger_id"]
        other = pair[owner]
        lines = state.cvs.get(owner, [])
        tagged = [line for line in lines if line.get("source_id")]
        if tagged:
            lines = [line for line in tagged if line.get("source_id") == row.get("source_id")]
        for frame_code in sorted(state.frames.get(other, ())):
            years = edition_years(frame_code, state.rows_of.get(other, []))
            if cv_mentions(lines, frame_code, years, state.patterns):
                return ""
    return "the cited CV does not list a roster edition of the other record"


def _verify_e3(state, keep: str, drop: str, rest: str) -> str:
    from giye.resolve.evidence import YEAR_WINDOW, cv_lists_work, norm_title, roster_works

    works = {lid: roster_works(state.rows_of.get(lid, []), state.generic) for lid in (keep, drop)}
    holding: set[str] = {
        title
        for title, year in works[keep]
        if any(title == other and abs(year - when) <= YEAR_WINDOW for other, when in works[drop])
    }
    for this, other in ((keep, drop), (drop, keep)):
        for title, year in works[other]:
            if cv_lists_work(state.cvs.get(this, []), {(title, year)}):
                holding.add(title)
    if not holding:
        return "no work is credited on both rosters, or on one roster and the other's CV"
    cited = norm_title(rest)
    if any(title in cited for title in holding):
        return ""
    return "the cited work is not the work the two records share"


def _verify_e4(state, keep: str, drop: str, rest: str) -> str:
    from giye.resolve.evidence import norm_title, teams

    shared = teams(state.rows_of.get(keep, []), prefix=state.team_prefix) & teams(
        state.rows_of.get(drop, []), prefix=state.team_prefix
    )
    if not shared:
        return "the two rosters credit no team in common"
    cited = norm_title(rest)
    if any(team in cited for team in shared):
        return ""
    return "the cited team is not the team both rosters credit"


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
