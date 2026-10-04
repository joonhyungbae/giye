# SPDX-License-Identifier: AGPL-3.0-only
"""One-command demo: collect → extract (replay) → ledger → resolve → normalize → publish → rim.

The demo config is ``examples/demo/giye.toml``. Output goes to a directory the
caller chooses (or a fresh temporary directory), never into the fixture tree.
Reads stay on the offline fixtures. Ledger ids are a fixed sequence so two runs
of the same fixtures publish the same snapshot aside from the clock.

The synthetic CVs treat 2026 as the current year (an upcoming row in 2026 stays
visible; 2027 stays hidden). The golden test freezes the clock at
2026-01-15T00:00:00Z for that reason. ``giye demo`` without a frozen clock uses
the real UTC time. The summary counts merges per rule, T1 blocks, open queue
items, and institution merges V7, V8 and V9. What those numbers refer to is
listed in ``examples/demo/EXPECTED.md``.
"""

from __future__ import annotations

import importlib
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from giye.collect.base import run_configured
from giye.config import Config, load
from giye.explore.rim import build_rim_order, write_rim_order
from giye.extract.service import extract
from giye.ledger.io import read_csv
from giye.ledger.ledger import Ledger
from giye.normalize.service import normalize
from giye.normalize.venue_names import trimmed
from giye.publish import publish, render
from giye.resolve.service import resolve

# Modules that stamp rows with datetime.now. Patched only when the caller freezes the clock.
_CLOCK_MODULES = (
    "giye.collect.base",
    "giye.collect.snapshot",
    "giye.ledger.io",
    "giye.ledger.ledger",
    "giye.extract.apply",
    "giye.extract.pull",
    "giye.resolve.candidates",
    "giye.resolve.teams",
    "giye.normalize.service",
)


@dataclass
class DemoResult:
    output: Path
    site: Path
    people: int
    roster_rows: int
    activities: int
    merges: dict[str, int]
    blocked: dict[str, int]
    queue_items: int
    institution_merges: dict[str, int]
    files: list[Path]
    summary: str


def resolve_demo_config(path: str | Path | None) -> Path:
    """The demo archive. An explicit path wins; otherwise ``examples/demo/giye.toml``."""
    if path:
        return Path(path)
    candidates = [
        Path.cwd() / "examples" / "demo" / "giye.toml",
        Path(__file__).resolve().parents[2] / "examples" / "demo" / "giye.toml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("giye demo: pass --config, or run from the repository (examples/demo/giye.toml)")


def run_demo(
    config: str | Path | None = None,
    output: str | Path | None = None,
    *,
    now: datetime | None = None,
) -> DemoResult:
    """Run the offline demo. ``output`` is the data directory (ledger, processed, site)."""
    path = resolve_demo_config(config)
    out = Path(output) if output is not None else Path(tempfile.mkdtemp(prefix="giye-demo-"))
    out.mkdir(parents=True, exist_ok=True)
    if (out / "ledger" / "artists.csv").is_file():
        raise FileExistsError(f"{out} already holds a ledger; giye demo needs an empty directory")
    cfg = replace(load(path), data=out.resolve())
    if now is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    collected = now.astimezone(timezone.utc).date().isoformat() if now is not None else None
    with _deterministic_ids(), _frozen_clock(now):
        roster = run_configured(cfg, collected_at=collected)
        extract(cfg, replay_only=True)
        ledger_counts(cfg)
        resolve_result = resolve(cfg)
        norm = normalize(cfg)
        published = publish(cfg, now=now)
        rim_path = write_rim_order(build_rim_order(cfg, now=now), cfg.site)
        pages = render(cfg)
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
        institution_merges=_institution_counts(norm.venue_merges, norm.processed),
        files=files,
        summary="",
    )
    result.summary = _summary(result)
    return result


def _summary(result: DemoResult) -> str:
    merges = ", ".join(f"{rule} {count}" for rule, count in result.merges.items()) or "none"
    blocked = ", ".join(f"{rule} {count}" for rule, count in result.blocked.items()) or "none"
    institutions = ", ".join(f"{rule} {count}" for rule, count in result.institution_merges.items()) or "none"
    lines = [
        f"people: {result.people}",
        f"roster rows: {result.roster_rows}",
        f"activities: {result.activities}",
        f"merges: {merges}",
        f"blocked: {blocked}",
        f"queue items: {result.queue_items}",
        f"institution merges: {institutions}",
        "output files:",
        *[f"  {path}" for path in result.files],
    ]
    return "\n".join(lines)


def _institution_counts(merges: dict[str, int], processed: Path) -> dict[str, int]:
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
        counts["V7"] += sum(1 for spelling in spellings if trimmed(spelling))
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

    def seq():
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
        @classmethod
        def now(cls, tz=None):
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
