# SPDX-License-Identifier: AGPL-3.0-only
"""A kept body gone from disk stops export and replay unless --allow-missing says otherwise."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from giye.collect.base import run_configured
from giye.collect.snapshot import SnapshotMissingError
from giye.config import load
from giye.demo import run_demo
from giye.export.rocrate import export_ro_crate
from giye.export.warc import export_warc
from tests.test_export import CLOCK, DEMO


@pytest.fixture
def config(tmp_path: Path):
    result = run_demo(DEMO, tmp_path / "out", now=CLOCK)
    config = replace(load(DEMO), data=result.output.resolve())
    bodies = [path for path in config.raw.glob("*/snapshots/sha256/*/*") if path.is_file()]
    assert bodies
    bodies[0].unlink()
    return config


def test_warc_export_fails_on_a_missing_body(config, tmp_path: Path):
    with pytest.raises(SnapshotMissingError, match="--allow-missing"):
        export_warc(config, tmp_path / "s.warc.gz")
    result = export_warc(config, tmp_path / "s.warc.gz", allow_missing=True)
    assert len(result.missing) >= 1


def test_ro_crate_export_fails_on_a_missing_body(config, tmp_path: Path):
    with pytest.raises(SnapshotMissingError):
        export_ro_crate(config, tmp_path / "crate", config_path=DEMO)
    assert export_ro_crate(config, tmp_path / "crate", config_path=DEMO, allow_missing=True).is_file()


def test_replay_fails_on_a_missing_body(config, tmp_path: Path):
    # Replay writes the ledger: work on a copy of the demo data.
    copy = tmp_path / "copy"
    shutil.copytree(config.data, copy)
    replay = replace(config, data=copy)
    with pytest.raises(SnapshotMissingError):
        run_configured(replay, from_snapshots=True)
