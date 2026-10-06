# SPDX-License-Identifier: AGPL-3.0-only
"""One-command demo: collect → extract (replay) → ledger → resolve → normalize → publish → rim → ties.

The demo config is ``examples/demo/giye.toml``. Output goes to a directory the
caller chooses (or a fresh temporary directory), never into the fixture tree.
Reads stay on the offline fixtures. Ledger ids are a fixed sequence so two runs
of the same fixtures publish the same snapshot aside from the clock.

The synthetic CVs treat 2026 as the current year (an upcoming row in 2026 stays
visible; 2027 stays hidden). ``giye demo`` therefore always runs at
2026-01-15T00:00:00Z, the same instant the golden test uses, unless the caller
passes ``now``. The summary's first line says that the run date is fixed.
The summary counts merges per rule, T1 blocks, open queue items, institution
merges V7, V8 and V9, and the co-presence ties under each name-rule layer
(``giye.explore.ties``). What those numbers refer to is listed in
``examples/demo/EXPECTED.md``.
"""

from __future__ import annotations

import importlib
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone, tzinfo
from pathlib import Path

from giye.collect.base import run_configured
from giye.config import Config, ConfigError, GiyeError, load
from giye.explore.rim import build_rim_order, write_rim_order
from giye.explore.ties import LAYERS, layers_for_config
from giye.extract.service import extract
from giye.ledger.io import read_csv
from giye.ledger.ledger import Ledger
from giye.normalize.language import LanguageModule, language_for
from giye.normalize.service import normalize
from giye.normalize.venue_names import trimmed
from giye.publish import publish, render
from giye.resolve.service import resolve

# The synthetic field treats 2026 as the current year. ``giye demo`` uses this
# instant unless the caller passes ``now``, so a later system date still prints
# the same counts. The golden test uses the same instant.
DEMO_RUN_DATE = datetime(2026, 1, 15, tzinfo=timezone.utc)

# A person's recorded decisions on X2 pairs, next to the demo config. X2 only
# queues a cross-language pair; this file is the decision the demo's editor
# made on it, so the walkthrough still shows a fold (examples/demo/EXPECTED.md).
DECISIONS_FILE = "decisions.csv"

# Modules that stamp rows with datetime.now. Patched for the demo's run date.
_CLOCK_MODULES = (
    "giye.collect.base",
    "giye.collect.snapshot",
    "giye.ledger.io",
    "giye.ledger.ledger",
    "giye.extract.apply",
    "giye.extract.crosslang",
    "giye.resolve.decide",
    "giye.extract.pull",
    "giye.resolve.candidates",
    "giye.resolve.teams",
    "giye.normalize.service",
)


@dataclass
class DemoResult:
    """Counts and paths from one offline demo run, plus the text ``summary`` prints."""

    output: Path
    site: Path
    people: int
    roster_rows: int
    activities: int
    merges: dict[str, int]
    blocked: dict[str, int]
    queue_items: int
    institution_merges: dict[str, int]
    # giye.explore.ties.layer_report: entities and co-presence ties per name-rule layer.
    copresence: dict
    files: list[Path]
    # UTC calendar date the run used. The summary header prints it.
    run_date: str
    # True when the caller omitted ``now`` and the built-in date was used.
    fixed_run_date: bool
    summary: str


def resolve_demo_config(path: str | Path | None) -> Path:
    """The demo archive. An explicit path wins; otherwise ``examples/demo/giye.toml`` in the working directory.

    The file is not looked up from the installed package. ``giye demo`` outside
    a checkout has to be given ``--config``, and that miss is a configuration
    error rather than a traceback.
    """
    if path:
        found = Path(path)
        if not found.is_file():
            raise ConfigError(f"giye demo: config not found: {found}")
        return found
    candidate = Path.cwd() / "examples" / "demo" / "giye.toml"
    if candidate.is_file():
        return candidate
    raise ConfigError("giye demo: pass --config, or run from the repository (examples/demo/giye.toml)")


