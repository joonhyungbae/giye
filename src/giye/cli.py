# SPDX-License-Identifier: AGPL-3.0-only
"""Command line: ``giye <stage> --config giye.toml`` or ``giye run`` for the whole chain.

A stage this package does not run yet says so and exits with status 2, so a chain
fails loudly instead of skipping a step (see docs/ROADMAP.md).
"""

from __future__ import annotations

import argparse
import sys

from giye import __version__
from giye.config import GiyeError

STAGES = ["collect", "extract", "ledger", "resolve", "normalize", "explore", "publish"]


def _unknown_command(command: str) -> int:
    """Exit 2 for a command the dispatcher does not handle (argparse normally rejects it first)."""
    print(f"giye: error: unknown command {command}", file=sys.stderr)
    return 2


def _open_config(path: str):
    """Load ``giye.toml``. A bad file is a ``ConfigError``, not a traceback."""
    from giye.config import ConfigError, GiyeError, load

    try:
        return load(path)
    except GiyeError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigError(str(exc)) from exc


def _resolve(args: argparse.Namespace) -> int:
    from giye.resolve.service import resolve

    result = resolve(_open_config(args.config), dry_run=getattr(args, "dry_run", False))
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

    config = _open_config(args.config)
    refusals: list = []
    results = run_configured(config, from_snapshots=getattr(args, "from_snapshots", False), refusals=refusals)
    if not results:
        print(
            "giye collect: no collectors configured (set collect.collector_modules in the config)",
            file=sys.stderr,
        )
        return 2
    for frame, rows, path in results:
        print(f"{frame}\t{len(rows)}\t{path}")
    # A collector failed when a fetch was refused and it wrote no row. A refusal is
    # recorded, not fatal; the run fails only when no collector got anything.
    refused_frames = {item.frame for item in refusals}
    failed = [frame for frame, rows, _path in results if frame in refused_frames and not rows]
    print(
        f"collect: {len(results)} collectors, {sum(len(rows) for _f, rows, _p in results)} rows, "
        f"{len(refusals)} refused fetches, {len(failed)} collectors with no rows after a refusal",
        file=sys.stderr,
    )
    return 1 if len(failed) == len(results) else 0


def _extract(args: argparse.Namespace) -> int:
    from dataclasses import replace

    from giye.config import default_chunk_chars
    from giye.extract.service import extract

    config = _open_config(args.config)
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
    from giye.demo import ledger_counts

    counts = ledger_counts(_open_config(args.config))
    for name, count in counts.items():
        print(f"{name}\t{count}")
    return 0


def _publish(args: argparse.Namespace) -> int:
    from giye.publish import publish

    publish(_open_config(args.config))
    return 0


def _render(args: argparse.Namespace) -> int:
    from giye.publish import render

    pages = render(_open_config(args.config))
    print(f"pages={len(pages)}")
    return 0


def _demo(args: argparse.Namespace) -> int:
    from giye.demo import run_demo

    result = run_demo(args.config, args.output)
    print(result.summary)
    return 0


def _normalize(args: argparse.Namespace) -> int:
    from giye.normalize.service import normalize

    result = normalize(_open_config(args.config), venue_name_rules=getattr(args, "venue_name_rules", None))
    print(result.report, end="")
    return 0


def _export(args: argparse.Namespace) -> int:
    from pathlib import Path

    from giye.export.rocrate import export_ro_crate
    from giye.export.warc import export_warc

    config = _open_config(args.config)
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


def _explore_ties(args: argparse.Namespace) -> int:
    """``giye explore ties``: write the co-presence ties, and with ``--layers`` the rule layers."""
    import json
    from pathlib import Path

    from giye.explore.ties import format_layers, layers_for_config, ties_for_config

    config = _open_config(args.config)
    kind = getattr(args, "kind", None) or "roster-independent"
    out = getattr(args, "out", None)
    layers = getattr(args, "layers", None)
    if out or not layers:
        pairs = sorted(ties_for_config(config, kind))
        people = len({person for pair in pairs for person in pair})
        if out:
            Path(out).write_text(json.dumps([list(pair) for pair in pairs]) + "\n", encoding="utf-8")
        print(f"ties\t{kind}\tties={len(pairs)}\tpeople_in_ties={people}" + (f"\t{out}" if out else ""))
    if layers:
        report = layers_for_config(config)
        print(format_layers(report))
        if layers != "-":
            Path(layers).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"layers\t{layers}")
    return 0


