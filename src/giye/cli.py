# SPDX-License-Identifier: MIT
"""Command line: ``giye <stage> --config giye.toml`` or ``giye run`` for the whole chain.

Stages that are not yet ported say so and exit with status 2, so scripts fail loudly instead
of silently skipping a step (see docs/ROADMAP.md).
"""

from __future__ import annotations

import argparse
import sys

from giye import __version__
from giye.resolve.names import hangul_name_keys, latin_name_keys

STAGES = ["collect", "extract", "ledger", "resolve", "normalize", "explore", "publish"]


def _not_ported(stage: str) -> int:
    print(f"giye {stage}: not ported yet (see docs/ROADMAP.md)", file=sys.stderr)
    return 2


def _resolve(args: argparse.Namespace) -> int:
    from giye.config import load
    from giye.resolve.service import resolve

    result = resolve(load(args.config), dry_run=args.dry_run)
    for item in result.merges:
        print(f"same person: {item.evidence} → keep {item.kept}, merge {item.dropped}")
    for left, right in result.blocked_team:
        print(f"same_name_blocked_team {left} {right}")
    for item in result.queued:
        print(f"review: {item['detail']} ledger={item['ledger_id']}")
    print(
        f"merged={len(result.merges)} queued={len(result.queued)} "
        f"blocked_team={len(result.blocked_team)} expanded={len(result.expanded)} "
        f"reopened={len(result.reopened)}"
    )
    return 0


def _collect(args: argparse.Namespace) -> int:
    from giye.collect.base import run_configured
    from giye.config import load

    config = load(args.config)
    results = run_configured(config)
    if not results:
        print(
            "giye collect: no collectors configured (set collect.collector_modules in the config)",
            file=sys.stderr,
        )
        return 2
    for frame, rows, path in results:
        print(f"{frame}\t{len(rows)}\t{path}")
    return 0


def _name_keys(args: argparse.Namespace) -> int:
    for name in args.names:
        keys = hangul_name_keys(name) or latin_name_keys(name)
        print(f"{name}\t{' '.join(sorted(keys)) or '-'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="giye", description=__doc__)
    ap.add_argument("--version", action="version", version=f"giye {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for stage in STAGES + ["run"]:
        sp = sub.add_parser(stage, help=f"run the {stage} stage" if stage != "run" else "run every stage in order")
        sp.add_argument("--config", default="giye.toml")
        if stage == "resolve":
            sp.add_argument("--dry-run", action="store_true", help="decide without writing the ledger")
    nk = sub.add_parser("name-keys", help="print romanized matching keys for names (rule X1)")
    nk.add_argument("names", nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "name-keys":
        return _name_keys(args)
    if args.cmd == "collect":
        return _collect(args)
    if args.cmd == "resolve":
        return _resolve(args)
    return _not_ported(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
