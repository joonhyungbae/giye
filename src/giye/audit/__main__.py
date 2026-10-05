# SPDX-License-Identifier: AGPL-3.0-only
"""``python -m giye.audit`` — sample, score, or serve an accuracy-audit sheet.

    python -m giye.audit sample {cv,people,venues} --config giye.toml --n N --seed S --out sheet.csv
    python -m giye.audit score sheet.csv --kind {cv,people,venues} [--json]
    python -m giye.audit page sheet.csv --kind {cv,people,venues} --out page.html
    python -m giye.audit serve sheet.csv --port 5181

The protocol is ``docs/EVALUATION.md``. ``serve`` listens on 127.0.0.1 and
writes each label into the sheet.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from giye.audit.page import render_page
from giye.audit.sample import load_config, sample_sheet
from giye.audit.score import format_score, score_sheet
from giye.audit.serve import serve_sheet
from giye.audit.sheet import KINDS, read_sheet


def main(argv: list[str] | None = None) -> int:
    """Run ``sample``, ``score``, ``page``, or ``serve``. Returns the process status."""
    parser = argparse.ArgumentParser(prog="giye.audit", description="Accuracy-audit sheets for a Giye ledger.")
    sub = parser.add_subparsers(dest="command", required=True)

    sample = sub.add_parser("sample", help="draw a sheet from a ledger")
    sample.add_argument("kind", choices=KINDS)
    sample.add_argument("--config", required=True, type=Path)
    sample.add_argument("--n", required=True, type=int)
    sample.add_argument("--seed", required=True, type=int)
    sample.add_argument("--out", required=True, type=Path)
    sample.set_defaults(func=_sample)

    score = sub.add_parser("score", help="precision and Wilson intervals")
    score.add_argument("sheet", type=Path)
    score.add_argument("--kind", required=True, choices=KINDS)
    score.add_argument("--json", action="store_true")
    score.set_defaults(func=_score)

    page = sub.add_parser("page", help="write the static judging page")
    page.add_argument("sheet", type=Path)
    page.add_argument("--kind", required=True, choices=KINDS)
    page.add_argument("--out", required=True, type=Path)
    page.set_defaults(func=_page)

    serve = sub.add_parser("serve", help="serve the page and save labels into the sheet")
    serve.add_argument("sheet", type=Path)
    serve.add_argument("--port", type=int, default=5181)
    serve.set_defaults(func=_serve)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"giye audit: {exc}", file=sys.stderr)
        return 2


def _sample(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    rows = sample_sheet(config, args.kind, args.n, args.seed, args.out)
    print(f"wrote {len(rows)} {args.kind} rows to {args.out}")
    return 0


def _score(args: argparse.Namespace) -> int:
    report = score_sheet(args.sheet, args.kind)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        sys.stdout.write(format_score(report))
    return 0


def _page(args: argparse.Namespace) -> int:
    _fields, rows = read_sheet(args.sheet)
    kinds = {row.get("kind") or "" for row in rows}
    kinds.discard("")
    if kinds and kinds != {args.kind}:
        raise ValueError(f"sheet kind is {sorted(kinds)}, not {args.kind}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_page(rows), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    if args.port < 0 or args.port > 65535:
        raise ValueError("--port must be 0..65535")
    # serve_sheet already runs the accept loop on a daemon thread. This thread
    # only has to stay alive until Ctrl-C, then shut that loop down.
    server = serve_sheet(args.sheet, args.port)
    host, port = server.server_address[:2]
    print(f"giye audit serve {args.sheet} on http://{host}:{port}/", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
