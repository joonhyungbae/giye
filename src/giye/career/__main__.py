# SPDX-License-Identifier: AGPL-3.0-only
"""Command line for the career bundle.

What: ``build`` writes one bundle directory. ``holdout`` checks the
next-window base rates at a split year.

Why: phase 1 has no ``giye career`` subcommand yet (the author adds that
later). This module is the entry point the specs name.

How to run (the venv interpreter):

  .venv/bin/python -m giye.career build --config <giye.toml> --out <directory>
  .venv/bin/python -m giye.career holdout --config <giye.toml> --split-year T --out <directory>
"""

from __future__ import annotations

import argparse
import sys

from giye.career.build import CareerBuildError, build
from giye.career.holdout import HoldoutError, holdout
from giye.career.rules import PCT_MIN, K
from giye.config import load


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run ``build`` or ``holdout``. Returns 0, or 2 on a failed check."""
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
    holdout_cmd = sub.add_parser("holdout", help="compare next-window base rates at T with what followed")
    holdout_cmd.add_argument("--config", required=True, help="path to giye.toml")
    holdout_cmd.add_argument("--split-year", required=True, type=int, dest="split_year", help="censoring year T")
    holdout_cmd.add_argument("--out", required=True, help="directory for holdout.json and holdout.md")
    holdout_cmd.add_argument("--weights", help="CSV of ledger_id, weight (record-depth IPW)")
    holdout_cmd.add_argument("--k", type=int, default=K, help="minimum people per cell (default 10)")
    holdout_cmd.add_argument("--pct-min", type=int, default=PCT_MIN, dest="pct_min", help="minimum people for a share")
    holdout_cmd.add_argument("--seed", type=int, default=20261010, help="recorded seed; the check does not resample")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
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
        else:
            holdout(
                load(args.config),
                args.out,
                args.split_year,
                weights=args.weights,
                k=args.k,
                pct_min=args.pct_min,
                seed=args.seed,
            )
    except (CareerBuildError, HoldoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
