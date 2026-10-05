# SPDX-License-Identifier: AGPL-3.0-only
"""Roster collector base class.

A programme collector is a short subclass. It yields editions; this class fetches
each page (robots.txt, snapshot) and turns the people into roster rows that carry
``source_url`` and ``collected_at``. ``run()`` writes those rows into the ledger
(people with permanent ``gy_id``s, frame membership, one activity per appearance)
and keeps a copy under the configured work directory. Production did the upsert
in ``scripts/collectors/base.py`` (``upsert_people``). A roster row joins an
existing person only under A1–A6. Merging two existing records is E1–E4 and X1.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from giye.collect.fetch import Fetcher, Page, fetcher_from_config
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
    """

    name: str
    name_ko: str = ""
    name_en: str = ""
    website: str = ""
    role: str = ""
    members: str = ""
    aliases: str = ""
    identity: str = ""

    def __post_init__(self) -> None:
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
    """One year (or other edition label) of a programme roster."""

    year: int | str
    people: list[Person]
    source_url: str


class RosterCollector:
    """Base class for programme roster collectors. Set ``frame`` on the subclass."""

    frame: str = ""

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
        self.store = store if store is not None else SnapshotStore(Path(config.raw))  # type: ignore[attr-defined]
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def editions(self) -> Iterator[Edition]:
        """Yield each edition of this frame's public roster. Subclasses implement this."""
        raise NotImplementedError

    def fetch(self, url: str) -> Page:
        """Fetch ``url`` and snapshot a successful body. A snapshot error does not stop the run.

        Production ``SnapshotSession`` swallows archive errors so a disk problem cannot
        abort a monthly collection. The same decision is kept here.
        """
        page = self.fetcher.get(url)
        if page.ok and page.content and len(page.content) <= MAX_BYTES:
            try:
                extra: dict[str, object] = {}
                if page.robots_tls_unverified:
                    extra["robots_tls_unverified"] = True
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
        calendar date. Production used the machine-local date (``date.today()``); UTC
        keeps a row from depending on the operator's timezone. The snapshot manifest
        stores a full UTC timestamp.

        The ledger is the source of truth: a new person gets a ``gy_id``, the frame
        gains a membership, and each appearance is an activity. The CSV is the
        collection report for this run.

        One ``apply_roster`` per edition, not one for the whole programme. The
        code it is given is both the membership and the activity origin, and
        those two strings have to be equal (team expansion and the published
        roster role match on them). The code is ``<frame>-<year>`` when the
        edition label is a year. Rule R1 reads that suffix and does not use the
        activity's year column, so a single call with the bare frame would
        leave every membership undated. The snapshot path stays the bare frame:
        ``fetch`` uses ``self.frame``, not the edition.

        A frame whose ``eligibility.decision`` is not ``included`` or
        ``adjacent`` is not collected. ``run`` prints one line and returns no
        rows, and it does not fetch or write the ledger.
        """
        from giye.collect.frames import is_admitted
        from giye.config import checked_frames

        registry = checked_frames(self.config)  # type: ignore[arg-type]
        declared = registry.by_code(self.frame)
        if declared is not None and not is_admitted(declared.eligibility.decision):
            _skip_notice(self.frame, declared.eligibility.decision)
            return []
        stamp = collected_at or datetime.now(timezone.utc).date().isoformat()
        rows: list[dict[str, str]] = []
        batches: dict[str, list[dict[str, str]]] = {}
        for edition in self.editions():
            code = edition_code(self.frame, edition.year)
            batch = batches.setdefault(code, [])
            for person in edition.people:
                row = {
                    "frame_code": code,
                    "year": str(edition.year),
                    "name": person.name,
                    "name_ko": person.name_ko,
                    "name_en": person.name_en,
                    "source_url": edition.source_url,
                    "collected_at": stamp,
                    "website": person.website,
                    "role": person.role,
                    "members": person.members,
                    "aliases": person.aliases,
                    "identity": person.identity,
                }
                rows.append(row)
                batch.append(row)
        ledger = Ledger.open(self.config)
        for code, batch in batches.items():
            ledger.apply_roster(code, batch, task="collect")
        self.write_csv(rows)
        return rows

    def csv_path(self) -> Path:
        return Path(self.config.work) / "rosters" / f"{self.frame}.csv"  # type: ignore[attr-defined]

    def write_csv(self, rows: list[dict[str, str]]) -> Path:
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
            raise FileNotFoundError(f"collector module not found: {path}")
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


def run_configured(
    config: object, *, collected_at: str | None = None, run_id: str | None = None
) -> list[tuple[str, list[dict[str, str]], Path]]:
    """Run every configured collector. One fetcher and one snapshot store are shared.

    The frames file is read first. A collector whose ``frame`` is not declared
    there is refused before any page is fetched. A declared frame whose
    decision is not ``included`` or ``adjacent`` is skipped before ``run``:
    one notice, no fetch, no ledger rows. ``run`` repeats that check for a
    caller that does not come through here.
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
    fetcher = fetcher_from_config(config)
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
        rows = collector.run(collected_at=collected_at)
        results.append((cls.frame, rows, collector.csv_path()))
    return results


def _skip_notice(code: str, decision: str) -> None:
    """One line. The collector is not run and no page is fetched."""
    print(f"skip {code}: eligibility.decision is {decision}; not collected", file=sys.stderr)
