# SPDX-License-Identifier: AGPL-3.0-only
"""CSV reads and writes, the ledger lock, and a backup before every write.

``write_csv`` is keyword-only so ``path``, ``fields``, and ``rows`` cannot be
swapped. ``Ledger.write`` copies the current file to
``<work>/backups/<file>-<YYYYMMDD>-before-<task>.csv`` before replacing it
(docs/RULES.md, ledger backups). A missing file has nothing to copy.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

_LOCKS: dict[str, TextIO] = {}
_TASK_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def hold_ledger_lock(ledger_dir: Path) -> None:
    """Serialize read-modify-write of one ledger directory across processes.

    Taken on the first read or write and held until this process exits, so a
    run's reads and its later writes see no interleaved writer. Blocks while
    another process holds the lock, and prints who holds it. A second call for
    the same directory in this process does nothing.
    """
    key = str(ledger_dir.resolve())
    if key in _LOCKS:
        return
    import atexit
    import fcntl
    import os
    import sys
    import time

    ledger_dir.mkdir(parents=True, exist_ok=True)
    handle = open(ledger_dir / ".ledger.lock", "a+", encoding="utf-8")  # noqa: SIM115 — held until exit
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        holder = handle.read().strip() or "another process"
        print(f"[ledger] waiting for lock held by {holder}", file=sys.stderr, flush=True)
        start = time.monotonic()
        fcntl.flock(handle, fcntl.LOCK_EX)
        waited = time.monotonic() - start
        print(f"[ledger] lock acquired after {waited:.0f}s", file=sys.stderr, flush=True)
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid {os.getpid()}: {' '.join(sys.argv)[:200]}")
    handle.flush()
    _LOCKS[key] = handle

    def _release(held: TextIO = handle, name: str = key) -> None:
        _LOCKS.pop(name, None)
        held.close()

    atexit.register(_release)


def _lineterminator(path: Path) -> str:
    """The line ending already in ``path``, or ``\\n`` when the file is new.

    A file that already contains a CRLF pair keeps CRLF. Rewriting it as LF
    would make every row a diff. Anything else, including a new file, uses LF.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return "\n"
    with path.open("rb") as handle:
        sample = handle.read(65536)
        if b"\r\n" not in sample and len(sample) == 65536:
            # A huge first line might hide the ending. One more window is enough
            # for a ledger row.
            sample += handle.read(65536)
    if b"\r\n" in sample:
        return "\r\n"
    return "\n"


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read a ledger CSV. A missing file is an empty table, not an error."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = []
        for row in csv.DictReader(handle):
            rows.append({key: (value if value is not None else "") for key, value in row.items()})
        return rows


def write_csv(*, path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    """Write ``rows`` with exactly ``fields`` as the header.

    Keyword-only so the path, the column list, and the rows cannot be passed
    in the wrong order. Unknown keys are dropped. ``None`` is written as an
    empty cell. An existing file keeps its line ending. A new file uses ``\\n``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(fields)
    ending = _lineterminator(path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", lineterminator=ending)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key, "") for key in columns})


def backup_before_write(path: Path, backup_dir: Path, task: str) -> Path | None:
    """Copy ``path`` before it is replaced. Returns the copy, or None if ``path`` is new.

    The name is ``<stem>-<YYYYMMDD>-before-<task>.csv`` under ``backup_dir``
    (the archive's ``data/work/backups``). The date is UTC. A second write on
    the same day with the same task gets ``-2``, ``-3``, … so each write keeps
    the bytes it is about to replace. A second write the same day must not
    overwrite the earlier copy: each write needs the bytes it is about to
    replace (docs/RULES.md, ledger backups).
    """
    if not path.is_file():
        return None
    safe = _TASK_SAFE.sub("-", task.strip()).strip("-")
    if not safe:
        raise ValueError("a ledger write needs a task name so the backup can be identified")
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"{path.stem}-{day}-before-{safe}.csv"
    n = 2
    while dest.exists():
        dest = backup_dir / f"{path.stem}-{day}-before-{safe}-{n}.csv"
        n += 1
    dest.write_bytes(path.read_bytes())
    return dest