def _explore(args: argparse.Namespace) -> int:
    """Write the rim order. With ``--assignment``, also score a division.

    The ties are ``--ties`` when given, else the roster-independent co-presence
    ties computed from the ledger (``giye.explore.ties``).
    """
    import json
    from pathlib import Path

    from giye.explore.evaluate import evaluate
    from giye.explore.rim import build_rim_order, write_rim_order

    if getattr(args, "action", None) == "ties":
        return _explore_ties(args)
    config = _open_config(args.config)
    document = build_rim_order(config)
    path = write_rim_order(document, config.site)
    artists = sum(int(family.get("n") or 0) for family in document.get("families") or [])
    print(f"rim\t{path}\tartists={artists}\tarcs={len(document.get('families') or [])}")
    assignment_path = getattr(args, "assignment", None)
    ties_path = getattr(args, "ties", None)
    if not assignment_path and not ties_path:
        return 0
    if not assignment_path:
        print("giye explore: --ties needs --assignment", file=sys.stderr)
        return 2
    assignment = json.loads(Path(assignment_path).read_text(encoding="utf-8"))
    if ties_path:
        raw_ties = json.loads(Path(ties_path).read_text(encoding="utf-8"))
    else:
        from giye.explore.ties import ties_for_config

        raw_ties = sorted(ties_for_config(config, "roster-independent"))
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
    from giye.normalize.language import default_language

    # The keys X1 compares, through the language module (Korean-English by default).
    language = default_language()
    for name in args.names:
        keys = language.name_keys(name)
        print(f"{name}\t{' '.join(sorted(keys)) or '-'}")
    return 0


def _queue_list(args: argparse.Namespace) -> int:
    from giye.ledger.ledger import Ledger
    from giye.resolve.decide import list_queue

    status = None if args.status == "any" else args.status
    for item in list_queue(Ledger.open(_open_config(args.config)), kind=args.kind, status=status):
        print(
            f"{item.get('queue_id')}\t{item.get('status')}\t{item.get('reason')}\t"
            f"{item.get('ledger_id')}\t{item.get('detail')}"
        )
    return 0


def _queue_decide(args: argparse.Namespace) -> int:
    from giye.ledger.ledger import Ledger
    from giye.resolve.decide import decide_queue

    item = decide_queue(
        Ledger.open(_open_config(args.config)),
        args.item_id,
        args.decision,
        evidence=args.evidence or "",
        note=args.note or "",
        override_distinct=args.override_distinct,
    )
    print(f"decide {item.get('queue_id')} {args.decision} status={item.get('status')}")
    return 0


def _merge_people(args: argparse.Namespace) -> int:
    from giye.ledger.ledger import Ledger
    from giye.resolve.decide import merge_people

    ledger = Ledger.open(_open_config(args.config))
    # A survivor can already be the target of an older retirement. Report the
    # gy_id this call retired, not that earlier redirect.
    before = {row["gy_id"] for row in ledger.read("gy_retired")} if ledger.path("gy_retired").exists() else set()
    keep, drop = merge_people(
        ledger, args.keep_id, args.drop_id, evidence=args.evidence, override_distinct=args.override_distinct
    )
    retired = [row["gy_id"] for row in ledger.read("gy_retired") if row["gy_id"] not in before]
    print(f"merge {drop} → {keep}" + (f" retired {', '.join(retired)}" if retired else ""))
    return 0


def _hide(args: argparse.Namespace) -> int:
    from giye.ledger.ledger import Ledger
    from giye.resolve.decide import hide_person

    hide_person(Ledger.open(_open_config(args.config)), args.gy_id, reason=args.reason)
    print(f"hidden {args.gy_id}")
    return 0


def _unhide(args: argparse.Namespace) -> int:
    from giye.ledger.ledger import Ledger
    from giye.resolve.decide import unhide_person

    unhide_person(Ledger.open(_open_config(args.config)), args.gy_id)
    print(f"unhidden {args.gy_id}")
    return 0


def _evidence(args: argparse.Namespace) -> int:
    from collections import Counter

    from giye.collect.evidence import archive_cited

    status = archive_cited(_open_config(args.config))
    counts = Counter(item.get("status") or "" for item in status.values())
    summary = " ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "urls=0"
    print(f"evidence {len(status)} {summary}")
    return 0


