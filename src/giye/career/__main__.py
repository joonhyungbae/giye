# SPDX-License-Identifier: AGPL-3.0-only
"""Command line for the career bundle.

What: ``python -m giye.career build`` writes one bundle directory.

Why: phase 1 has no ``giye career`` subcommand yet (the author adds that
later). This module is the entry point the spec names.

How to run (the venv interpreter):

  .venv/bin/python -m giye.career build --config <giye.toml> --out <directory>
"""

from __future__ import annotations

import argparse
import sys

from giye.career.build import CareerBuildError, build
from giye.career.rules import PCT_MIN, K
from giye.config import load


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and build. Returns 0, or 2 when a disclosure check fails."""
    parser = argparse.ArgumentParser(prog="giye.career")
    sub = parser.add_subparsers(dest="command", required=True)
    build_cmd = sub.add_parser("build", help="write a career bundle")
    build_cmd.add_argument("--config", required=True, help="path to giye.toml")
    build_cmd.add_argument("--out", required=True, help="bundle directory, created if needed")
    build_cmd.add_argument("--weights", help="CSV of ledger_id, weight (record-depth IPW)")
    build_cmd.add_argument("--k", type=int, default=K, help="minimum people per cell (default 10)")
    build_cmd.add_argument("--pct-min", type=int, default=PCT_MIN, dest="pct_min", help="minimum people for a share")
    build_cmd.add_argument("--roster-facts", dest="roster_facts", help="open release roster_facts.csv for the D5 check")
    build_cmd.add_argument("--seed", type=int, default=20261010, help="bootstrap seed")
    build_cmd.add_argument("--current-year", type=int, default=None, dest="current_year", help="right-censoring year")
    args = parser.parse_args(argv)
    try:
        build(
            load(args.config),
            args.out,
            weights=args.weights,
            k=args.k,
            pct_min=args.pct_min,
            roster_facts=args.roster_facts,
            seed=args.seed,
            current_year=args.current_year,
        )
    except CareerBuildError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
