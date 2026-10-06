# SPDX-License-Identifier: AGPL-3.0-only
"""Write an RO-Crate 1.1 description of one Giye run.

The crate is ``ro-crate-metadata.json``. It records the software version, the
sha256 of the configuration file, each roster and CV URL as a ``CreativeWork``
(sha256 of the kept bytes, and ``dateCreated``), the snapshot files, and one
``CreateAction`` per stage. Stages that have rule ids list them in
``ruleId``. Extract, the ledger, and publish do not have F/E/P/V ids; their
actions say so and carry an empty list (docs/RULES.md).

Every file's ``@id`` is a path relative to the crate directory. A file that
is already inside the crate keeps its place. A file outside it is copied in
first: a file under the data directory to ``data/<path under data>``, the
configuration file to ``config/<name>``, anything else to ``external/<name>``.
Why: an absolute ``file:`` id leaks a local path and does not resolve on
another machine, and RO-Crate 1.1 forbids a relative id that climbs out of
the crate with ``..``. The crate is therefore self-contained: it can be moved
or zipped and every id still names its bytes. A copy whose bytes already
match is not written again.

The software entity's licence is AGPL-3.0-only. The root dataset's ``license``
is the data licence of the run when ``[publish]`` or ``[archive]`` sets
``data_license`` (or ``data_licence``). Otherwise it points to a statement that
no data licence is granted: the software licence does not cover the data.

People hidden by request are left out by default, and CVs are included only
for published people (``giye.export.privacy``): ledger and processed CSVs are
copied without those rows, a text file that names a hidden person is left out,
and captures of private CV and personal-page URLs are not listed. The root
description says which rule applied and what was left out.
``include_hidden`` (``--include-hidden``) includes hidden people and says so.

The root's ``author`` is the contextual entity named by ``[publish]
citation_author``, typed by ``[publish] citation_author_type``
(``Organization``, the default, or ``Person``). The ``publisher`` is always an
``Organization``: the same entity when the author is one, otherwise the archive
(``[archive] name``). The organisation is identified by ``site_url`` when set.
Every ``File`` carries an ``encodingFormat`` (a media type from its suffix,
``application/octet-stream`` when the suffix is unknown). Why: the RO-Crate
validator's RECOMMENDED level asks for these, and they are already in the
configuration or the file name.
"""

from __future__ import annotations

import csv
import hashlib
import json
import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from giye import __version__
from giye.collect.snapshot import SnapshotMissingError, missing_message, servable_rows, verified_bytes
from giye.config import Config
from giye.export.privacy import Privacy, privacy_for
from giye.extract.paths import resolve_stored

# Rule ids the stage applies. Empty means the stage has no F/E/P/V letter id
# (docs/RULES.md). The action description then uses ``_STAGE_NOTE``.
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

# SPDX id for the software. The exported data are not under this licence.
SOFTWARE_LICENCE = "https://spdx.org/licenses/AGPL-3.0-only.html"

# Said in the root description when the run configures no data licence.
# RO-Crate 1.1 requires ``license`` on the root dataset. This run omits it
# anyway: labelling the data with AGPL-3.0-only would claim a licence the
# archive did not set. The sentence is the reason.
NO_DATA_LICENCE_REASON = (
    "No data licence is configured for this run: no reuse licence is granted for these data. "
    "The software is AGPL-3.0-only; that licence does not cover these data."
)

_LICENCE_SECTIONS = ("publish", "archive")
_LICENCE_KEYS = ("data_license", "data_licence")

_STAGE_NOTE = {
    "extract": "CV extraction has no rule id of its own. The schema and the apply decisions are in docs/RULES.md.",
    "ledger": "The ledger invariants are not F/E/P/V ids. They are listed in docs/RULES.md.",
    "publish": "Publish writes the site snapshot. It does not apply a sampling or identity rule.",
}


