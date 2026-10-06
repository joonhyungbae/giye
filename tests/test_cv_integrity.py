# SPDX-License-Identifier: AGPL-3.0-only
"""Kept CV texts are checked against ``cv_sources.content_sha256`` when read. Synthetic demo only."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from giye.collect.snapshot import SnapshotIntegrityError
from giye.config import load
from giye.demo import run_demo
from giye.extract.paths import resolve_stored
from giye.ledger.io import read_csv

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo" / "giye.toml"
CLOCK = datetime(2026, 1, 15, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("cv") / "out"
    result = run_demo(DEMO, out, now=CLOCK)
    return replace(load(DEMO), data=result.output.resolve())


def _sources(config) -> list[dict[str, str]]:
    return [row for row in read_csv(config.ledger / "cv_sources.csv") if row.get("snapshot_path")]


def _tampered(config, tmp_path: Path):
    """A copy of the demo data whose first kept CV text gained an invented line."""
    import shutil

    copy = tmp_path / "data"
    shutil.copytree(config.data, copy)
    tampered = replace(config, data=copy)
    source = _sources(tampered)[0]
    path = resolve_stored(tampered, source["snapshot_path"] + ".txt")
    path.write_text(path.read_text(encoding="utf-8") + "2021 Imaginary Hall\n", encoding="utf-8")
    return tampered, source, path


def test_untouched_cv_texts_read_back(demo) -> None:
    from giye.extract.paths import verified_cv_text

    for source in _sources(demo):
        assert verified_cv_text(demo, source)


def test_every_cv_reader_refuses_an_edited_text(demo, tmp_path: Path) -> None:
    from giye.audit.sample import _cv_texts as audit_texts
    from giye.extract.apply import _CvTexts
    from giye.extract.service import _documents
    from giye.normalize.service import _cv_texts as normalize_texts

    config, source, path = _tampered(demo, tmp_path)
    with pytest.raises(SnapshotIntegrityError, match=path.name):
        _documents(config, [source])
    with pytest.raises(SnapshotIntegrityError, match=path.name):
        _CvTexts(config).get(source)
    with pytest.raises(SnapshotIntegrityError, match=path.name):
        normalize_texts(config, [source])
    with pytest.raises(SnapshotIntegrityError, match=path.name):
        audit_texts(config, [source])


def test_warc_export_carries_each_cv_page_and_its_text(demo, tmp_path: Path) -> None:
    from warcio.archiveiterator import ArchiveIterator

    from giye.export.warc import export_warc

    exported = export_warc(demo, tmp_path / "snapshots.warc.gz")
    responses: dict[str, bytes] = {}
    conversions: dict[str, bytes] = {}
    with exported.warc.open("rb") as handle:
        for record in ArchiveIterator(handle):
            target = record.rec_headers.get_header("WARC-Target-URI") or ""
            if record.rec_type == "response":
                responses[target] = record.content_stream().read()
            elif record.rec_type == "conversion":
                assert record.rec_headers.get_header("WARC-Refers-To")
                conversions[target] = record.content_stream().read()
    sources = _sources(demo)
    assert sources
    for source in sources:
        target = source.get("fetch_url") or source["url"]
        assert target in responses
        text = resolve_stored(demo, source["snapshot_path"] + ".txt").read_text(encoding="utf-8")
        assert conversions[target].decode("utf-8") == text


def test_warc_export_refuses_an_edited_cv_text(demo, tmp_path: Path) -> None:
    from giye.export.warc import export_warc

    config, _source, path = _tampered(demo, tmp_path)
    with pytest.raises(SnapshotIntegrityError, match=path.name):
        export_warc(config, tmp_path / "out.warc.gz")
