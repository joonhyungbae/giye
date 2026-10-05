# SPDX-License-Identifier: AGPL-3.0-only
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

    result = resolve(load(args.config), dry_run=getattr(args, "dry_run", False))
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
    from dataclasses import replace

    from giye.config import default_chunk_chars, load
    from giye.extract.service import extract

    config = load(args.config)
    overrides = {}
    provider = getattr(args, "provider", None)
    base_url = getattr(args, "base_url", None)
    model = getattr(args, "model", None)
    if provider is not None:
        overrides["extract_provider"] = provider
        # An omitted chunk_chars follows the provider actually used.
        if not config.extract_chunk_chars_explicit:
            overrides["extract_chunk_chars"] = default_chunk_chars(provider)
    if base_url is not None:
        overrides["extract_base_url"] = base_url.rstrip("/")
    if model is not None:
        overrides["extract_model"] = model
    if overrides:
        config = replace(config, **overrides)
    result = extract(config, replay_only=getattr(args, "replay_only", False))
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

    result = normalize(load(args.config), venue_name_rules=getattr(args, "venue_name_rules", None))
    print(result.report, end="")
    return 0


def _export(args: argparse.Namespace) -> int:
    from pathlib import Path

    from giye.config import load
    from giye.export.rocrate import export_ro_crate
    from giye.export.warc import export_warc

    config = load(args.config)
    output = Path(args.output) if args.output else None
    if args.export_cmd == "warc":
        result = export_warc(config, output, wacz=args.wacz)
        print(result.warc)
        if result.wacz is not None:
            print(result.wacz)
        return 0
    if args.export_cmd == "ro-crate":
        print(export_ro_crate(config, output, config_path=Path(args.config)))
        return 0
    print("giye export: expected 'warc' or 'ro-crate'", file=sys.stderr)
    return 2


def _explore(args: argparse.Namespace) -> int:
    """Write the rim order. With ``--assignment`` and ``--ties``, also score a division."""
    import json
    from pathlib import Path

    from giye.config import load
    from giye.explore.evaluate import evaluate
    from giye.explore.rim import build_rim_order, write_rim_order

    config = load(args.config)
    document = build_rim_order(config)
    path = write_rim_order(document, config.site)
    artists = sum(int(family.get("n") or 0) for family in document.get("families") or [])
    print(f"rim\t{path}\tartists={artists}\tarcs={len(document.get('families') or [])}")
    assignment_path = getattr(args, "assignment", None)
    ties_path = getattr(args, "ties", None)
    if not assignment_path and not ties_path:
        return 0
    if not assignment_path or not ties_path:
        print("giye explore: evaluation needs both --assignment and --ties", file=sys.stderr)
        return 2
    assignment = json.loads(Path(assignment_path).read_text(encoding="utf-8"))
    raw_ties = json.loads(Path(ties_path).read_text(encoding="utf-8"))
    if not isinstance(assignment, dict) or not isinstance(raw_ties, list):
        print("giye explore: assignment is an object, ties is a list of pairs", file=sys.stderr)
        return 2
    ties = [tuple(pair) for pair in raw_ties]
    scored = evaluate(assignment, ties)
    lift = scored.get("lift")
    auc = scored.get("auc")
    share = scored["coverage"]["placed_share"]
    stability = scored["stability"]["ari_mean"]
    print(
        f"coverage={share if share is None else f'{share:.3f}'} "
        f"groups={scored['coverage']['n_groups']} "
        f"lift={lift if lift is None else f'{lift:.3f}'} "
        f"auc={auc if auc is None else f'{auc:.3f}'} "
        f"stability={stability if stability is None else f'{stability:.3f}'}"
    )
    return 0


def _run(args: argparse.Namespace) -> int:
    """collect → extract → resolve → normalize → publish → explore.

    ``ledger`` prints counts and does not change the ledger, so it is not a step.
    Extract uses the config: with no API key it reads the replay cache and does
    not call a model.
    """
    steps = (
        ("collect", _collect),
        ("extract", _extract),
        ("resolve", _resolve),
        ("normalize", _normalize),
        ("publish", _publish),
        ("explore", _explore),
    )
    for name, step in steps:
        print(f"== {name} ==")
        code = step(args)
        if code:
            return code
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
        if stage == "explore":
            sp.add_argument(
                "--assignment",
                default=None,
                help="JSON object of person id → group. With --ties, score the division.",
            )
            sp.add_argument(
                "--ties",
                default=None,
                help="JSON list of [id, id] pairs the division was not built from.",
            )
        if stage == "extract":
            sp.add_argument(
                "--replay-only",
                action="store_true",
                help="Use the replay cache only. Do not call a model, even when an API key is set.",
            )
            sp.add_argument(
                "--provider",
                choices=("anthropic", "openai_compatible"),
                default=None,
                help="Override [extract] provider. Default is the config, else anthropic.",
            )
            sp.add_argument(
                "--base-url",
                default=None,
                help="OpenAI-compatible base URL (default http://localhost:11434/v1). Appends /chat/completions.",
            )
            sp.add_argument(
                "--model",
                default=None,
                help="Override [extract] model. The cache key records this string as given.",
            )
    nk = sub.add_parser("name-keys", help="print romanized matching keys for names (rule X1)")
    nk.add_argument("names", nargs="+")
    demo = sub.add_parser("demo", help="run the synthetic field offline and print a summary")
    demo.add_argument("--config", default=None, help="defaults to examples/demo/giye.toml")
    demo.add_argument("--output", default=None, help="data directory; default is a new temporary directory")
    render_cmd = sub.add_parser("render", help="write one plain HTML page per person from the site snapshot")
    render_cmd.add_argument("--config", default="giye.toml")
    export = sub.add_parser("export", help="write the snapshot store as WARC, or the run as an RO-Crate")
    export_sub = export.add_subparsers(dest="export_cmd", required=True)
    warc = export_sub.add_parser("warc", help="WARC 1.1 of kept snapshot bodies (optional WACZ)")
    warc.add_argument("--config", default="giye.toml")
    warc.add_argument("--output", default=None, help="WARC path; default is <data>/work/export/snapshots.warc.gz")
    warc.add_argument("--wacz", action="store_true", help="also write a WACZ 1.1.1 package next to the WARC")
    crate = export_sub.add_parser("ro-crate", help="RO-Crate 1.1 metadata for this run")
    crate.add_argument("--config", default="giye.toml")
    crate.add_argument(
        "--output",
        default=None,
        help="directory or ro-crate-metadata.json path; default is <data>/work/export/ro-crate/",
    )
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
    if args.cmd == "export":
        return _export(args)
    if args.cmd == "explore":
        return _explore(args)
    if args.cmd == "run":
        return _run(args)
    return _not_ported(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
