# SPDX-License-Identifier: AGPL-3.0-only
"""Two offline ``giye run`` passes publish the same site snapshot.

Extract applies each CV before resolve merges the people, so the first pass
must fold those readings itself. The second pass must find the per-CV cache
(replay_miss=0) and must not add activities.
"""

from __future__ import annotations

import importlib
import re
import shutil
import socket
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from giye.cli import main
from giye.demo import _CLOCK_MODULES

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo"
CLOCK = datetime(2026, 1, 15, tzinfo=timezone.utc)
_SITE = re.compile(r"site artists=(\d+) activities=(\d+)")
_CLOCK = _CLOCK_MODULES + (
    "giye.publish.snapshot",
    "giye.explore.rim",
    "giye.extract.service",
)


@contextmanager
def _frozen_clock():
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return CLOCK.replace(tzinfo=None)
            return CLOCK.astimezone(tz)

    patched = []
    for name in _CLOCK:
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


def _site_bytes(root: Path) -> dict[str, bytes]:
    site = root / "data" / "site"
    return {path.relative_to(site).as_posix(): path.read_bytes() for path in sorted(site.rglob("*")) if path.is_file()}


def _activity_rows(root: Path) -> int:
    text = (root / "data" / "ledger" / "activities.csv").read_text(encoding="utf-8")
    return max(text.count("\n") - 1, 0)


def test_two_runs_publish_the_same_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def refuse(*_args, **_kwargs):
        raise AssertionError("giye run tried to use the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    dest = tmp_path / "demo"
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns("data", "__pycache__", "*.pyc"))
    config = str(dest / "giye.toml")
    with _frozen_clock():
        assert main(["run", "--config", config]) == 0
        first_out = capsys.readouterr().out
        first_site = _site_bytes(dest)
        first_rows = _activity_rows(dest)
        assert main(["run", "--config", config]) == 0
        second_out = capsys.readouterr().out
    second_site = _site_bytes(dest)
    assert first_site == second_site
    assert _activity_rows(dest) == first_rows
    assert _SITE.findall(first_out) == _SITE.findall(second_out)
    assert _SITE.findall(second_out) == [("21", "37")]
    assert "replay_miss=0" in second_out
    assert "replay miss " not in second_out
    assert "activities_added=0" in second_out
