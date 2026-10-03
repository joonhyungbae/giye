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


def _extract(args: argparse.Namespace) -> int:
    from giye.config import load
    from giye.extract.service import extract

    result = extract(load(args.config), replay_only=args.replay_only)
    print(
        f"registered={result.registered} pull={result.pull or '-'} "
        f"extracted={len(result.extracted)} replay_miss={len(result.replay_misses)} "
        f"invalid={len(result.invalid)}"
    )
    for ledger_id in result.skipped_unmatched:
        print(f"no artist for source {ledger_id}")
    for item in result.skipped_team:
        print(f"skip team row {item}")
    for ledger_id in result.replay_misses:
        print(f"replay miss {ledger_id}")
    for ledger_id in result.invalid:
        print(f"invalid extraction {ledger_id}")
    applied = result.apply
    if applied is not None:
        print(
            f"activities_added={applied.added} repointed={applied.repointed} "
            f"superseded_files={applied.superseded_files} "
            f"self_reported_superseded={applied.superseded_rows}"
        )
    return 0


def _ledger(args: argparse.Namespace) -> int:
    from giye.config import load
    from giye.demo import ledger_counts

    counts = ledger_counts(load(args.config))
    for name, count in counts.items():
        print(f"{name}\t{count}")
    return 0


def _publish(args: argparse.Namespace) -> int:
    from giye.config import load
    from giye.publish import publish

    publish(load(args.config))
    return 0


def _render(args: argparse.Namespace) -> int:
    from giye.config import load
    from giye.publish import render

    pages = render(load(args.config))
    print(f"pages={len(pages)}")
    return 0


def _demo(args: argparse.Namespace) -> int:
    from giye.demo import run_demo

    result = run_demo(args.config, args.output)
    print(result.summary)
    return 0


def _normalize(args: argparse.Namespace) -> int:
    from giye.config import load
    from giye.normalize.service import normalize

    result = normalize(load(args.config), venue_name_rules=args.venue_name_rules)
    print(result.report, end="")
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
        if stage == "normalize":
            sp.add_argument(
                "--venue-name-rules",
                default=None,
                help="Ablation: comma-separated V7,V8,V9, or 'none'. Default: the config, else all three.",
            )
        if stage == "extract":
            sp.add_argument(
                "--replay-only",
                action="store_true",
                help="Use the replay cache only. Do not call a model, even when an API key is set.",
            )
    nk = sub.add_parser("name-keys", help="print romanized matching keys for names (rule X1)")
    nk.add_argument("names", nargs="+")
    demo = sub.add_parser("demo", help="run the synthetic field offline and print a summary")
    demo.add_argument("--config", default=None, help="defaults to examples/demo/giye.toml")
    demo.add_argument("--output", default=None, help="data directory; default is a new temporary directory")
    render_cmd = sub.add_parser("render", help="write one plain HTML page per person from the site snapshot")
    render_cmd.add_argument("--config", default="giye.toml")
    args = ap.parse_args(argv)
    if args.cmd == "name-keys":
        return _name_keys(args)
    if args.cmd == "collect":
        return _collect(args)
    if args.cmd == "resolve":
        return _resolve(args)
    if args.cmd == "normalize":
        return _normalize(args)
    if args.cmd == "extract":
        return _extract(args)
    if args.cmd == "ledger":
        return _ledger(args)
    if args.cmd == "publish":
        return _publish(args)
    if args.cmd == "render":
        return _render(args)
    if args.cmd == "demo":
        return _demo(args)
    return _not_ported(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
