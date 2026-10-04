# SPDX-License-Identifier: AGPL-3.0-only
"""Compare a local model with the demo's cached hosted CV extractions.

What: for each synthetic CV under ``examples/demo``, call an OpenAI-compatible
server and compare its activity rows with the replay cache ``giye demo`` reads.
Those cache files are the hosted responses (model id ``claude-opus-5``). They
are hand-written and marked ``synthetic``; this script does not call Anthropic.

Why: a local model is the path when CV text must not leave the machine. The
numbers say how often that model repeats the cached rows on the fictitious
demo field, and how often it names a year or a venue the CV does not contain.

How to run: from the repository root, with this package importable and a server
such as Ollama already listening. The interpreter is ``python3`` (3.10 or
newer). No extra dependency: the call uses ``requests``, which Giye already
requires.

    python3 tools/compare_local_extraction.py
    python3 tools/compare_local_extraction.py --model qwen2.5:7b
    python3 tools/compare_local_extraction.py --model qwen2.5:14b --base-url http://localhost:11434/v1

Temperature is 0 because the demo cache records 0. A sample would not be an
agreement check. The report is ``docs/local-extraction.md``. A second model
replaces only its own section.

A row matches when the year, the activity type, and the title agree. The title
is Unicode NFC, then casefold, then runs of whitespace collapsed. Recall is
matched/hosted. Precision is matched/local. An invented row is a local row
whose year is absent from the CV text, or whose non-empty venue is absent
after whitespace is collapsed. Matching is one-to-one: two identical hosted
rows need two local rows.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import ValidationError

from giye.collect.fetch import fetcher_from_config
from giye.config import load
from giye.extract.prompt import prompt_sha256, prompt_text
from giye.extract.provider import OpenAICompatibleProvider, ProviderError, cache_path
from giye.extract.schema import Entry, parse_extraction
from giye.extract.service import _render_document
from giye.extract.text import bundle_fingerprint, extension_for, extract_text, normalize

DEMO_CONFIG = ROOT / "examples" / "demo" / "giye.toml"
DEFAULT_REPORT = ROOT / "docs" / "local-extraction.md"
_BLOCK = re.compile(
    r"<!-- begin model (?P<model>.*?) -->\n.*?\n<!-- end model (?P=model) -->",
    re.DOTALL,
)
_PREAMBLE = """# Local extraction agreement

Each section is one local model against the synthetic demo replay cache
(`examples/demo/cache`, model id `claude-opus-5`). The cache is what `giye demo`
replays. It is hand-written (`synthetic: true`), not a fresh hosted call.

