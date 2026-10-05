# SPDX-License-Identifier: AGPL-3.0-only
"""Write an RO-Crate 1.1 description of one Giye run.

The crate is ``ro-crate-metadata.json``. It records the software version, the
sha256 of the configuration file, each roster and CV URL as a ``CreativeWork``
(sha256 of the kept bytes, and ``dateCreated``), the snapshot files, and one
``CreateAction`` per stage. Stages that have production rule ids list them in
``ruleId``. Extract, the ledger, and publish do not have F/E/P/V ids; their
actions say so and carry an empty list.

Paths in the crate are relative to the metadata file. Snapshot bytes stay where
the run wrote them; the crate points at them.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

import yaml

from giye import __version__
from giye.collect.snapshot import servable_rows
from giye.config import Config
from giye.extract.paths import resolve_stored

# Rule ids the stage applies. Empty means the stage has no production letter id.
STAGE_RULES: dict[str, tuple[str, ...]] = {
    "collect": ("F1", "F2", "F3", "F4", "F5", "A1", "A2", "A3", "A4", "A5", "A6"),
    "extract": (),
    "ledger": (),
    "resolve": ("E1", "E2", "E3", "E4", "T1", "X1"),
    "normalize": (
        "P1",
        "P2",
        "P3",
        "P4",
        "P5",
        "P6",
        "B1",
        "L1",
        "A1",
        "M1",
        "V1",
        "V2",
        "V3",
        "V4",
        "V5",
        "V7",
        "V8",
        "V9",
        "G1",
    ),
    "publish": (),
}

_STAGE_NOTE = {
    "extract": "CV extraction has no production F/E/P/V id. The schema and the apply decisions are in docs/RULES.md.",
    "ledger": "The ledger invariants are not F/E/P/V ids. They are listed in docs/RULES.md.",
    "publish": "Publish writes the site snapshot. It does not apply a sampling or identity rule.",
}


def export_ro_crate(config: Config, dest: Path | None = None, *, config_path: Path | None = None) -> Path:
    """Write ``<data>/work/export/ro-crate/ro-crate-metadata.json`` and return that path."""
    crate = Path(dest) if dest is not None else config.work / "export" / "ro-crate"
    if crate.suffix == ".json":
        meta_path = crate
        crate = crate.parent
    else:
        meta_path = crate / "ro-crate-metadata.json"
    crate.mkdir(parents=True, exist_ok=True)
    toml = _config_file(config, config_path)
    graph = _graph(config, crate, toml)
    document = {
        "@context": [
            "https://w3id.org/ro/crate/1.1/context",
            {"ruleId": "https://giye.org/ns/ruleId"},
        ],
        "@graph": graph,
    }
    meta_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta_path


def _config_file(config: Config, config_path: Path | None) -> Path | None:
    if config_path is not None:
        path = Path(config_path)
        return path if path.is_file() else None
    candidate = config.root / "giye.toml"
    return candidate if candidate.is_file() else None


def _graph(config: Config, crate: Path, toml: Path | None) -> list[dict]:
    works, snapshots = _inputs(config)
    files: list[dict] = []
    file_ids: dict[str, str] = {}

    def add_file(path: Path, *, encoding: str = "") -> str | None:
        if not path.is_file():
            return None
        rel = _rel(crate, path)
        if rel in file_ids:
            return file_ids[rel]
        entity: dict = {
            "@id": rel,
            "@type": "File",
            "name": path.name,
            "contentSize": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if encoding:
            entity["encodingFormat"] = encoding
        files.append(entity)
        file_ids[rel] = rel
        return rel

    if toml is not None:
        add_file(toml, encoding="application/toml")
    snapshot_ids = []
    for path in snapshots:
        ident = add_file(path)
        if ident:
            snapshot_ids.append(ident)
    cv_ids = [ident for path in _files_under(config.raw / "cv") if (ident := add_file(path))]
    ledger_ids = [ident for path in sorted((config.ledger).glob("*.csv")) if (ident := add_file(path))]
    resolve_names = {"review_queue.csv", "gy_retired.csv"}
    resolve_ids = [ident for ident in ledger_ids if Path(ident).name in resolve_names]
    processed_ids = [ident for path in _files_under(config.processed) if (ident := add_file(path))]
    site_ids = [ident for path in _files_under(config.site) if (ident := add_file(path))]

    work_entities = []
    roster_ids = []
    cv_work_ids = []
    for work in works:
        entity = {
            "@id": work["url"],
            "@type": "CreativeWork",
            "name": work["role"],
            "url": work["url"],
        }
        if work.get("sha256"):
            entity["sha256"] = work["sha256"]
        if work.get("dateCreated"):
            entity["dateCreated"] = work["dateCreated"]
        work_entities.append(entity)
        if work["role"] == "cv":
            cv_work_ids.append(work["url"])
        else:
            roster_ids.append(work["url"])

    results = {
        "collect": snapshot_ids,
        "extract": cv_ids,
        "ledger": ledger_ids,
        "resolve": resolve_ids or ledger_ids,
        "normalize": processed_ids,
        "publish": site_ids,
    }
    objects = {
        "collect": roster_ids,
        "extract": cv_work_ids,
        "ledger": roster_ids,
        "resolve": roster_ids + cv_work_ids,
        "normalize": ledger_ids,
        "publish": processed_ids or ledger_ids,
    }
    actions = []
    for stage, rules in STAGE_RULES.items():
        action = {
            "@id": f"#action-{stage}",
            "@type": "CreateAction",
            "name": stage,
            "instrument": {"@id": "#giye"},
            "ruleId": list(rules),
            "object": [{"@id": item} for item in objects[stage]],
            "result": [{"@id": item} for item in results[stage]],
        }
        if rules:
            action["description"] = "Rules " + ", ".join(rules) + "."
        elif stage in _STAGE_NOTE:
            action["description"] = _STAGE_NOTE[stage]
        actions.append(action)

    parts = [{"@id": entity["@id"]} for entity in files]
    root = {
        "@id": "./",
        "@type": "Dataset",
        "name": config.name,
        "description": f"One Giye run, software giye/{__version__}.",
        "hasPart": parts,
        "wasGeneratedBy": [{"@id": action["@id"]} for action in actions],
    }
    software = {
        "@id": "#giye",
        "@type": "SoftwareApplication",
        "name": "giye",
        "softwareVersion": __version__,
    }
    descriptor = {
        "@id": "ro-crate-metadata.json",
        "@type": "CreativeWork",
        "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
        "about": {"@id": "./"},
    }
    return [descriptor, root, software, *work_entities, *files, *actions]


def _inputs(config: Config) -> tuple[list[dict], list[Path]]:
    """Roster and CV works, plus the snapshot files those fetches kept."""
    by_url = _manifest_by_url(config)
    snapshots: list[Path] = []
    seen_paths: set[Path] = set()
    for row, path in _manifest_files(config):
        if path not in seen_paths:
            seen_paths.add(path)
            snapshots.append(path)
        _ = row

    roster_urls: dict[str, str] = {}
    for url in _roster_urls(config):
        roster_urls.setdefault(url, "")
    for path in sorted(config.work.glob("rosters/*.csv")):
        for row in _read_csv(path):
            url = (row.get("source_url") or "").strip()
            if url.startswith("http"):
                roster_urls.setdefault(url, row.get("collected_at") or "")

    cv_rows = {row.get("url", "").strip(): row for row in _read_csv(config.ledger / "cv_sources.csv")}
    for source in config.extract_sources:
        cv_rows.setdefault(source.url, {"url": source.url})

    works: list[dict] = []
    seen: set[str] = set()
    for url, collected_at in sorted(roster_urls.items()):
        if not url or url in seen:
            continue
        seen.add(url)
        hit = by_url.get(url)
        works.append(
            {
                "url": url,
                "role": "roster",
                "sha256": (hit or {}).get("sha256") or "",
                "dateCreated": (hit or {}).get("fetched_at") or collected_at,
            }
        )
    for url, row in sorted(cv_rows.items()):
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        digest, when = _cv_hash(config, row)
        hit = by_url.get(url)
        if hit:
            digest = hit.get("sha256") or digest
            when = hit.get("fetched_at") or when
        works.append({"url": url, "role": "cv", "sha256": digest, "dateCreated": when})
    return works, snapshots


def _cv_hash(config: Config, row: dict) -> tuple[str, str]:
    when = row.get("last_changed_at") or row.get("last_pulled_at") or ""
    stored = (row.get("snapshot_path") or "").strip()
    if stored:
        stem = resolve_stored(config, stored)
        originals = []
        if stem.parent.is_dir():
            originals = [
                path
                for path in stem.parent.glob(stem.name + ".*")
                if path.is_file() and path.suffix.lower() != ".txt"
            ]
        chosen = originals[0] if originals else stem.with_suffix(".txt")
        if chosen.is_file():
            return hashlib.sha256(chosen.read_bytes()).hexdigest(), when
    return (row.get("content_sha256") or ""), when


def _roster_urls(config: Config) -> list[str]:
    found: list[str] = []
    if config.frames.is_file():
        try:
            document = yaml.safe_load(config.frames.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            document = {}
        for frame in document.get("frames") or []:
            if isinstance(frame, dict):
                url = str(frame.get("source_url") or "")
                if url.startswith("http"):
                    found.append(url)
    return found


def _manifest_files(config: Config) -> list[tuple[dict, Path]]:
    rows: list[tuple[dict, Path]] = []
    raw = config.raw
    if not raw.is_dir():
        return rows
    base = raw.resolve()
    for manifest in sorted(raw.glob("*/snapshots/manifest.jsonl")):
        parsed: list[dict] = []
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                parsed.append(row)
        for row in servable_rows(parsed):
            rel = row.get("path")
            if not isinstance(rel, str) or not rel or ".." in Path(rel).parts:
                continue
            path = (raw / rel).resolve()
            try:
                path.relative_to(base)
            except ValueError:
                continue
            if path.is_file():
                rows.append((row, path))
    return rows


def _manifest_by_url(config: Config) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row, _path in _manifest_files(config):
        for key in (row.get("url"), row.get("final_url")):
            if isinstance(key, str) and key and key not in found:
                found[key] = row
    return found


def _files_under(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file())


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]


def _rel(crate: Path, path: Path) -> str:
    return Path(os.path.relpath(path.resolve(), crate.resolve())).as_posix()
