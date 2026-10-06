# SPDX-License-Identifier: AGPL-3.0-only
"""Write the snapshot store as a WARC 1.1 file, and optionally a WACZ package.

Each manifest line with a readable body becomes two records:

- a ``response`` record whose payload is the kept body and a reconstructed
  HTTP status line (``status``, ``Content-Type``, ``Content-Length``);
- a ``metadata`` record whose JSON is that manifest line, plus the note that
  original response headers were not kept before manifest version 2.

``WARC-Target-URI`` is ``final_url`` when the line has one, otherwise ``url``.
The manifest line still carries the URL that was asked for. A refused fetch
has no body and is not in the file.

WACZ (``--wacz``) is a zip of that WARC, ``pages/pages.jsonl``, a CDXJ index of
the response records, and ``datapackage.json`` (WACZ 1.1.1). Every resource in
the data package carries its ``hash`` (``sha256:``) and ``bytes``, and every
page line an ``id``, which ``wacz validate`` requires.

The same store exports to the same bytes. Record ids are UUID5 of the record
kind, body hash, target URI, fetch time and position; the warcinfo date and
the data package's ``created`` are the newest ``fetched_at``; zip entries
carry that date too. A line whose ``fetched_at`` is a date only is dated at
midnight UTC that day. The status follows replay (``snapshot._stored_status``):
a legacy line without a status was a kept successful body and is 200.

CDXJ keys are canonical SURTs, as pywb and py-wacz compute them: lower case,
a leading ``www``/``www<N>`` and a default port dropped, query parameters
sorted, and percent-escapes of unreserved characters decoded.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from giye import __version__
from giye.collect.snapshot import (
    HEADERS_NOT_KEPT,
    MANIFEST_VERSION,
    _stored_status,
    servable_rows,
    verified_bytes,
)
from giye.config import Config

_REASONS = {
    200: "OK",
    201: "Created",
    301: "Moved Permanently",
    302: "Found",
    303: "See Other",
    304: "Not Modified",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    410: "Gone",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


@dataclass(frozen=True)
class WarcExport:
    """Paths written by :func:`export_warc`."""

    warc: Path
    wacz: Path | None = None


def export_warc(config: Config, dest: Path | None = None, *, wacz: bool = False) -> WarcExport:
    """Write ``<data>/work/export/snapshots.warc.gz``, and a ``.wacz`` when asked."""
    out = Path(dest) if dest is not None else config.work / "export" / "snapshots.warc.gz"
    if out.suffix == ".gz" or out.name.endswith(".warc"):
        warc_path = out
    else:
        warc_path = out / "snapshots.warc.gz"
    warc_path.parent.mkdir(parents=True, exist_ok=True)
    entries = list(_entries(config.raw))
    created = _newest_date(entries)
    index = _write_warc(warc_path, entries, name=config.name, created=created)
    wacz_path = None
    if wacz:
        wacz_path = _wacz_path(warc_path)
        _write_wacz(wacz_path, warc_path, index, title=config.name, created=created)
    return WarcExport(warc=warc_path, wacz=wacz_path)


def _entries(root: Path) -> list[tuple[dict, Path, bytes]]:
    """Servable snapshot rows with their bytes, in manifest order."""
    found: list[tuple[dict, Path, bytes]] = []
    if not root.is_dir():
        return found
    base = root.resolve()
    for manifest in sorted(root.glob("*/snapshots/manifest.jsonl")):
        parsed: list[dict] = []
        for raw in manifest.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                parsed.append(row)
        # A withdrawn URL is link-only. Its earlier body line is not exported.
        for row in servable_rows(parsed):
            rel = row.get("path")
            if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
                continue
            path = (root / rel).resolve()
            try:
                path.relative_to(base)
            except ValueError:
                continue
            if not path.is_file():
                continue
            found.append((row, path, verified_bytes(path, row.get("sha256"))))
    return found


def _wacz_path(warc_path: Path) -> Path:
    if warc_path.name.endswith(".warc.gz"):
        return warc_path.with_name(warc_path.name[: -len(".warc.gz")] + ".wacz")
    return warc_path.with_suffix(".wacz")


def _newest_date(entries: list[tuple[dict, Path, bytes]]) -> str:
    """Newest WARC date among the lines, or the Unix epoch for an empty or undated store."""
    dates = [_line_date(row.get("fetched_at")) for row, _path, _body in entries]
    dates = [item for item in dates if item]
    return max(dates) if dates else "1970-01-01T00:00:00Z"


def _record_id(*parts: object) -> str:
    """A stable WARC-Record-ID for the same record of the same store."""
    key = "\x1f".join(str(part) for part in parts)
    return f"<urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, key)}>"


def _write_warc(path: Path, entries: list[tuple[dict, Path, bytes]], *, name: str, created: str) -> list[dict]:
    """Write the WARC. Return one index row per response record, with gzip-member offsets."""
    index: list[dict] = []
    with path.open("wb") as handle:
        writer = WARCWriter(handle, gzip=True, warc_version="1.1")
        info = (
            f"software: giye/{__version__}\r\n"
            "format: WARC File Format 1.1\r\n"
            f"description: Snapshot store for {name}\r\n"
            f"headers: {HEADERS_NOT_KEPT}\r\n"
            f"manifest-version: {MANIFEST_VERSION}\r\n"
        ).encode()
        writer.write_record(
            writer.create_warc_record(
                f"urn:giye:warcinfo:{path.name}",
                "warcinfo",
                payload=io.BytesIO(info),
                length=len(info),
                warc_content_type="application/warc-fields",
                warc_headers_dict={"WARC-Record-ID": _record_id("warcinfo", path.name, created), "WARC-Date": created},
            )
        )
        for position, (row, _stored, body) in enumerate(entries):
            target = _target(row)
            status = _status(row)
            ctype = str(row.get("content_type") or "application/octet-stream")
            http_headers = StatusAndHeaders(
                f"{status} {_REASONS.get(status, 'None')}".rstrip(),
                [("Content-Type", ctype), ("Content-Length", str(len(body)))],
                protocol="HTTP/1.1",
            )
            when = _line_date(row.get("fetched_at")) or created
            sha = hashlib.sha256(body).hexdigest()
            headers = {"WARC-Date": when, "WARC-Record-ID": _record_id("response", sha, target, when, position)}
            response = writer.create_warc_record(
                target,
                "response",
                payload=io.BytesIO(body),
                length=len(body),
                warc_headers_dict=headers,
                http_headers=http_headers,
            )
            offset = handle.tell()
            writer.write_record(response)
            length = handle.tell() - offset
            record_id = response.rec_headers.get_header("WARC-Record-ID")
            digest = response.rec_headers.get_header("WARC-Payload-Digest") or ""
            index.append(
                {
                    "url": target,
                    "ts": headers["WARC-Date"],
                    "mime": ctype.split(";", 1)[0].strip() or "application/octet-stream",
                    "status": str(status),
                    "digest": digest,
                    "length": str(length),
                    "offset": str(offset),
                    "filename": path.name,
                }
            )
            meta = dict(row)
            meta["headers_note"] = HEADERS_NOT_KEPT
            blob = json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8")
            writer.write_record(
                writer.create_warc_record(
                    target,
                    "metadata",
                    payload=io.BytesIO(blob),
                    length=len(blob),
                    warc_content_type="application/json",
                    warc_headers_dict={
                        "WARC-Date": headers["WARC-Date"],
                        "WARC-Record-ID": _record_id("metadata", sha, target, when, position),
                        "WARC-Refers-To": record_id,
                        "WARC-Concurrent-To": record_id,
                    },
                )
            )
    return index


def _write_wacz(dest: Path, warc_path: Path, index: list[dict], *, title: str, created: str) -> None:
    """Zip the WARC with pages, a CDXJ index, and a WACZ 1.1.1 data package.

    CDX offsets are byte offsets inside the gzip WARC (one member per record).
    They are not offsets inside the zip.
    """
    warc_name = warc_path.name
    pages = [_pages_header(title)]
    lines: list[tuple[str, str]] = []
    for position, row in enumerate(index):
        page_id = uuid.uuid5(uuid.NAMESPACE_URL, f"page\x1f{row['url']}\x1f{row['ts']}\x1f{position}").hex
        pages.append(json.dumps({"id": page_id, "url": row["url"], "ts": row["ts"]}, ensure_ascii=False) + "\n")
        payload = {
            "url": row["url"],
            "mime": row["mime"],
            "status": row["status"],
            "digest": row["digest"],
            "length": row["length"],
            "offset": row["offset"],
            "filename": warc_name,
        }
        key = f"{_surt(row['url'])} {_cdx_timestamp(row['ts'])}"
        lines.append((key, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
    lines.sort(key=lambda item: item[0])
    pages_bytes = "".join(pages).encode("utf-8")
    index_bytes = ("\n".join(f"{key} {payload}" for key, payload in lines) + ("\n" if lines else "")).encode("utf-8")
    warc_bytes = warc_path.read_bytes()

    def resource(name: str, path: str, fmt: str, content: bytes) -> dict:
        return {
            "name": name,
            "path": path,
            "format": fmt,
            "hash": "sha256:" + hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }

    datapackage = {
        "profile": "data-package",
        "wacz_version": "1.1.1",
        "title": title or "Giye snapshots",
        "software": f"giye/{__version__}",
        "created": created,
        "description": HEADERS_NOT_KEPT,
        "resources": [
            resource("pages.jsonl", "pages/pages.jsonl", "jsonl", pages_bytes),
            resource(warc_name, f"archive/{warc_name}", "warc", warc_bytes),
            resource("index.cdxj", "indexes/index.cdxj", "cdxj", index_bytes),
        ],
    }
    dp_bytes = (json.dumps(datapackage, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    digest = {
        "path": "datapackage.json",
        "hash": "sha256:" + hashlib.sha256(dp_bytes).hexdigest(),
    }
    digest_bytes = (json.dumps(digest, indent=2) + "\n").encode("utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    stamp = _zip_time(created)
    with zipfile.ZipFile(dest, "w") as package:
        for name, content in (
            ("datapackage.json", dp_bytes),
            ("datapackage-digest.json", digest_bytes),
            ("pages/pages.jsonl", pages_bytes),
            ("indexes/index.cdxj", index_bytes),
            (f"archive/{warc_name}", warc_bytes),
        ):
            info = zipfile.ZipInfo(name, date_time=stamp)
            # The WARC is gzip already; WACZ readers expect it stored, not deflated twice.
            info.compress_type = zipfile.ZIP_STORED if name.startswith("archive/") else zipfile.ZIP_DEFLATED
            package.writestr(info, content)


def _zip_time(created: str) -> tuple[int, int, int, int, int, int]:
    """Zip entry time from the data package date. Zip cannot store a year before 1980."""
    when = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if when.year < 1980:
        return (1980, 1, 1, 0, 0, 0)
    return (when.year, when.month, when.day, when.hour, when.minute, when.second)


def _pages_header(title: str) -> str:
    header = {"format": "jsonl", "id": "pages", "title": title or "Giye snapshots", "hasText": False}
    return json.dumps(header, ensure_ascii=False) + "\n"


def _target(row: dict) -> str:
    final = str(row.get("final_url") or "").strip()
    asked = str(row.get("url") or "").strip()
    return final or asked


def _status(row: dict) -> int:
    """The same status replay serves for this line (``giye.collect.snapshot._stored_status``)."""
    return _stored_status(row)


_FULL_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _line_date(value: object) -> str:
    """WARC-Date of a manifest ``fetched_at``; a date only is midnight UTC; empty when there is none."""
    text = str(value or "").strip()
    if _FULL_DATE.fullmatch(text):
        return text
    if _DAY.fullmatch(text):
        return f"{text}T00:00:00Z"
    return ""


def _warc_date(value: object) -> str:
    """Kept for callers: a line's date, or the current time when the line has none."""
    return _line_date(value) or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cdx_timestamp(warc_date: str) -> str:
    digits = "".join(ch for ch in warc_date if ch.isdigit())
    if len(digits) >= 14:
        return digits[:14]
    return digits.ljust(14, "0") or "19700101000000"


_UNRESERVED_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _decode_unreserved(text: str) -> str:
    def one(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else "%" + match.group(1).upper()

    return _UNRESERVED_ESCAPE.sub(one, text)


def _surt(url: str) -> str:
    """Canonical SURT key, as the ``surt`` package (pywb, py-wacz) writes it.

    Host reversed and lower-cased with a leading ``www``/``www<N>`` dropped, a
    default port dropped, path and query lower-cased, query parameters
    sorted, and percent-escapes of unreserved characters decoded.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    host = re.sub(r"^www\d*\.", "", host)
    host_key = ",".join(reversed(host.split("."))) if host else ""
    default = {"http": 80, "https": 443}.get(parsed.scheme.lower())
    if parsed.port and parsed.port != default:
        host_key += f":{parsed.port}"
    path = _decode_unreserved(parsed.path or "/").lower()
    if parsed.query:
        params = sorted(_decode_unreserved(parsed.query).lower().split("&"))
        path += "?" + "&".join(params)
    return f"{host_key}){path}"
