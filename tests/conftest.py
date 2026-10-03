# SPDX-License-Identifier: MIT
"""Local fixture server. Tests must not open a socket except to 127.0.0.1."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest


@pytest.fixture(autouse=True)
def _clear_robots_cache():
    """robots.txt outcomes are remembered for the process. Tests start from an empty cache."""
    from giye.collect.robots import clear_cache

    clear_cache()
    yield
    clear_cache()


@contextmanager
def serve(directory: Path):
    """Serve ``directory`` on 127.0.0.1. Yields ``(base_url, server)``; ``server.hits`` records paths."""
    root = directory.resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            rel = unquote(parsed.path).lstrip("/")
            self.server.hits.append((parsed.path, self.headers.get("User-Agent")))  # type: ignore[attr-defined]
            if ".." in Path(rel).parts:
                self.send_error(400)
                return
            file = (root / rel).resolve() if rel else root
            if file != root and root not in file.parents:
                self.send_error(400)
                return
            if not file.is_file() and rel and not Path(rel).suffix:
                alt = (root / (rel + ".html")).resolve()
                if alt.is_file() and root in alt.parents:
                    file = alt
            if not file.is_file():
                self.send_error(404)
                return
            body = file.read_bytes()
            kind = "text/plain; charset=utf-8" if file.name == "robots.txt" else "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.hits = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    try:
        yield f"http://{host}:{port}", httpd
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()
