# SPDX-License-Identifier: AGPL-3.0-only
"""Career-evidence MCP server.

What: an MCP server that reads one career bundle and answers with aggregate
evidence (where a career stands, what similar careers did next, programme
profiles, field trends). It holds no ledger row and no request after the
response.

Why: the archive site records; this server interprets. Aggregates are already
disclosure-controlled in the bundle, so the server only looks them up.

How to run (the venv interpreter, with the ``mcp`` extra):

  .venv/bin/python -m giye.mcp serve --bundle <bundle directory>
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server import MCPServer


def build_server(bundle_dir: str | Path, lang: str = "en") -> MCPServer:
    """Load ``bundle_dir`` and return a server that speaks only that bundle.

    ``lang`` is the default for tools and the prompt when a call omits it.
    The import of the MCP SDK stays inside ``giye.mcp`` so the rest of the
    package imports without the extra.
    """
    from giye.mcp.server import build_server as _build_server

    return _build_server(bundle_dir, lang=lang)


__all__ = ["build_server"]
