# SPDX-License-Identifier: AGPL-3.0-only
"""WACZ validity, reproducible WARC output, legacy lines, and canonical SURT keys."""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from giye.config import Config, load
from giye.demo import run_demo
from giye.export.warc import _surt, export_warc
from tests.test_export import CLOCK, DEMO


@pytest.fixture(scope="module")
def demo_config(tmp_path_factory: pytest.TempPathFactory) -> Config:
    out = tmp_path_factory.mktemp("demo")
    result = run_demo(DEMO, out / "out", now=CLOCK)
    return replace(load(DEMO), data=result.output.resolve())


def test_wacz_resources_carry_hash_and_bytes_and_pages_have_ids(demo_config: Config, tmp_path: Path):
    exported = export_warc(demo_config, tmp_path / "s.warc.gz", wacz=True)
    with zipfile.ZipFile(exported.wacz) as package:
        datapackage = json.loads(package.read("datapackage.json"))
        assert datapackage["created"].endswith("Z")
        for resource in datapackage["resources"]:
            content = package.read(resource["path"])
            assert resource["hash"] == "sha256:" + hashlib.sha256(content).hexdigest()
            assert resource["bytes"] == len(content)
        pages = package.read("pages/pages.jsonl").decode().splitlines()[1:]
        assert pages and all(json.loads(line)["id"] for line in pages)
        digest = json.loads(package.read("datapackage-digest.json"))
        assert digest["hash"] == "sha256:" + hashlib.sha256(package.read("datapackage.json")).hexdigest()


def test_wacz_validates_with_py_wacz(demo_config: Config, tmp_path: Path):
    pytest.importorskip("wacz")
    exported = export_warc(demo_config, tmp_path / "s.warc.gz", wacz=True)
    run = subprocess.run(
        [sys.executable, "-m", "wacz.main", "validate", "-f", str(exported.wacz)], capture_output=True, text=True, check=False
    )
    # py-wacz 0.6 exits 0 either way; the verdict is the last line.
    assert run.returncode == 0, run.stdout + run.stderr
    assert "Validation failed" not in run.stdout, run.stdout
    assert "Validation succeeded" in run.stdout, run.stdout


def test_same_store_exports_the_same_bytes(demo_config: Config, tmp_path: Path):
    one = export_warc(demo_config, tmp_path / "a" / "s.warc.gz", wacz=True)
    two = export_warc(demo_config, tmp_path / "b" / "s.warc.gz", wacz=True)
    assert one.warc.read_bytes() == two.warc.read_bytes()
    assert one.wacz.read_bytes() == two.wacz.read_bytes()


def test_legacy_line_is_200_and_a_date_only_line_is_that_day(tmp_path: Path):
    body = b"<li>Alex Example</li>"
    sha = hashlib.sha256(body).hexdigest()
    data = tmp_path / "data"
    obj = data / "raw" / "OLD" / "snapshots" / "sha256" / sha[:2] / f"{sha}.html"
    obj.parent.mkdir(parents=True)
    obj.write_bytes(body)
    line = {"url": "https://example.org/old", "sha256": sha, "path": str(obj.relative_to(data / "raw")),
            "fetched_at": "2024-03-01"}
    (data / "raw" / "OLD" / "snapshots" / "manifest.jsonl").write_text(json.dumps(line) + "\n")
    config = Config(root=tmp_path, name="Synthetic", data=data)
    with gzip.open(export_warc(config).warc) as handle:
        raw = handle.read()
    assert b"HTTP/1.1 200 OK" in raw
    assert b"WARC-Date: 2024-03-01T00:00:00Z" in raw


def test_cdxj_keys_are_canonical_surts():
    assert _surt("https://www.example.org/Roster/2019?b=2&a=1") == "org,example)/roster/2019?a=1&b=2"
    assert _surt("http://example.org:80/a") == "org,example)/a"
    assert _surt("https://example.org/%7Euser") == "org,example)/~user"
    assert _surt("https://example.org:8443/x") == "org,example:8443)/x"
