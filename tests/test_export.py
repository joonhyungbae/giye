# SPDX-License-Identifier: AGPL-3.0-only
"""WARC 1.1 and RO-Crate 1.1 export, checked on the synthetic demo. No network."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from warcio.archiveiterator import ArchiveIterator

from giye import __version__
from giye.collect.snapshot import HEADERS_NOT_KEPT
from giye.config import load
from giye.demo import run_demo
from giye.export.rocrate import NO_DATA_LICENCE_REASON, SOFTWARE_LICENCE, export_ro_crate
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
    assert graph["#giye"]["license"]["@id"] == SOFTWARE_LICENCE
    root = graph["./"]
    assert root["@type"] == "Dataset"
    assert root["name"] == "Synthetic media-art field (demo)"
    assert root["description"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", root["datePublished"])
    # The demo config sets no data licence: the root licence is a statement that none is granted.
    graph = {entity["@id"]: entity for entity in crate["@graph"]}
    assert root["license"] == {"@id": "#no-data-licence"}
    assert graph["#no-data-licence"]["description"] == NO_DATA_LICENCE_REASON
    _assert_ids_inside_crate(meta_path.parent, crate)
    _assert_rocrate_11(meta_path)
    config_entity = next(entity for entity in crate["@graph"] if entity.get("name") == "giye.toml")
    assert config_entity["sha256"] == hashlib.sha256(DEMO.read_bytes()).hexdigest()
    # Files are copied into the crate: the config sits at config/<name>, data under data/.
    assert config_entity["@id"] == "config/giye.toml"
    assert _locate(meta_path.parent, config_entity["@id"]).read_bytes() == DEMO.read_bytes()

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
    assert _locate(meta_path.parent, snapshot).is_file()

    licensed = tmp_path / "licensed.toml"
    licensed.write_text('[publish]\ndata_license = "CC-BY-4.0"\n', encoding="utf-8")
    licensed_meta = export_ro_crate(config, tmp_path / "crate-licensed", config_path=licensed)
    licensed_crate = json.loads(licensed_meta.read_text(encoding="utf-8"))
    licensed_graph = {entity["@id"]: entity for entity in licensed_crate["@graph"]}
    assert licensed_graph["./"]["license"]["@id"] == "https://spdx.org/licenses/CC-BY-4.0.html"
    assert licensed_graph["#giye"]["license"]["@id"] == SOFTWARE_LICENCE
    assert NO_DATA_LICENCE_REASON not in licensed_graph["./"]["description"]
    _assert_ids_inside_crate(licensed_meta.parent, licensed_crate)
    _assert_rocrate_11(licensed_meta)


def test_demo_summary_does_not_depend_on_the_system_date(tmp_path: Path, monkeypatch):
    """A system clock in 2027 would publish one more activity. The demo must not."""
    future = datetime(2027, 6, 1, tzinfo=timezone.utc)

    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return future.replace(tzinfo=None)
            return future.astimezone(tz)

    demo = importlib.import_module("giye.demo")
    for name in demo._CLOCK_MODULES:
        module = importlib.import_module(name)
        if hasattr(module, "datetime"):
            monkeypatch.setattr(module, "datetime", Future)

    result = run_demo(DEMO, tmp_path / "out")
    header = result.summary.splitlines()[0]
    assert header == "run date: 2026-01-15 (fixed; this summary does not follow the system date)"
    assert "people: 21" in result.summary
    assert "roster rows: 23" in result.summary
    assert "activities: 38" in result.summary


def _locate(crate: Path, entity_id: str) -> Path:
    assert not entity_id.startswith("file:"), entity_id
    return (crate / entity_id).resolve()


def _assert_ids_inside_crate(crate: Path, document: dict) -> None:
    """Every hasPart and File id is a relative path to a file inside the crate (no ``file:`` URL)."""
    graph = {entity["@id"]: entity for entity in document["@graph"]}
    root = graph["./"]
    ids = [part["@id"] for part in root.get("hasPart") or []]
    ids.extend(entity["@id"] for entity in document["@graph"] if entity.get("@type") == "File")
    root_resolved = crate.resolve()
    for entity_id in ids:
        if ".." in Path(entity_id).parts:
            raise AssertionError(entity_id)
        if "://" in entity_id:
            parsed = urlparse(entity_id)
            assert parsed.scheme in {"http", "https"} and parsed.netloc, entity_id
            continue
        resolved = (crate / entity_id).resolve()
        resolved.relative_to(root_resolved)
        assert resolved.is_file()


def _assert_rocrate_11(meta_path: Path) -> None:
    """Run rocrate-validator when the package imports; otherwise the property checks above stand.

    ``pip install rocrate-validator`` has no distribution. The package that provides
    this API is ``roc-validator`` (import ``rocrate_validator``).
    """
    try:
        from rocrate_validator import models, services
    except ImportError:
        return
    settings = services.ValidationSettings(
        rocrate_uri=str(meta_path.parent),
        profile_identifier="ro-crate-1.1",
        requirement_severity=models.Severity.REQUIRED,
    )
    result = services.validate(settings)
    issues = [f"{issue.check.identifier}: {issue.message}" for issue in result.get_issues()]
    assert not issues, "\n".join(issues)