def run_demo(
    config: str | Path | None = None,
    output: str | Path | None = None,
    *,
    now: datetime | None = None,
) -> DemoResult:
    """Run the offline demo. ``output`` is the data directory (ledger, processed, site).

    ``now`` defaults to ``DEMO_RUN_DATE`` (2026-01-15). The CLI does not pass a
    clock, so ``giye demo`` prints the same counts in any year.
    """
    path = resolve_demo_config(config)
    out = Path(output) if output is not None else Path(tempfile.mkdtemp(prefix="giye-demo-"))
    out.mkdir(parents=True, exist_ok=True)
    if (out / "ledger" / "artists.csv").is_file():
        # A one-line error, not a traceback: this is a usage mistake.
        raise GiyeError(f"{out} already holds a ledger; giye demo needs an empty directory")
    try:
        loaded = load(path)
    except ConfigError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigError(str(exc)) from exc
    cfg = replace(loaded, data=out.resolve())
    fixed_run_date = now is None
    if now is None:
        now = DEMO_RUN_DATE
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    collected = now.astimezone(timezone.utc).date().isoformat()
    with _deterministic_ids(), _frozen_clock(now):
        roster = run_configured(cfg, collected_at=collected)
        extract(cfg, replay_only=True)
        ledger_counts(cfg)
        resolve_result = resolve(cfg)
        # After resolve: the two CVs of the pair belong to one person only once
        # the X1+E2 merge has joined their owners.
        record_decisions(cfg, path.parent / DECISIONS_FILE)
        norm = normalize(cfg)
        published = publish(cfg, now=now)
        rim_path = write_rim_order(build_rim_order(cfg, now=now), cfg.site)
        pages = render(cfg)
        copresence = layers_for_config(cfg)
    roster_rows = sum(len(rows) for _frame, rows, _path in roster)
    queue_items = sum(1 for row in Ledger.open(cfg).read("review_queue") if row.get("status") == "open")
    merges = dict(sorted(Counter(item.rule for item in resolve_result.merges).items()))
    files = sorted({*published.files, *pages, rim_path})
    result = DemoResult(
        output=out,
        site=published.site,
        people=published.artists,
        roster_rows=roster_rows,
        activities=published.activities,
        merges=merges,
        blocked={"T1": len(resolve_result.blocked_team)},
        queue_items=queue_items,
        institution_merges=_institution_counts(norm.venue_merges, norm.processed, language_for(cfg)),
        copresence=copresence,
        files=files,
        run_date=now.astimezone(timezone.utc).date().isoformat(),
        fixed_run_date=fixed_run_date,
        summary="",
    )
    result.summary = _summary(result)
    return result


def _summary(result: DemoResult) -> str:
    merges = ", ".join(f"{rule} {count}" for rule, count in result.merges.items()) or "none"
    blocked = ", ".join(f"{rule} {count}" for rule, count in result.blocked.items()) or "none"
    institutions = ", ".join(f"{rule} {count}" for rule, count in result.institution_merges.items()) or "none"
    ties = result.copresence["ties"]

    def per_layer(kind: str) -> str:
        """Comma-separated ``name count`` pairs for one co-presence kind."""
        return ", ".join(f"{name} {ties[name][kind]}" for name, _rules in LAYERS)

    if result.fixed_run_date:
        header = f"run date: {result.run_date} (fixed; this summary does not follow the system date)"
    else:
        header = f"run date: {result.run_date}"
    lines = [
        header,
        f"people: {result.people}",
        f"roster rows: {result.roster_rows}",
        f"activities: {result.activities}",
        f"merges: {merges}",
        f"blocked: {blocked}",
        f"queue items: {result.queue_items}",
        f"institution merges: {institutions}",
        f"co-presence ties, CV listing: {per_layer('cv_listing')}",
        f"co-presence ties, roster independent: {per_layer('roster_independent')}",
        "output files:",
        *[f"  {path}" for path in result.files],
    ]
    return "\n".join(lines)


