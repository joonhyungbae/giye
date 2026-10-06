# SPDX-License-Identifier: AGPL-3.0-only
"""CSV reads and writes, the ledger lock, and a backup before every write.

``write_csv`` is keyword-only so ``path``, ``fields``, and ``rows`` cannot be
swapped. ``Ledger.write`` copies the current file to
``<work>/backups/<file>-<YYYYMMDD>-before-<task>.csv.gz`` before replacing it
(docs/RULES.md, ledger backups). A missing file has nothing to copy. The copy
is taken once per file and task in a run (this process), gzip-compressed, and
older copies may be pruned by ``[ledger] keep_backups_days``.
"""

from __future__ import annotations

import csv
import gzip
import os
import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

_LOCKS: dict[str, TextIO] = {}
# Open descriptor of each locked ledger directory (the lock itself), held until exit.
_DIRECTORY_LOCKS: dict[str, int] = {}
_TASK_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
# A run is one process. Key: (backup directory, file stem, task). Value: the copy
# taken before that file's first write for that task in this run.
_RUN_BACKUPS: dict[tuple[str, str, str], Path] = {}
# Backup directories already pruned in this run.
_PRUNED: set[str] = set()
# Names this package writes: <stem>-<YYYYMMDD>-before-<task>[-<n>].csv[.gz].
# Pruning only ever touches files that match it.
BACKUP_NAME = re.compile(r"^(?P<stem>.+?)-(?P<day>\d{8})-before-(?P<task>.+?)(?:-(?P<n>\d+))?\.csv(?:\.gz)?$")