A row matches when year, activity type, and title agree. The title is Unicode
NFC, then casefold, then whitespace collapsed. Recall is matched/hosted.
Precision is matched/local. Both are recomputed from the totals, not averaged
across CVs. An invented row names a year that does not occur in the CV text,
or a non-empty venue that does not occur once whitespace is collapsed. Invalid
counts a response that is not JSON matching the activity schema, or a provider
error that returned no text. Temperature is 0, the value stored on the cache
records.
"""


@dataclass
class CvCase:
    """One demo CV and the hosted rows cached for it."""

    source_id: str
    text: str
    document: str
    hosted: list[Entry]


@dataclass
class Score:
    """Agreement for one CV, or the summed overall line."""

    source_id: str
    hosted: int
    local: int
    matched: int
    invented: int
    invalid: int
    note: str = ""


def normalise_title(title: str) -> str:
    """NFC, then casefold, then one space where the CV had a run of whitespace."""
    folded = unicodedata.normalize("NFC", title).casefold()
    return re.sub(r"\s+", " ", folded).strip()


def match_count(hosted: list[Entry], local: list[Entry]) -> int:
    """How many hosted rows have a distinct local row with the same key."""
    left: Counter[tuple] = Counter((row.year, row.activity_type, normalise_title(row.title)) for row in hosted)
    right: Counter[tuple] = Counter((row.year, row.activity_type, normalise_title(row.title)) for row in local)
    return sum(min(left[key], right[key]) for key in left)


def invented_count(rows: list[Entry], cv_text: str) -> int:
    """Rows whose year, or non-empty venue, is not in the CV text."""
    haystack = re.sub(r"\s+", " ", cv_text)
    count = 0
    for row in rows:
        year_missing = str(row.year) not in cv_text
        venue = row.venue.strip()
        venue_missing = bool(venue) and re.sub(r"\s+", " ", venue) not in haystack
        if year_missing or venue_missing:
            count += 1
    return count


def load_cases(config_path: Path) -> list[CvCase]:
    """Read each demo CV the way extract does, and the cache file for its hash."""
    config = load(config_path)
    if config.extract_cache is None:
        raise SystemExit(f"{config_path}: [extract] cache is required")
    fetcher = fetcher_from_config(config)
    prompt_sha = prompt_sha256()
    cases: list[CvCase] = []
    for spec in config.extract_sources:
        page = fetcher.get(spec.url)
        if not page.ok:
            raise SystemExit(f"{spec.source_id}: fixture fetch failed (HTTP {page.status})")
        ext = extension_for(page.content, page.content_type, spec.kind or "web")
        # pull stores the normalised text plus a trailing newline; extract reads that back.
        text = normalize(extract_text(page.content, ext)) + "\n"
        document = _render_document(spec.name_ko or spec.name_en, [(spec.source_id, text)])
        digest = bundle_fingerprint([(spec.source_id, text)])
        path = cache_path(config.extract_cache, digest, prompt_sha, config.extract_model)
        if not path.is_file():
            raise SystemExit(f"no hosted cache for {spec.source_id} ({path.name})")
        payload = json.loads(path.read_text(encoding="utf-8"))
        hosted = parse_extraction(payload["response"]).activities
        cases.append(CvCase(source_id=spec.source_id, text=text, document=document, hosted=hosted))
    if not cases:
        raise SystemExit(f"{config_path}: no [[extract.sources]]")
    return cases


def score_case(case: CvCase, provider: OpenAICompatibleProvider, prompt: str) -> Score:
    """One local completion. A schema failure is an invalid row, not a crash."""
    try:
        raw = provider.complete(prompt, case.document)
    except ProviderError as exc:
        note = re.sub(r"\s+", " ", str(exc))[:400]
        return Score(case.source_id, len(case.hosted), 0, 0, 0, 1, f"provider error: {note}")
    try:
        local = parse_extraction(raw).activities
    except ValidationError as exc:
        return Score(case.source_id, len(case.hosted), 0, 0, 0, 1, _schema_note(exc))
    return Score(
        source_id=case.source_id,
        hosted=len(case.hosted),
        local=len(local),
        matched=match_count(case.hosted, local),
        invented=invented_count(local, case.text),
        invalid=0,
    )


def _schema_note(exc: ValidationError) -> str:
    """A short schema failure. The full pydantic text repeats the same URL for every field."""
    errors = exc.errors()
    bits = []
    for err in errors[:6]:
        loc = ".".join(str(part) for part in err.get("loc", ())) or "<root>"
        bits.append(f"{loc} {err.get('type')}")
    extra = len(errors) - len(bits)
    suffix = f" (+{extra} more)" if extra > 0 else ""
    return f"invalid JSON or schema: {len(errors)} errors: " + "; ".join(bits) + suffix


def _rate(matched: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{matched / total:.3f}"


def _overall(scores: list[Score]) -> Score:
    return Score(
        source_id="overall",
        hosted=sum(item.hosted for item in scores),
        local=sum(item.local for item in scores),
        matched=sum(item.matched for item in scores),
        invented=sum(item.invented for item in scores),
        invalid=sum(item.invalid for item in scores),
    )


def _line(score: Score) -> str:
    return (
        f"| {score.source_id} | {score.hosted} | {score.local} | {score.matched} "
        f"| {_rate(score.matched, score.hosted)} | {_rate(score.matched, score.local)} "
        f"| {score.invented} | {score.invalid} |"
    )


def render_section(model: str, base_url: str, ollama: str, when: str, scores: list[Score]) -> str:
    """One model block. Markers let a later run replace this model only."""
    lines = [
        f"<!-- begin model {model} -->",
        f"## {model}",
        "",
        f"- Date (UTC): {when}",
        f"- Ollama: {ollama}",
        f"- Base URL: `{base_url}`",
        "",
        "| CV | hosted | local | matched | recall | precision | invented | invalid |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(_line(score) for score in scores)
    lines.append(_line(_overall(scores)))
    notes = [score for score in scores if score.note]
    if notes:
        lines.append("")
        lines.append("Failures:")
        lines.append("")
        for score in notes:
            lines.append(f"- `{score.source_id}`: {score.note}")
    lines.append(f"<!-- end model {model} -->")
    return "\n".join(lines)


def ollama_version() -> str:
    """First line of ``ollama --version``. The report names the server that ran."""
    try:
        completed = subprocess.run(
            ["ollama", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable ({type(exc).__name__})"
    text = (completed.stdout or completed.stderr).strip()
    return text.splitlines()[0] if text else "unavailable"


def upsert(path: Path, model: str, section: str) -> None:
    """Rewrite the report, keeping sections for models other than ``model``."""
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    sections = {match.group("model"): match.group(0) for match in _BLOCK.finditer(existing)}
    sections[model] = section
    body = "\n\n".join(sections[key] for key in sorted(sections))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PREAMBLE.rstrip() + "\n\n" + body + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--model", default="qwen2.5:14b")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args(argv)
    cases = load_cases(DEMO_CONFIG)
    provider = OpenAICompatibleProvider(args.model, args.base_url, temperature=0, timeout=args.timeout)
    prompt = prompt_text()
    scores = [score_case(case, provider, prompt) for case in cases]
    when = datetime.now(timezone.utc).date().isoformat()
    section = render_section(args.model, args.base_url.rstrip("/"), ollama_version(), when, scores)
    report = args.report if args.report.is_absolute() else ROOT / args.report
    upsert(report, args.model, section)
    print(section)
    print(f"wrote {report}")
    if scores and all(score.note.startswith("provider error: connection error") for score in scores):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