def _institution_counts(merges: dict[str, int], processed: Path, lang: LanguageModule) -> dict[str, int]:
    """Counts the demo summary prints: V7, V8, V9, in that order.

    V8 and V9 are joins the audit records. V7e is recorded the same way and is
    counted under V7. V7a–d are not: they rewrite the entity key before any
    join is logged, so a qualifier (외) or an exhibition title (《…》) would
    otherwise be invisible. A trimmed spelling that shares an entity with
    another spelling is one of those merges.
    """
    counts = {
        "V7": sum(count for rule, count in merges.items() if rule == "V7" or rule.startswith("V7")),
        "V8": merges.get("V8", 0),
        "V9": merges.get("V9", 0),
    }
    for row in read_csv(processed / "venues.csv"):
        spellings = [row.get("name") or ""]
        aliases = row.get("aliases") or ""
        if aliases:
            spellings.extend(part for part in aliases.split("|") if part)
        spellings = [item for item in spellings if item]
        if len(spellings) < 2:
            continue
        counts["V7"] += sum(1 for spelling in spellings if trimmed(spelling, lang))
    return counts


@contextmanager
def _deterministic_ids():
    """Replace ``uuid.uuid4`` with a counter so ledger ids and link ids repeat.

    A normal ``giye collect`` still uses random ids. Only the demo pins them,
    so the golden snapshot can be compared. The first ten hex digits differ for
    each value: ledger ids keep ``uuid4().hex[:10]``.
    """
    real = uuid.uuid4
    counter = {"n": 0}

    def seq() -> uuid.UUID:
        """The next deterministic UUID. The counter is in the first and last groups."""
        counter["n"] += 1
        n = counter["n"]
        return uuid.UUID(f"{n:08x}-4000-8000-8000-{n:012x}")

    uuid.uuid4 = seq  # type: ignore[method-assign]
    try:
        yield
    finally:
        uuid.uuid4 = real  # type: ignore[method-assign]


@contextmanager
def _frozen_clock(moment: datetime | None):
    """Point ``datetime.now`` in the pipeline modules at ``moment``. No-op when it is None."""
    if moment is None:
        yield
        return
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    class Frozen(datetime):
        """``datetime`` whose ``now`` is the moment this demo run is pinned to."""

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            """That moment in ``tz``, or a naive UTC wall time when ``tz`` is omitted."""
            if tz is None:
                return moment.replace(tzinfo=None)
            return moment.astimezone(tz)

    patched: list[tuple[object, datetime]] = []
    for name in _CLOCK_MODULES:
        module = importlib.import_module(name)
        if not hasattr(module, "datetime"):
            continue
        patched.append((module, module.datetime))
        module.datetime = Frozen
    try:
        yield
    finally:
        for module, original in patched:
            module.datetime = original


def record_decisions(config: Config, path: Path) -> int:
    """Record the decisions in ``path`` on the matching open X2 queue items. Returns how many.

    Columns: ``korean_title``, ``latin_title``, ``year``, ``decision`` (``same``
    or ``different``) and ``evidence`` (``H`` with the reason, who judged and
    the date). An item matches when the two rows it names have those titles
    and that year. The decision goes through ``giye queue decide``'s own code
    (``giye.resolve.decide.decide_queue``), the path a person uses.
    """
    from giye.extract.crosslang import REASON
    from giye.resolve.decide import decide_queue

    if not path.is_file():
        return 0
    ledger = Ledger.open(config)
    recorded = 0
    for wanted in read_csv(path):
        titles = {row["activity_id"]: row for row in ledger.read("activities")}
        for item in ledger.read("review_queue"):
            if item.get("reason") != REASON or item.get("status") != "open":
                continue
            parts = dict(
                part.strip().split("=", 1) for part in (item.get("detail") or "").split(";") if "=" in part
            )
            korean, latin = titles.get(parts.get("korean", ""), {}), titles.get(parts.get("latin", ""), {})
            if (
                korean.get("title") == wanted.get("korean_title")
                and latin.get("title") == wanted.get("latin_title")
                and korean.get("year") == wanted.get("year")
            ):
                decide_queue(ledger, item["queue_id"], wanted["decision"], evidence=wanted.get("evidence") or "")
                recorded += 1
    return recorded


def ledger_counts(config: Config) -> dict[str, int]:
    """Row counts for ``giye ledger``. Reads the tables; writes nothing."""
    ledger = Ledger.open(config)
    counts = {}
    for name in (
        "artists",
        "activities",
        "frame_membership",
        "links",
        "cv_sources",
        "review_queue",
        "gy_retired",
        "scope",
    ):
        counts[name] = len(ledger.read(name))
    return counts
