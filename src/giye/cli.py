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
    nk = sub.add_parser("name-keys", help="print romanized matching keys for names (rule X1)")
    nk.add_argument("names", nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "name-keys":
        return _name_keys(args)
    return _not_ported(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