def export_ro_crate(
    config: Config,
    dest: Path | None = None,
    *,
    config_path: Path | None = None,
    allow_missing: bool = False,
    include_hidden: bool = False,
    leave_out_shared_pages: bool = False,
) -> Path:
    """Write ``<data>/work/export/ro-crate/ro-crate-metadata.json`` and return that path.

    A kept snapshot body missing from disk raises ``SnapshotMissingError``
    unless ``allow_missing``.
    """
    missing = [path for _row, path in _manifest_lines(config) if not path.is_file()]
    if missing and not allow_missing:
        base = config.raw.resolve()
        raise SnapshotMissingError(missing_message([path.relative_to(base).as_posix() for path in missing]))
    crate = Path(dest) if dest is not None else config.work / "export" / "ro-crate"
    if crate.suffix == ".json":
        meta_path = crate
        crate = crate.parent
    else:
        meta_path = crate / "ro-crate-metadata.json"
    crate.mkdir(parents=True, exist_ok=True)
    toml = _config_file(config, config_path)
    privacy = privacy_for(config, include_hidden=include_hidden, leave_out_shared_pages=leave_out_shared_pages)
    graph = _graph(config, crate, toml, privacy)
    document = {
        "@context": [
            "https://w3id.org/ro/crate/1.1/context",
            {
                # Not terms in the RO-Crate 1.1 context. Compacted JSON-LD
                # rejects a key the context does not define.
                "ruleId": "https://giye.org/ns/ruleId",
                "sha256": "https://giye.org/ns/sha256",
                "wasGeneratedBy": "http://schema.org/wasGeneratedBy",
            },
        ],
        "@graph": graph,
    }
    meta_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta_path


def _config_file(config: Config, config_path: Path | None) -> Path | None:
    """The config file to hash into the crate, or None when it is not on disk."""
    if config_path is not None:
        path = Path(config_path)
        return path if path.is_file() else None
    candidate = config.root / "giye.toml"
    return candidate if candidate.is_file() else None


def _graph(config: Config, crate: Path, toml: Path | None, privacy: Privacy | None = None) -> list[dict]:
    """RO-Crate ``@graph`` for one run: descriptor, dataset, software, works, files, actions."""
    privacy = privacy if privacy is not None else Privacy(include_hidden=True)
    works, snapshots = _inputs(config, privacy)
    files: list[dict] = []
    file_ids: dict[str, str] = {}
    data = Path(config.data)
    if toml is not None:
        _crate_file(
            files, file_ids, crate, toml, encoding="application/toml", data=data, config_file=toml, privacy=privacy
        )
    snapshot_ids = _crate_paths(files, file_ids, crate, snapshots, data=data)
    cv_root = config.raw / "cv"
    cv_files = [path for path in _files_under(cv_root) if privacy.cv_file_allowed(path, cv_root)]
    cv_ids = _crate_paths(files, file_ids, crate, cv_files, data=data)
    ledger_ids = _crate_paths(files, file_ids, crate, sorted(config.ledger.glob("*.csv")), data=data, privacy=privacy)
    resolve_names = {"review_queue.csv", "gy_retired.csv"}
    resolve_ids = [ident for ident in ledger_ids if Path(ident).name in resolve_names]
    processed_ids = _crate_paths(files, file_ids, crate, _files_under(config.processed), data=data, privacy=privacy)
    site_ids = _crate_paths(files, file_ids, crate, _files_under(config.site), data=data, privacy=privacy)
    work_entities, roster_ids, cv_work_ids = _work_entities(works)
    actions = _stage_actions(
        {
            "collect": roster_ids,
            "extract": cv_work_ids,
            "ledger": roster_ids,
            "resolve": roster_ids + cv_work_ids,
            "normalize": ledger_ids,
            "publish": processed_ids or ledger_ids,
        },
        {
            "collect": snapshot_ids,
            "extract": cv_ids,
            "ledger": ledger_ids,
            "resolve": resolve_ids or ledger_ids,
            "normalize": processed_ids,
            "publish": site_ids,
        },
    )
    return _assemble_graph(config, toml, files, work_entities, actions, privacy)


def _crate_file(
    files: list[dict],
    file_ids: dict[str, str],
    crate: Path,
    path: Path,
    *,
    encoding: str = "",
    data: Path | None = None,
    config_file: Path | None = None,
    privacy: Privacy | None = None,
) -> str | None:
    """Add a File entity once. Returns its ``@id``, or None when ``path`` is not a file.

    With ``privacy`` the copy is what ``Privacy.content`` returns: a CSV without
    a hidden person's rows, or nothing when a text file names them.
    """
    if not path.is_file():
        return None
    rel = _file_id(crate, path, data=data, config_file=config_file)
    if rel in file_ids:
        return file_ids[rel]
    content = path.read_bytes() if privacy is None else privacy.content(path)
    if content is None:
        return None
    digest = hashlib.sha256(content).hexdigest()
    _place(path, crate / rel, digest, content)
    entity: dict = {
        "@id": rel,
        "@type": "File",
        "name": path.name,
        "contentSize": len(content),
        "sha256": digest,
    }
    entity["encodingFormat"] = encoding or media_type(path)
    files.append(entity)
    file_ids[rel] = rel
    return rel


