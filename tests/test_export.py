# SPDX-License-Identifier: MIT
"""WARC 1.1 and RO-Crate 1.1 export, checked on the synthetic demo. No network."""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from warcio.archiveiterator import ArchiveIterator

from giye import __version__
from giye.collect.snapshot import HEADERS_NOT_KEPT
from giye.config import load
from giye.demo import run_demo
from giye.export.rocrate import export_ro_crate
from giye.export.warc import export_warc

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo" / "giye.toml"
CLOCK = datetime(2026, 1, 15, tzinfo=timezone.utc)


def test_demo_exports_warc_wacz_and_ro_crate(tmp_path: Path):
    result = run_demo(DEMO, tmp_path / "out", now=CLOCK)
    config = replace(load(DEMO), data=result.output.resolve())
    exported = export_warc(config, tmp_path / "snapshots.warc.gz", wacz=True)
    meta_path = export_ro_crate(config, tmp_path / "crate", config_path=DEMO)

    assert exported.warc.is_file()
    assert exported.wacz is not None and exported.wacz.is_file()
    responses = []
    metadata = []
    with exported.warc.open("rb") as handle:
        for record in ArchiveIterator(handle):
            assert record.rec_headers.protocol == "WARC/1.1"
            if record.rec_type == "warcinfo":
                assert HEADERS_NOT_KEPT.encode("utf-8") in record.content_stream().read()
            elif record.rec_type == "response":
                body = record.content_stream().read()
                assert record.http_headers is not None
                assert record.http_headers.protocol == "HTTP/1.1"
                responses.append((record.rec_headers.get_header("WARC-Target-URI"), body))
            elif record.rec_type == "metadata":
                payload = json.loads(record.content_stream().read().decode("utf-8"))
                metadata.append(payload)
                assert payload["headers_note"] == HEADERS_NOT_KEPT
                assert payload.get("url", "").startswith("https://example.org/")
                assert record.rec_headers.get_header("WARC-Refers-To")
    assert responses
    assert len(metadata) == len(responses)
    assert any("김하늘" in body.decode("utf-8", errors="replace") for _url, body in responses)
    # The reconstructed header is not a stored original. The note says so.
    assert all("headers_note" in item for item in metadata)

    with zipfile.ZipFile(exported.wacz) as package:
        names = set(package.namelist())
        assert "datapackage.json" in names
        assert "pages/pages.jsonl" in names
        assert "indexes/index.cdxj" in names
        assert any(name.startswith("archive/") and name.endswith(".warc.gz") for name in names)
        datapackage = json.loads(package.read("datapackage.json"))
    assert datapackage["wacz_version"] == "1.1.1"
    assert datapackage["profile"] == "data-package"
    assert HEADERS_NOT_KEPT in datapackage["description"]

    crate = json.loads(meta_path.read_text(encoding="utf-8"))
    assert crate["@context"][0] == "https://w3id.org/ro/crate/1.1/context"
    graph = {entity["@id"]: entity for entity in crate["@graph"]}
    assert graph["ro-crate-metadata.json"]["conformsTo"]["@id"] == "https://w3id.org/ro/crate/1.1"
    assert graph["#giye"]["softwareVersion"] == __version__
    config_entity = next(entity for entity in crate["@graph"] if entity.get("name") == "giye.toml")
    assert config_entity["sha256"] == hashlib.sha256(DEMO.read_bytes()).hexdigest()
    assert (meta_path.parent / config_entity["@id"]).resolve() == DEMO.resolve()

    works = [entity for entity in crate["@graph"] if entity.get("@type") == "CreativeWork" and entity.get("url")]
    rosters = {entity["url"]: entity for entity in works if entity.get("name") == "roster"}
    cvs = {entity["url"]: entity for entity in works if entity.get("name") == "cv"}
    for url in (
        "https://example.org/residency/alumni",
        "https://example.org/workshop/fellows",
        "https://example.org/forum/guests",
    ):
        assert len(rosters[url]["sha256"]) == 64
        assert rosters[url]["dateCreated"]
    # The excluded grant is a frame URL the demo does not fetch, so it has no hash.
    assert "https://example.org/grant/notice" in rosters
    for url in ("https://cv.example.org/haneul-ko", "https://cv.example.org/haneul-en"):
        assert len(cvs[url]["sha256"]) == 64
        assert cvs[url]["dateCreated"]
    # Declared in the demo config, and not pulled: no person row matched it, so no bytes.
    assert "https://cv.example.org/minsoo-mixed" in cvs
    assert "sha256" not in cvs["https://cv.example.org/minsoo-mixed"]

    actions = {entity["name"]: entity for entity in crate["@graph"] if entity.get("@type") == "CreateAction"}
    assert set(actions) == {"collect", "extract", "ledger", "resolve", "normalize", "publish"}
    assert "F1" in actions["collect"]["ruleId"] and "F5" in actions["collect"]["ruleId"]
    assert {"E1", "E2", "E3", "E4", "T1", "X1"} <= set(actions["resolve"]["ruleId"])
    assert "V7" in actions["normalize"]["ruleId"] and "P1" in actions["normalize"]["ruleId"]
    assert actions["collect"]["result"]
    snapshot = actions["collect"]["result"][0]["@id"]
    assert (meta_path.parent / snapshot).is_file()