def _parser() -> argparse.ArgumentParser:
    """The ``giye`` command line. Help text is part of the public interface."""
    ap = argparse.ArgumentParser(prog="giye", description=__doc__)
    ap.add_argument("--version", action="version", version=f"giye {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    _add_stage_parsers(sub)
    _add_tool_parsers(sub)
    return ap


def _add_stage_parsers(sub: argparse._SubParsersAction) -> None:
    """``collect`` through ``run``, including the flags only one stage reads."""
    for stage in STAGES + ["run"]:
        sp = sub.add_parser(stage, help=f"run the {stage} stage" if stage != "run" else "run every stage in order")
        sp.add_argument("--config", default="giye.toml")
        if stage in {"collect", "run"}:
            sp.add_argument(
                "--from-snapshots",
                action="store_true",
                help=(
                    "re-run roster collectors on kept snapshot bodies; "
                    "no socket, no robots.txt fetch, no new snapshot lines; "
                    "collected_at is the kept page's fetch date"
                ),
            )
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
                "action",
                nargs="?",
                choices=("ties",),
                default=None,
                help="'ties': write co-presence ties (giye.explore.ties) instead of the rim order.",
            )
            sp.add_argument(
                "--assignment",
                default=None,
                help="JSON object of person id → group. Scores the division against --ties.",
            )
            sp.add_argument(
                "--ties",
                default=None,
                help=(
                    "JSON list of [id, id] pairs the division was not built from. "
                    "Default: roster-independent co-presence computed from the ledger."
                ),
            )
            sp.add_argument("--out", default=None, help="ties: write the pairs as a JSON list to this path.")
            sp.add_argument(
                "--kind",
                choices=("roster-independent", "cv-listing"),
                default="roster-independent",
                help="ties: which definition (default roster-independent, the evaluation outcome).",
            )
            sp.add_argument(
                "--layers",
                nargs="?",
                const="-",
                default=None,
                help=(
                    "ties: print entities and ties under base, V7, V7+V8, V7+V8+V9 and the "
                    "per-rule attribution; with a path, also write them there as JSON."
                ),
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


def _add_tool_parsers(sub: argparse._SubParsersAction) -> None:
    """Commands that are not a pipeline stage: keys, demo, render, export, queue, merge, hide."""
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
    queue = sub.add_parser("queue", help="list or close a review-queue item")
    queue_sub = queue.add_subparsers(dest="queue_cmd", required=True)
    queue_list = queue_sub.add_parser("list", help="list review items")
    queue_list.add_argument("--config", default="giye.toml")
    queue_list.add_argument("--kind", default=None, help="reason column, for example possible_same_person")
    queue_list.add_argument("--status", default="open", help="status to list; 'any' lists every status")
    queue_decide = queue_sub.add_parser("decide", help="close one item: merge, distinct, or dismiss")
    queue_decide.add_argument("item_id")
    queue_decide.add_argument("--config", default="giye.toml")
    queue_decide.add_argument("--decision", required=True, choices=("merge", "distinct", "dismiss"))
    queue_decide.add_argument(
        "--evidence", default="", help="required for --decision merge: E1-E4 (or X1+E) with a citation, or H with a reason and date"
    )
    queue_decide.add_argument(
        "--override-distinct", action="store_true", help="allow a merge of a pair decided distinct (recorded)"
    )
    queue_decide.add_argument("--note", default="")
    merge_cmd = sub.add_parser("merge", help="merge two people and retire the dropped gy_id")
    merge_cmd.add_argument("keep_id", help="ledger id or gy_id to keep")
    merge_cmd.add_argument("drop_id", help="ledger id or gy_id to retire")
    merge_cmd.add_argument("--config", default="giye.toml")
    merge_cmd.add_argument(
        "--evidence", required=True, help="E1-E4 (or X1+E) with a citation, or H with a reason and date"
    )
    merge_cmd.add_argument(
        "--override-distinct", action="store_true", help="allow merging a pair decided distinct (recorded)"
    )
    hide_cmd = sub.add_parser("hide", help="hide a page (HIDDEN_BY_REQUEST tombstone)")
    hide_cmd.add_argument("gy_id")
    hide_cmd.add_argument("--config", default="giye.toml")
    hide_cmd.add_argument("--reason", required=True)
    unhide_cmd = sub.add_parser("unhide", help="publish a hidden page again")
    unhide_cmd.add_argument("gy_id")
    unhide_cmd.add_argument("--config", default="giye.toml")
    evidence_cmd = sub.add_parser("evidence", help="keep a copy of every URL the ledger cites")
    evidence_cmd.add_argument("--config", default="giye.toml")


def main(argv: list[str] | None = None) -> int:
    """Run one ``giye`` command. Returns 0 on success and 2 on a usage or stage error."""
    args = _parser().parse_args(argv)
    # One command is one run: one ledger backup per file and task (giye.ledger.io).
    from giye.ledger.io import start_backup_run

    start_backup_run()
    try:
        return _dispatch(args)
    except GiyeError as exc:
        print(f"giye: error: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    """Run the parsed command. A command this package does not run yet exits 2."""
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
    if args.cmd == "queue" and args.queue_cmd == "list":
        return _queue_list(args)
    if args.cmd == "queue" and args.queue_cmd == "decide":
        return _queue_decide(args)
    if args.cmd == "merge":
        return _merge_people(args)
    if args.cmd == "hide":
        return _hide(args)
    if args.cmd == "unhide":
        return _unhide(args)
    if args.cmd == "evidence":
        return _evidence(args)
    return _unknown_command(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