def hold_ledger_lock(ledger_dir: Path) -> None:
    """Serialize read-modify-write of one ledger directory across processes.

    Taken on the first read or write and held until this process exits, so a
    run's reads and its later writes see no interleaved writer. Blocks while
    another process holds the lock, and prints who holds it. A second call for
    the same directory in this process does nothing. The lock is on the
    directory itself; the file ``.ledger.lock`` holds ``pid <n>`` while the
    lock is held and is empty after the process exits.
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
    # The lock is taken on the directory, not on .ledger.lock. Why: restoring the data tree
    # (a git checkout) replaces the lock file with a new one, and a lock on the old file then
    # no longer excludes a process that opens the new one; two writers ran at once that way
    # on 2026-10-06. The directory keeps its inode. The file only says who holds the lock.
    directory = os.open(ledger_dir, os.O_RDONLY)
    handle = open(ledger_dir / ".ledger.lock", "a+", encoding="utf-8")  # noqa: SIM115 — held until exit
    try:
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        holder = handle.read().strip() or "another process"
        print(f"[ledger] waiting for lock held by {holder}", file=sys.stderr, flush=True)
        start = time.monotonic()
        fcntl.flock(directory, fcntl.LOCK_EX)
        waited = time.monotonic() - start
        print(f"[ledger] lock acquired after {waited:.0f}s", file=sys.stderr, flush=True)
    handle.seek(0)
    handle.truncate()
    # Only the pid: the full command line could carry paths or arguments the
    # data directory should not keep, and it outlived the run.
    handle.write(f"pid {os.getpid()}")
    handle.flush()
    _LOCKS[key] = handle
    _DIRECTORY_LOCKS[key] = directory

    def _release(held: TextIO = handle, name: str = key) -> None:
        _LOCKS.pop(name, None)
        # The file stays (unlinking a lock file another process may have open
        # lets two processes lock different files); it is emptied, so a
        # finished run leaves no holder behind.
        try:
            held.seek(0)
            held.truncate()
            held.flush()
        except (OSError, ValueError):
            pass
        held.close()
        locked = _DIRECTORY_LOCKS.pop(name, None)
        if locked is not None:
            os.close(locked)

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
    """Read a ledger CSV. A missing file is an empty table, not an error.

    A ``.gz`` path (a compressed backup) is decompressed on the way in.
    """
    if not path.exists():
        return []
    compressed = path.suffix == ".gz"
    with (
        gzip.open(path, "rt", encoding="utf-8", newline="") if compressed else path.open(encoding="utf-8", newline="")
    ) as handle:
        rows = []
        for row in csv.DictReader(handle):
            rows.append({key: (value if value is not None else "") for key, value in row.items()})
        return rows


def write_csv(*, path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    """Write ``rows`` with exactly ``fields`` as the header.

    Keyword-only so the path, the column list, and the rows cannot be passed
    in the wrong order. Unknown keys are dropped. ``None`` is written as an
    empty cell. An existing file keeps its line ending. A new file uses ``\\n``.

    The write is atomic: the rows go to a temporary file in the same
    directory, which is flushed, fsynced and then moved over ``path`` with
    ``os.replace``. Why: the ledger is the source of truth, and a crash or a
    full disk in the middle of an in-place rewrite would leave a truncated
    table. A reader sees either the old file or the new one. The new file
    keeps the old file's permission bits.
    """
    columns = list(fields)
    ending = _lineterminator(path)

    def fill(handle) -> None:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", lineterminator=ending)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key, "") for key in columns})

    _replace_atomically(path, fill)


def write_text_atomic(path: Path, text: str) -> None:
    """Write ``text`` (UTF-8, line endings as given) to ``path`` the way ``write_csv`` writes a table.

    Why: the replay cache, the extraction files, the site snapshot, the rim
    order, the processed manifest and the evidence status are read back by a
    later stage; a crash or a full disk during a plain ``write_text`` would
    leave a truncated JSON file that the next run either rejects or, worse,
    reads as empty. A reader sees the old file or the new one.
    """
    _replace_atomically(path, lambda handle: handle.write(text))


def _replace_atomically(path: Path, fill) -> None:
    """Write through a temporary file in the same directory, fsync it, and move it over ``path``.

    ``fill(handle)`` writes the content to a text handle opened with UTF-8 and
    ``newline=""``. The new file keeps the old file's permission bits (a new
    file gets the umask default), and the directory is fsynced after the
    rename so the rename itself survives a crash.
    """
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            fill(handle)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copymode(path, temp)
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(temp, 0o666 & ~umask)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    """Make the rename durable. Not every platform can open a directory; that is not an error."""
    try:
        handle = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def start_backup_run() -> None:
    """Begin a new run: the next write of each file and task takes a fresh backup.

    ``giye`` calls this once per command, so commands invoked in one process
    (tests, ``giye.cli.main`` from Python) are separate runs as on the shell.
    """
    _RUN_BACKUPS.clear()
    _PRUNED.clear()


def backup_before_write(path: Path, backup_dir: Path, task: str, *, keep_days: int | None = None) -> Path | None:
    """Copy ``path`` before it is replaced. Returns the copy, or None if ``path`` is new.

    The name is ``<stem>-<YYYYMMDD>-before-<task>.csv.gz`` under ``backup_dir``
    (the archive's ``data/work/backups``). The date is UTC. The first write of
    a file for a task in this run (process) takes the copy; later writes of the
    same file and task in the same run return that copy and do not copy again,
    so every write is still preceded by a dated backup of the bytes the run
    started from. A later run the same day with the same task gets ``-2``,
    ``-3``, … and never overwrites an earlier copy. Why one copy per run and
    not per write: a full collect rewrites the same tables hundreds of times,
    and per-write copies of a large register filled the disk
    (docs/RULES.md, ledger backups). The copy is gzip-compressed for the same
    reason.

    ``keep_days`` (``[ledger] keep_backups_days``) prunes old copies once per
    run, see ``prune_backups``. None keeps every copy.
    """
    if not path.is_file():
        return None
    safe = _TASK_SAFE.sub("-", task.strip()).strip("-")
    if not safe:
        raise ValueError("a ledger write needs a task name so the backup can be identified")
    key = (str(backup_dir.resolve()), path.stem, safe)
    taken = _RUN_BACKUPS.get(key)
    if taken is not None and taken.is_file():
        return taken
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup_dir.mkdir(parents=True, exist_ok=True)
    base = f"{path.stem}-{day}-before-{safe}"
    dest = backup_dir / f"{base}.csv.gz"
    n = 2
    # An uncompressed copy from before compression holds the same slot.
    while dest.exists() or dest.with_suffix("").exists():
        dest = backup_dir / f"{base}-{n}.csv.gz"
        n += 1
    part = dest.with_name(dest.name + ".part")
    # mtime=0 keeps the gzip header free of the clock, so equal bytes compress equally.
    with path.open("rb") as src, open(part, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as out:
        shutil.copyfileobj(src, out, 1 << 20)
    os.replace(part, dest)
    _RUN_BACKUPS[key] = dest
    if keep_days is not None and key[0] not in _PRUNED:
        _PRUNED.add(key[0])
        prune_backups(backup_dir, keep_days)
    return dest


def prune_backups(backup_dir: Path, keep_days: int, *, today: date | None = None) -> list[Path]:
    """Delete package backups dated more than ``keep_days`` days before ``today``.

    Only names matching ``BACKUP_NAME`` are touched, so files a person or
    another tool put in the directory stay. The date is the one in the name
    (UTC day of the write), not the file's mtime, so a copied or restored
    directory prunes the same way. Every copy on the newest day of each file
    is kept whatever its age, so a ledger file always has at least one backup.
    Returns the deleted paths.
    """
    if keep_days < 1:
        raise ValueError("keep_backups_days must be at least 1")
    if not backup_dir.is_dir():
        return []
    today = today or datetime.now(timezone.utc).date()
    cutoff = (today - timedelta(days=keep_days)).strftime("%Y%m%d")
    by_stem: dict[str, list[tuple[str, Path]]] = {}
    for entry in backup_dir.iterdir():
        match = BACKUP_NAME.match(entry.name)
        if match and entry.is_file():
            by_stem.setdefault(match.group("stem"), []).append((match.group("day"), entry))
    removed: list[Path] = []
    for copies in by_stem.values():
        newest = max(day for day, _ in copies)
        for day, entry in copies:
            if day < cutoff and day != newest:
                entry.unlink()
                removed.append(entry)
    return sorted(removed)
