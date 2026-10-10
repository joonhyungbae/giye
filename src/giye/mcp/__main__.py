# SPDX-License-Identifier: AGPL-3.0-only
"""Command line for the career-evidence MCP server.

What: ``python -m giye.mcp serve`` reads one bundle and speaks MCP. Stdio is
the default. ``--http`` is streamable HTTP.

Why: development and tests use stdio. The reference instance will use HTTP.
The SDK stays optional: without the extra, the command says how to install it.

How to run (the venv interpreter):

  .venv/bin/python -m giye.mcp serve --bundle <directory>
"""

from __future__ import annotations

import argparse
import os
import sys

_INSTALL = 'giye mcp: install the extra: pip install "giye[mcp]"'


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="giye.mcp")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="serve one career bundle over MCP")
    serve.add_argument(
        "--bundle",
        default=os.environ.get("GIYE_CAREER_BUNDLE"),
        help="bundle directory (default: GIYE_CAREER_BUNDLE)",
    )
    serve.add_argument(
        "--lang",
        default=os.environ.get("GIYE_LANG", "en"),
        choices=("en", "ko"),
        help="default language (default: GIYE_LANG, else en)",
    )
    serve.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    serve.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    serve.add_argument("--port", type=int, default=6240, help="HTTP bind port")
    serve.add_argument("--stateless", action="store_true", help="stateless streamable HTTP")
    serve.add_argument(
        "--allowed-host",
        action="append",
        default=None,
        dest="allowed_host",
        help="Host header allowed when DNS-rebinding protection is on; repeatable",
    )
    serve.add_argument(
        "--allowed-origin",
        action="append",
        default=None,
        dest="allowed_origin",
        help="Origin header allowed when DNS-rebinding protection is on; repeatable",
    )
    return parser


def _missing_mcp(exc: ImportError) -> bool:
    name = exc.name or ""
    return name == "mcp" or name.startswith("mcp.")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and serve. Returns 2 when the extra or the bundle is missing."""
    args = _parser().parse_args(argv)
    if args.command != "serve":
        print("giye mcp: expected 'serve'", file=sys.stderr)
        return 2
    if not args.bundle:
        print("giye mcp: --bundle is required (or set GIYE_CAREER_BUNDLE)", file=sys.stderr)
        return 2
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        from giye.mcp.bundle import BundleError
        from giye.mcp.server import build_server
    except ImportError as exc:
        if _missing_mcp(exc):
            print(_INSTALL, file=sys.stderr)
            return 2
        raise
    try:
        server = build_server(args.bundle, lang=args.lang)
    except BundleError as exc:
        print(f"giye mcp: {exc}", file=sys.stderr)
        return 2
    if args.http:
        options: dict[str, object] = {
            "transport": "streamable-http",
            "host": args.host,
            "port": args.port,
            "stateless_http": bool(args.stateless),
            "json_response": False,
        }
        # With no allow-list the SDK default applies. Passing an empty list
        # would turn protection on and then refuse every host.
        if args.allowed_host or args.allowed_origin:
            options["transport_security"] = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=list(args.allowed_host or []),
                allowed_origins=list(args.allowed_origin or []),
            )
        server.run(**options)
    else:
        server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
