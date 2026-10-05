# SPDX-License-Identifier: AGPL-3.0-only
"""Serve a judging page on 127.0.0.1 and write each POST back into the sheet.

There is no export step. The page at ``/`` is rendered from the sheet on each
GET. ``POST /label`` with ``{"item_id", "label", "note"}`` updates that row
and replaces the file atomically. The listen address is loopback only.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from giye.audit.page import render_page
from giye.audit.sheet import read_sheet, update_label

_MAX_BODY = 64 * 1024


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True


def serve_sheet(path: Path, port: int) -> _Server:
    """Start a daemon thread serving ``path``. The caller closes the returned server."""
    sheet = path.resolve()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] != "/":
                self.send_error(404)
                return
            with lock:
                _fields, rows = read_sheet(sheet)
            body = render_page(rows).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/label":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or "0")
            if length < 0 or length > _MAX_BODY:
                self._json(400, {"ok": False, "error": "body too large"})
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise TypeError
                item_id = payload["item_id"]
                label = payload["label"]
                note = payload.get("note", "")
                if not isinstance(item_id, str) or not isinstance(label, str) or not isinstance(note, str):
                    raise TypeError
            except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
                self._json(400, {"ok": False, "error": "need item_id and label"})
                return
            if len(note) > 4000:
                self._json(400, {"ok": False, "error": "note is too long"})
                return
            try:
                with lock:
                    update_label(sheet, item_id, label, note)
            except KeyError:
                self._json(404, {"ok": False, "error": "unknown item_id"})
                return
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            self._json(200, {"ok": True, "item_id": item_id, "label": label})

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = _Server(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, name="giye-audit-serve", daemon=True)
    thread.start()
    return server