# Media types for suffixes the pipeline writes, so the crate does not depend
# on the host's mimetypes table for them.
_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".jsonl": "application/jsonl",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".toml": "application/toml",
    ".yml": "application/yaml",
    ".yaml": "application/yaml",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def media_type(path: Path) -> str:
    """The ``encodingFormat`` of a crate file, from its suffix."""
    suffix = path.suffix.lower()
    if suffix in _MEDIA_TYPES:
        return _MEDIA_TYPES[suffix]
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _crate_paths(
    files: list[dict],
    file_ids: dict[str, str],
    crate: Path,
    paths: list[Path],
    *,
    data: Path,
    privacy: Privacy | None = None,
) -> list[str]:
    """File ``@id``s for ``paths``, in that order, skipping a path that is not a file or is left out."""
    ids = []
    for path in paths:
        ident = _crate_file(files, file_ids, crate, path, data=data, privacy=privacy)
        if ident:
            ids.append(ident)
    return ids


def _work_entities(works: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """CreativeWork entities, plus the roster URLs and the CV URLs in work order."""
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
    return work_entities, roster_ids, cv_work_ids


def _stage_actions(objects: dict[str, list[str]], results: dict[str, list[str]]) -> list[dict]:
    """One CreateAction per stage. A stage with rule ids names them; the others use ``_STAGE_NOTE``."""
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
    return actions


def _assemble_graph(
    config: Config,
    toml: Path | None,
    files: list[dict],
    work_entities: list[dict],
    actions: list[dict],
    privacy: Privacy | None = None,
) -> list[dict]:
    """Root dataset, software, licences, then the works, files, and actions already built."""
    parts = [{"@id": entity["@id"]} for entity in files]
    data_licence = _data_licence(config, toml)
    description = f"One Giye run, software giye/{__version__}."
    if privacy is not None:
        description += " " + privacy.note
        if not privacy.include_hidden:
            description += (
                f" Left out: {privacy.left_out_rows} CSV rows, {len(privacy.left_out_files)} files"
                f" and {privacy.left_out_captures} kept captures."
            )
            if privacy.shared_pages_naming_hidden and not privacy.leave_out_shared_pages:
                description += (
                    f" Kept: {privacy.shared_pages_naming_hidden} captures of shared pages"
                    " that name a hidden person."
                )
    licence_entities: list[dict] = [
        {
            "@id": SOFTWARE_LICENCE,
            "@type": "CreativeWork",
            "name": "AGPL-3.0-only",
            "description": "GNU Affero General Public License v3.0 only. Covers the Giye software, not the exported data.",
        }
    ]
    root = {
        "@id": "./",
        "@type": "Dataset",
        "name": config.name,
        "description": description,
        "datePublished": datetime.now(timezone.utc).date().isoformat(),
        "hasPart": parts,
        "wasGeneratedBy": [{"@id": action["@id"]} for action in actions],
    }
    site = (config.site_url or "").rstrip("/")
    person_author = getattr(config, "citation_author_type", "Organization") == "Person"
    archive = {
        "@id": site or "#archive",
        "@type": "Organization",
        "name": config.name if person_author else (config.citation_author or config.name),
    }
    if site:
        archive["url"] = site
    agents = [archive]
    if person_author:
        author = {"@id": "#author", "@type": "Person", "name": config.citation_author or config.name}
        agents.insert(0, author)
        root["author"] = {"@id": author["@id"]}
    else:
        root["author"] = {"@id": archive["@id"]}
    root["publisher"] = {"@id": archive["@id"]}
    if data_licence:
        ref, entity = _licence_ref(data_licence)
        root["license"] = ref
        if entity is not None and entity["@id"] != SOFTWARE_LICENCE:
            licence_entities.append(entity)
    else:
        # RO-Crate 1.1 requires a license on the root. With no data licence the truthful
        # value is a statement that none is granted, not the software's AGPL.
        root["license"] = {"@id": "#no-data-licence"}
        licence_entities.append({
            "@id": "#no-data-licence",
            "@type": "CreativeWork",
            "name": "No data licence granted",
            "description": NO_DATA_LICENCE_REASON,
        })
    software = {
        "@id": "#giye",
        "@type": "SoftwareApplication",
        "name": "giye",
        "softwareVersion": __version__,
        "license": {"@id": SOFTWARE_LICENCE},
    }
    descriptor = {
        "@id": "ro-crate-metadata.json",
        "@type": "CreativeWork",
        "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
        "about": {"@id": "./"},
    }
    return [descriptor, root, *agents, software, *licence_entities, *work_entities, *files, *actions]


def _inputs(config: Config, privacy: Privacy | None = None) -> tuple[list[dict], list[Path]]:
    """Roster and CV works, plus the snapshot files those fetches kept.

    A capture of a private URL (``Privacy.private_url``) is not listed, and
    neither is its work.
    """
    privacy = privacy if privacy is not None else Privacy(include_hidden=True)
    by_url = _manifest_by_url(config)
    snapshots: list[Path] = []
    seen_paths: set[Path] = set()
    for row, path in _manifest_files(config):
        if privacy.private_url(str(row.get("url") or "")) or privacy.private_url(str(row.get("final_url") or "")):
            privacy.left_out_captures += 1
            continue
        if path not in seen_paths:
            # A body that no longer has its manifest hash is not the capture; refuse it.
            body = verified_bytes(path, row.get("sha256"))
            seen_paths.add(path)
            if privacy.shared_capture_left_out(row, body):
                privacy.left_out_captures += 1
                continue
            snapshots.append(path)

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
        if not url.startswith("http") or url in seen or privacy.hidden_url(url):
            continue
        seen.add(url)
        digest, when = _cv_hash(config, row)
        hit = by_url.get(url)
        if hit:
            digest = hit.get("sha256") or digest
            when = hit.get("fetched_at") or when
        if privacy.private_url(url):
            # The text is not in the crate, so its digest is not either.
            digest = ""
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
    """Servable manifest lines whose body file is on disk."""
    return [(row, path) for row, path in _manifest_lines(config) if path.is_file()]


def _manifest_lines(config: Config) -> list[tuple[dict, Path]]:
    """Servable manifest lines with a safe path, and that path (which may be gone)."""
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


def _data_licence(config: Config, toml: Path | None) -> str:
    """Data licence string from the run's config, or "" when none is set.

    ``[publish]`` wins over ``[archive]``. ``data_license`` and ``data_licence``
    are the same key. The software licence is not read from here.
    """
    path = toml if toml is not None else _config_file(config, None)
    if path is None:
        return ""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    if not isinstance(raw, dict):
        return ""
    found = ""
    for section in _LICENCE_SECTIONS:
        block = raw.get(section)
        if not isinstance(block, dict):
            continue
        for key in _LICENCE_KEYS:
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                found = value.strip()
                break
        if section == "publish" and found:
            return found
    return found


def _licence_ref(value: str) -> tuple[dict, dict | None]:
    """A licence property value, and a contextual entity when the value is a URI.

    An SPDX id becomes the SPDX URL. A sentence stays a string: RO-Crate 1.1
    allows ``license`` to be text.
    """
    if value.startswith(("http://", "https://")):
        uri = value
        name = value
    elif " " not in value:
        uri = f"https://spdx.org/licenses/{value}.html"
        name = value
    else:
        return value, None
    entity = {
        "@id": uri,
        "@type": "CreativeWork",
        "name": name,
        "description": f"Data licence configured for this run ({name}).",
    }
    return {"@id": uri}, entity


def _file_id(crate: Path, path: Path, *, data: Path | None = None, config_file: Path | None = None) -> str:
    """Relative path of ``path`` inside the crate (see the module docstring for where outside files go).

    ``Path.relative_to`` fails when ``path`` is outside ``crate``, which is
    also when a relative id would have contained ``..``.
    """
    resolved = path.resolve()
    try:
        return resolved.relative_to(crate.resolve()).as_posix()
    except ValueError:
        pass
    if config_file is not None and resolved == config_file.resolve():
        return f"config/{resolved.name}"
    if data is not None:
        try:
            return f"data/{resolved.relative_to(data.resolve()).as_posix()}"
        except ValueError:
            pass
    return f"external/{resolved.name}"


def _place(source: Path, target: Path, digest: str, content: bytes | None = None) -> None:
    """Copy ``source`` (or ``content``, its filtered bytes) to ``target`` unless the bytes are already there."""
    if source.resolve() == target.resolve():
        return
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        shutil.copyfile(source, target)
    else:
        target.write_bytes(content)
