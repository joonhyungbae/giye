# SPDX-License-Identifier: AGPL-3.0-only
"""Roster collector base class.

A programme collector is a short subclass. It yields editions; this class fetches
each page (robots.txt, snapshot) and turns the people into roster rows that carry
``source_url`` and ``collected_at``. ``run()`` writes those rows into the ledger
(people with permanent ``gy_id``s, frame membership, one activity per appearance)
and keeps a copy under the configured work directory. A roster row joins an
existing person only under A1–A6 (docs/RULES.md). Merging two existing records
is E1–E4 and X1.

    class ExampleResidency(RosterCollector):
        frame = "EXAMPLE-RESIDENCY"
        def editions(self):
            url = "https://example.org/residency/alumni"
            page = self.fetch(url)
            for year, names in parse_alumni(page.text):
                people = [Person(name=n) for n in names]
                yield Edition(year=year, people=people, source_url=page.url)
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import inspect
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from giye.collect.fetch import Fetcher, Page, TermsRefused, fetcher_from_config
from giye.collect.robots import RobotsRefused
from giye.collect.snapshot import MAX_BYTES, SnapshotStore
from giye.ledger import Ledger

ROSTER_FIELDS = [
    "frame_code",
    "year",
    "name",
    "name_ko",
    "name_en",
    "source_url",
    "collected_at",
    "website",
    "role",
    "members",
    "aliases",
]
_HANGUL = re.compile(r"[가-힣]")
_YEAR = re.compile(r"\d{4}$")


def _kept_fetch_date(fetched_at: str) -> str:
    """UTC calendar date of a manifest ``fetched_at``, or empty when it is not one.

    Roster ``collected_at`` is a date. The snapshot line stores a full UTC
    timestamp. Re-collection uses that date so the row does not move to today.
    """
    text = (fetched_at or "").strip()
    if len(text) < 10 or text[4] != "-" or text[7] != "-":
        return ""
    year, month, day = text[:4], text[5:7], text[8:10]
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return ""
    return text[:10]


def edition_code(frame: str, year: int | str) -> str:
    """``FRAME-YYYY`` when ``year`` is four digits, otherwise the bare frame.

    A label that is not a year must not grow a fake suffix. Rule R1 reads the
    suffix as the entry year and does not fall back to the activity year, so a
    bare frame code leaves that person on the undated rim (``GEN-UNDATED``).
    """
    text = str(year)
    if _YEAR.fullmatch(text):
        return f"{frame}-{text}"
    return frame


@dataclass
class Person:
    """One name as printed on a roster. Hangul goes to ``name_ko``, Latin to ``name_en``.

    ``website``, ``role``, ``members`` and ``aliases`` are optional. The resolver
    reads them back from the ledger (rules E1, E3, E4, T1). A name that already
    fills one script still takes the other script from ``name``.

    The remaining fields are optional too. They let a collector state what the
    roster page says about this appearance, so the ledger row is not only the
    frame code:

    ``members``
        A team's member names, as ``"A, B"``, ``"A|B"``, or a list. Stored as
        the ``members=`` note (rule T1). A collector whose ``expand_members``
        is true also gets each member's own row (``expand_teams``).
    ``websites``
        Further personal sites besides ``website``. Each becomes a ``links`` row.
    ``source_url`` / ``collected_at``
        The page this person was read from and its date, when the edition's
        people come from several pages. Empty falls back to the edition.
    ``note``
        Note on the appearance's activity row (``reviewer_note``).
    ``person_note``
        ``;``-separated segments for the person's ``reviewer_note`` (for
        example ``team=<name>`` on a member). Written when this row creates
        the person. An existing person's note is curated and is left alone
        unless ``person_note_existing`` is true; then only the segments it
        lacks are appended (compared after whitespace normalisation).
    ``activity``
        Fields of the appearance's activity row: ``title``, ``venue``, ``year``,
        ``activity_type``, ``role``, ``source_url``, ``source_type``,
        ``collected_at``, ``publishable``, ``reviewer_note``. A missing key keeps
        the default (title = edition code, type ``other``, the edition's year,
        the row's source and date, ``role`` and ``note`` above). A
        ``source_url`` that is stated, even empty, is not replaced by the
        roster page. ``False`` means the appearance has no activity row (a
        creator listed without a cohort year): the person gets the membership
        and the ``extra_activities`` only.
    ``extra_activities``
        Further activity rows for this appearance (one per work or session),
        same keys plus an optional ``origin``; ``title`` is required.

    Re-collecting the same pages adds missing rows and fills empty fields,
    nothing else (``Ledger.apply_roster``, docs/FIELD.md).
    """

    name: str
    name_ko: str = ""
    name_en: str = ""
    website: str = ""
    role: str = ""
    members: str | list[str] = ""
    aliases: str = ""
    identity: str = ""
    websites: list[str] = field(default_factory=list)
    source_url: str = ""
    collected_at: str = ""
    note: str = ""
    person_note: str = ""
    person_note_existing: bool = False
    activity: dict | bool | None = None
    extra_activities: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.members, (list, tuple)):
            self.members = "|".join(str(item).strip() for item in self.members if str(item).strip())
        if not self.name_ko and not self.name_en:
            if _HANGUL.search(self.name or ""):
                self.name_ko = self.name
            else:
                self.name_en = self.name
            return
        if self.name and not self.name_ko and _HANGUL.search(self.name):
            self.name_ko = self.name
        if self.name and not self.name_en and not _HANGUL.search(self.name):
            self.name_en = self.name


@dataclass
class Edition:
    """One year (or other edition label) of a programme roster.

    ``source_url`` is the citation on every row. ``fetched_from`` is the page
    that was actually fetched for it when the two differ (a listing page cited
    by its permanent address). ``collected_at`` states the date outright. When
    both are empty a replayed row is dated from the kept page whose URL is
    ``source_url``, as before.
    """

    year: int | str
    people: list[Person]
    source_url: str
    collected_at: str = ""
    fetched_from: str = ""


class RosterCollector:
    """Base class for programme roster collectors. Set ``frame`` on the subclass.

    ``expand_members`` makes ``run`` give each declared team member
    (``Person.members``) their own rows after the roster write, the way
    ``giye resolve`` does (``expand_teams``), limited to the teams' memberships
    in the editions this run wrote. It is off by default so a collector's run
    writes only its own rows.
    """

    frame: str = ""
    expand_members: bool = False

    def __init__(
        self,
        config: object,
        *,
        fetcher: Fetcher | None = None,
        store: SnapshotStore | None = None,
        run_id: str = "",
    ) -> None:
        self.config = config
        self.frame = type(self).frame
        if not self.frame or "/" in self.frame or "\\" in self.frame:
            raise ValueError("RosterCollector.frame must be a frame code without path separators")
        self.fetcher = fetcher if fetcher is not None else fetcher_from_config(config)
        self.from_snapshots = bool(getattr(self.fetcher, "from_snapshots", False))
        # source URL → UTC date of the kept page that ``fetch`` just returned.
        self._kept_dates: dict[str, str] = {}
        if self.from_snapshots:
            self.fetcher.prefer_frame = self.frame
        self.store = store if store is not None else SnapshotStore(Path(config.raw))  # type: ignore[attr-defined]
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Fetches refused in the last ``run`` (robots.txt, a terms block, or a
        # page replay does not have).
        self.refusals: list[Refusal] = []
        # Failures in the last ``run``: a network error, an error page, or a
        # roster row without a source or with a date that is not one. Each is
        # printed as one line, and ``giye collect`` exits non-zero.
        self.failures: list[Refusal] = []
        # Roster rows the last ``run`` did not write because nothing dated them.
        self.undated = 0

    def editions(self) -> Iterator[Edition]:
        """Yield each edition of this frame's public roster. Subclasses implement this."""
        raise NotImplementedError

    def edition_code(self, edition: Edition) -> str:
        """Membership and activity origin for one edition. Override for another scheme.

        The default is ``<frame>-<year>`` for a four-digit year and the bare frame
        otherwise (``edition_code``). A frame code that already ends in a year,
        or an edition labelled by something else, may need its own code. The
        code must not contain a path separator. Rule R1 reads a trailing
        ``-YYYY`` as the entry year.
        """
        return edition_code(self.frame, edition.year)

    def fetch(
        self, url: str, *, data: object = None, method: str | None = None, expect_error: bool = False
    ) -> Page:
        """Fetch ``url`` and snapshot a successful body. A snapshot error does not stop the run.

        A live page whose status is not OK (HTTP 4xx or 5xx) is returned but
        recorded in ``failures`` (verdict ``http_<status>``) and printed, so a
        roster that answered an error page is not a silent zero-row success.
        ``expect_error`` is for a collector that probes (pages until a 404):
        that page is not a failure.

        ``data`` is a request body and makes the request a POST unless
        ``method`` says otherwise (see ``Fetcher.request``). A POST's manifest
        line also records ``method`` and ``body_sha256``; a GET line is as before.

        A disk problem must not abort a collection that can still write the roster
        rows, so a snapshot error is logged and the page is still returned.

        In ``from_snapshots`` mode the body is already in the store. No manifest
        line is appended. ``run`` dates each row from that page's ``fetched_at``.
        """
        if self.from_snapshots:
            self.fetcher.prefer_frame = self.frame
        verb = (method or ("POST" if data is not None else "GET")).upper()
        if verb == "GET" and data is None:
            page = self.fetcher.get(url)
        else:
            page = self.fetcher.request(url, method=verb, data=data)
        if self.from_snapshots:
            if page.reason == "not kept":
                # Replay has no page for this URL: a live run never kept it
                # (refused, failed, or never fetched). That is a refusal of
                # the replay, reported like one, not an empty page.
                self.refusals.append(_not_kept(self.frame, url))
            when = _kept_fetch_date(page.fetched_at)
            if when:
                self._kept_dates[page.url] = when
                if page.requested_url:
                    self._kept_dates[page.requested_url] = when
            return page
        if not page.ok and not expect_error:
            self.failures.append(_failed(self.frame, page.requested_url or url, f"http_{page.status}"))
        if page.ok and page.content and len(page.content) <= MAX_BYTES:
            try:
                extra: dict[str, object] = {}
                if page.robots_tls_unverified:
                    extra["robots_tls_unverified"] = True
                if getattr(page, "method", "GET") != "GET":
                    extra["method"] = page.method
                    extra["body_sha256"] = page.body_sha256
                self.store.keep(
                    self.frame,
                    page.requested_url,
                    page.content,
                    final_url=page.url,
                    status=page.status,
                    content_type=page.content_type,
                    collector=type(self).__name__,
                    run_id=self.run_id,
                    tls_unverified=page.tls_unverified,
                    via="direct",
                    robots=page.robots,
                    **extra,
                )
            except Exception as exc:  # noqa: BLE001 — archiving must not break collection
                print(f"snapshot skipped for {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return page

    def run(self, *, collected_at: str | None = None) -> list[dict[str, str]]:
        """Return roster rows, upsert them into the ledger, and write ``<work>/rosters/<frame>.csv``.

        Each row has ``source_url`` and ``collected_at``. ``collected_at`` is the UTC
        calendar date, so a row does not depend on the operator's timezone. The
        snapshot manifest stores a full UTC timestamp.

        In ``from_snapshots`` mode ``collected_at`` is the UTC date of the kept
        page's ``fetched_at`` (the manifest line whose URL is the person's
        ``source_url``, else the edition's ``fetched_from``, else its
        ``source_url``), not ``collected_at`` and not today's date. A register
        rebuilt from the same pages stays aligned with the ledger those pages
        already produced. A date the collector states (``Person.collected_at``,
        then ``Edition.collected_at``) wins in both modes.

        The ledger is the source of truth: a new person gets a ``gy_id``, the frame
        gains a membership, and each appearance is an activity. The CSV is the
        collection report for this run.

        One ``apply_roster`` per edition, not one for the whole programme. The
        code it is given is both the membership and the activity origin, and
        those two strings have to be equal (team expansion and the published
        roster role match on them). The code is ``edition_code(edition)``:
        ``<frame>-<year>`` by default when the edition label is a year. Rule R1 reads that suffix and does not use the
        activity's year column, so a single call with the bare frame would
        leave every membership undated. The snapshot path stays the bare frame:
        ``fetch`` uses ``self.frame``, not the edition.

        A frame whose ``eligibility.decision`` is not ``included`` or
        ``adjacent`` is not collected. ``run`` prints one line and returns no
        rows, and it does not fetch or write the ledger.

        A fetch that robots.txt or the terms block refuses (``RobotsRefused``,
        ``TermsRefused``) ends this collector's editions: the refusal is
        recorded in ``self.refusals`` and printed as one line, and the editions
        already read are written as usual. Why: a refusal is a rule working,
        not a crash, and one refused page must not discard the rest of the
        roster or stop the other collectors. A collector that can skip just the
        refused edition catches the exception inside ``editions``.

        A network error (``requests.RequestException``: timeout, reset, DNS)
        ends the editions the same way, but it is a failure, not a rule:
        it goes to ``self.failures`` (verdict ``network_error``) and the
        command exits non-zero after the other collectors ran. When a run
        failed and read no row, the previous collection report CSV is left as
        it was instead of being overwritten with a header only.

        A row without an http(s) ``source_url``, or whose stated
        ``collected_at`` is not an ISO date (``YYYY-MM-DD``), is not written:
        every fact row carries both. Each such row is a failure.
        """
        from giye.collect.frames import is_admitted
        from giye.config import checked_frames

        registry = checked_frames(self.config)  # type: ignore[arg-type]
        declared = registry.by_code(self.frame)
        if declared is not None and not is_admitted(declared.eligibility.decision):
            _skip_notice(self.frame, declared.eligibility.decision)
            return []
        # Replay dates each row from the kept page. Today's date, or the
        # argument, would record a new visit and the ledger would drift.
        live_stamp = collected_at or datetime.now(timezone.utc).date().isoformat()
        rows: list[dict[str, str]] = []
        batches: dict[str, list[dict[str, str]]] = {}
        self.refusals = []
        self.failures = []
        self.undated = 0
        try:
            self._read_editions(rows, batches, live_stamp)
        except (RobotsRefused, TermsRefused) as exc:
            self.refusals.append(_refused(self.frame, exc))
        except requests.RequestException as exc:
            self.failures.append(_network_failure(self.frame, exc))
        if self.undated:
            print(
                f"skipped {self.frame}: {self.undated} roster row(s) without a collection date "
                "(no kept page dates them); nothing written for them",
                file=sys.stderr,
            )
        batches = {code: batch for code, batch in batches.items() if batch}
        ledger = Ledger.open(self.config)
        for code, batch in batches.items():
            ledger.apply_roster(code, batch, task="collect")
        if type(self).expand_members and any(row.get("members") for row in rows):
            # Imported here: giye.resolve imports the ledger, which this module also imports.
            from giye.resolve.teams import expand_teams

            # Only this run's editions: a collector must not touch other frames' teams.
            expand_teams(ledger, frames=set(batches))
        if rows or not self.failures:
            self.write_csv(rows)
        else:
            print(
                f"kept {self.csv_path()}: {self.frame} failed and read no row; the previous report stays",
                file=sys.stderr,
            )
        return rows

    def _read_editions(
        self, rows: list[dict[str, str]], batches: dict[str, list[dict[str, str]]], live_stamp: str
    ) -> None:
        """Turn each edition's people into roster rows, appending to ``rows`` and ``batches``."""
        for edition in self.editions():
            code = self.edition_code(edition)
            batch = batches.setdefault(code, [])
            for person in edition.people:
                row = {
                    "frame_code": code,
                    "year": str(edition.year),
                    "name": person.name,
                    "name_ko": person.name_ko,
                    "name_en": person.name_en,
                    "source_url": person.source_url or edition.source_url,
                    "collected_at": self._row_date(edition, person, live_stamp),
                    "website": person.website,
                    "role": person.role,
                    "members": person.members,
                    "aliases": person.aliases,
                    "identity": person.identity,
                }
                # Only present when set, so a minimal collector's rows are unchanged.
                optional = {
                    "websites": list(person.websites),
                    "note": person.note,
                    "reviewer_note": person.person_note,
                    "reviewer_note_existing": person.person_note_existing,
                    "activity": dict(person.activity) if isinstance(person.activity, dict) and person.activity else None,
                    "extra_activities": [dict(item) for item in person.extra_activities],
                }
                row.update({key: value for key, value in optional.items() if value})
                if person.activity is False:
                    row["activity"] = False
                if not str(row["source_url"]).startswith(("http://", "https://")):
                    self.failures.append(
                        _failed(self.frame, str(row["source_url"]), "row_without_source", name=person.name)
                    )
                    continue
                if row["collected_at"] and not _iso_date(row["collected_at"]):
                    self.failures.append(
                        _failed(self.frame, str(row["source_url"]), "row_bad_date", name=person.name)
                    )
                    continue
                if not row["collected_at"]:
                    # A row without a collection date is never written: every
                    # fact row has ``source_url`` and ``collected_at``. In replay
                    # this is a person read from a page that was never kept.
                    self.undated += 1
                    continue
                rows.append(row)
                batch.append(row)

    def _row_date(self, edition: Edition, person: Person, live_stamp: str) -> str:
        """``collected_at`` for one roster row (see ``run``)."""
        stated = (person.collected_at or edition.collected_at or "").strip()
        if stated:
            # A timestamp keeps its date part; anything else is checked by the caller.
            return stated[:10] if _iso_date(stated[:10]) else stated
        if not self.from_snapshots:
            return live_stamp
        for url in (person.source_url, edition.fetched_from, edition.source_url):
            if url and url in self._kept_dates:
                return self._kept_dates[url]
        return ""

    def csv_path(self) -> Path:
        """Collection report for this frame: ``<work>/rosters/<frame>.csv``."""
        return Path(self.config.work) / "rosters" / f"{self.frame}.csv"  # type: ignore[attr-defined]

    def write_csv(self, rows: list[dict[str, str]]) -> Path:
        """Write the collection report and return its path. The ledger write is ``run``."""
        path = self.csv_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ROSTER_FIELDS, extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        return path


def load_collectors(config: object) -> list[type[RosterCollector]]:
    """Import ``collect.collector_modules`` (paths relative to the config file) and return subclasses."""
    classes: list[type[RosterCollector]] = []
    for rel in config.collector_modules:  # type: ignore[attr-defined]
        path = Path(rel)
        if not path.is_absolute():
            path = Path(config.root) / path  # type: ignore[attr-defined]
        path = path.resolve()
        if not path.is_file():
            from giye.config import ConfigError

            raise ConfigError(f"collector module not found: {path}")
        mod_name = "giye_collectors_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load collector module: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        found: list[type[RosterCollector]] = []
        for obj in vars(module).values():
            if not isinstance(obj, type) or not issubclass(obj, RosterCollector) or obj is RosterCollector:
                continue
            if obj.__module__ != module.__name__ or not getattr(obj, "frame", ""):
                continue
            found.append(obj)
        found.sort(key=lambda cls: inspect.getsourcelines(cls)[1])
        classes.extend(found)
    return classes


@dataclass(frozen=True)
class Refusal:
    """One fetch a collector did not send: the frame, the URL, and the verdict."""

    frame: str
    url: str
    verdict: str


def _iso_date(text: str) -> bool:
    """True for a calendar date written ``YYYY-MM-DD``."""
    if len(text) != 10:
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _failed(frame: str, url: str, verdict: str, *, name: str = "") -> Refusal:
    """Record one failure and print it as one line."""
    what = {
        "row_without_source": "roster row without an http(s) source_url was not written",
        "row_bad_date": "roster row whose collected_at is not YYYY-MM-DD was not written",
    }.get(verdict, f"page answered {verdict.replace('_', ' ')}")
    subject = f"{name} " if name else ""
    print(f"failed {frame}: {subject}{url or '(no url)'}: {what}", file=sys.stderr)
    return Refusal(frame=frame, url=url, verdict=verdict)


def _network_failure(frame: str, exc: requests.RequestException) -> Refusal:
    """A network error ends this collector's editions; the ones read before it are written."""
    request = getattr(exc, "request", None)
    url = str(getattr(request, "url", "") or "")
    print(
        f"failed {frame}: network error {type(exc).__name__} on {url or 'a fetch'}; "
        "editions read before it are kept",
        file=sys.stderr,
    )
    return Refusal(frame=frame, url=url, verdict="network_error")


def _not_kept(frame: str, url: str) -> Refusal:
    """Record a replayed fetch that has no kept page and print it as one line."""
    print(f"refused {frame}: {url} was not kept (replay reads kept pages only)", file=sys.stderr)
    return Refusal(frame=frame, url=url, verdict="not_kept")


def _refused(frame: str, exc: RobotsRefused | TermsRefused) -> Refusal:
    """Record a refused fetch and print it as one line (no traceback)."""
    found = Refusal(frame=frame, url=getattr(exc, "url", ""), verdict=getattr(exc, "verdict", ""))
    print(f"refused {frame}: {exc}; editions read before it are kept", file=sys.stderr)
    return found


def run_configured(
    config: object,
    *,
    collected_at: str | None = None,
    run_id: str | None = None,
    from_snapshots: bool = False,
    refusals: list[Refusal] | None = None,
    failures: list[Refusal] | None = None,
    allow_missing: bool = False,
) -> list[tuple[str, list[dict[str, str]], Path]]:
    """Run every configured collector. One fetcher and one snapshot store are shared.

    The frames file is read first. A collector whose ``frame`` is not declared
    there is refused before any page is fetched. A declared frame whose
    decision is not ``included`` or ``adjacent`` is skipped before ``run``:
    one notice, no fetch, no ledger rows. ``run`` repeats that check for a
    caller that does not come through here.

    ``from_snapshots`` reads each page from the snapshot store. No socket is
    opened and no new snapshot line is written. ``collected_at`` on the rows
    is then the kept page's fetch date, not this argument and not today.

    A refused fetch does not stop the run: each collector's refusals are
    appended to ``refusals`` (when given) and the next collector runs.
    A network error or an error page does not stop it either; those go to
    ``failures``.
    """
    from giye.collect.frames import is_admitted
    from giye.config import ConfigError, checked_frames

    registry = checked_frames(config)  # type: ignore[arg-type]
    classes = load_collectors(config)
    missing = [cls.frame for cls in classes if registry.by_code(cls.frame) is None]
    if missing:
        raise ConfigError(
            f"collector frame not declared in {config.frames}: {', '.join(missing)}"  # type: ignore[attr-defined]
        )
    if not classes:
        return []
    fetcher = fetcher_from_config(config, from_snapshots=from_snapshots, allow_missing=allow_missing)
    store = SnapshotStore(Path(config.raw))  # type: ignore[attr-defined]
    stamp = run_id or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []
    for cls in classes:
        frame = registry.by_code(cls.frame)
        # ``frame`` is set: a missing code was refused above.
        decision = frame.eligibility.decision if frame is not None else ""
        if not is_admitted(decision):
            _skip_notice(cls.frame, decision)
            continue
        collector = cls(config, fetcher=fetcher, store=store, run_id=stamp)
        try:
            rows = collector.run(collected_at=collected_at)
        except (RobotsRefused, TermsRefused) as exc:
            # Raised outside the editions loop (a subclass's own run, say).
            collector.refusals.append(_refused(cls.frame, exc))
            rows = []
        except requests.RequestException as exc:
            collector.failures.append(_network_failure(cls.frame, exc))
            rows = []
        if refusals is not None:
            refusals.extend(collector.refusals)
        if failures is not None:
            failures.extend(collector.failures)
        results.append((cls.frame, rows, collector.csv_path()))
    return results


def _skip_notice(code: str, decision: str) -> None:
    """One line. The collector is not run and no page is fetched."""
    print(f"skip {code}: eligibility.decision is {decision}; not collected", file=sys.stderr)
