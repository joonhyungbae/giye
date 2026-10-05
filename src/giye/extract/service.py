# SPDX-License-Identifier: AGPL-3.0-only
"""Run CV registration, fetch, cached extraction, and ledger apply.

``giye extract --config giye.toml`` does the four steps. ``--replay-only``
never calls a model: a cache miss is reported and that artist is left for a
later run. With no API key the Anthropic path does the same, so the demo runs
offline. ``openai_compatible`` calls the local server on a miss. When the
environment variable named by ``[extract] api_key_env`` is set, that value
is sent as a Bearer token; a local server does not require one. A
content-hash change (or a new prompt digest, or a new model, or a new
``chunk_chars``) misses the previous cache entry and reads the CV again.
The model string is stored as given, so a local id does not reuse a hosted
response. A CV longer than ``chunk_chars`` is split first
(``giye.extract.chunk``). Each piece is its own call. The rows are
concatenated. One piece that fails drops the artist; a partial file is not
written.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from pydantic import ValidationError

from giye.collect.fetch import fetcher_from_config
from giye.config import Config
from giye.extract.apply import ApplyStats, apply_extractions
from giye.extract.chunk import split_cv
from giye.extract.paths import resolve_stored
from giye.extract.prompt import prompt_sha256, prompt_text
from giye.extract.provider import (
    AnthropicProvider,
    CacheMiss,
    OpenAICompatibleProvider,
    ProviderError,
    ReplayProvider,
    write_cache,
)
from giye.extract.pull import pull_active
from giye.extract.registry import register
from giye.extract.schema import parse_extraction, without_unknown_sources
from giye.extract.text import bundle_fingerprint
from giye.ledger.ledger import Ledger
from giye.resolve.teams import team_like


@dataclass
class ExtractResult:
    """Counts from one extract run. Misses are ledger ids, not exceptions."""

    registered: int = 0
    skipped_team: list[str] = field(default_factory=list)
    skipped_unmatched: list[str] = field(default_factory=list)
    pull: dict[str, int] = field(default_factory=dict)
    extracted: list[str] = field(default_factory=list)
    replay_misses: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    apply: ApplyStats | None = None


def api_key_configured() -> bool:
    """True when the process has an Anthropic key in the environment.

    Production also accepts an ``ant auth login`` profile. Probing that profile
    imports the SDK and can reach the network, so this release treats a missing
    environment key as "replay the cache" for the Anthropic provider only.
    ``openai_compatible`` does not consult this. Its bearer token, when one
    is set, comes from the variable named by ``[extract] api_key_env``.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def extract(config: Config, *, replay_only: bool = False, today: date | None = None) -> ExtractResult:
    """Register configured sources, pull them, replay or call the model, then apply."""
    ledger = Ledger.open(config)
    result = ExtractResult()
    _register_configured(ledger, config, result)
    fetcher = fetcher_from_config(config)
    result.pull = pull_active(ledger, fetcher, today=today)
    _extract_pending(ledger, config, result, replay_only=replay_only)
    result.apply = apply_extractions(ledger, today=today)
    return result


def _register_configured(ledger: Ledger, config: Config, result: ExtractResult) -> None:
    artists = ledger.read("artists")
    for spec in config.extract_sources:
        artist = _match_artist(artists, spec)
        label = spec.source_id or spec.url
        if artist is None:
            result.skipped_unmatched.append(label)
            continue
        reason = team_like(artist)
        if reason and not config.extract_allow_team:
            result.skipped_team.append(f"{artist['ledger_id']} ({reason})")
            continue
        _source_id, added = register(
            ledger,
            artist["ledger_id"],
            spec.lang,
            spec.url,
            kind=spec.kind or None,
            note=spec.note,
            fetch_url=spec.fetch_url,
            source_id=spec.source_id,
            allow_team=config.extract_allow_team,
        )
        result.registered += int(added)


def _match_artist(artists: list[dict[str, str]], spec: object) -> dict[str, str] | None:
    ledger_id = getattr(spec, "ledger_id", "")
    name_ko = getattr(spec, "name_ko", "")
    name_en = getattr(spec, "name_en", "")
    if ledger_id:
        hits = [row for row in artists if row.get("ledger_id") == ledger_id]
    elif name_ko or name_en:
        hits = []
        for row in artists:
            if name_ko and (row.get("name_ko") or "") != name_ko:
                continue
            if name_en and (row.get("name_en") or "") != name_en:
                continue
            hits.append(row)
    else:
        hits = []
    if len(hits) == 1:
        return hits[0]
    return None


def _extract_pending(ledger: Ledger, config: Config, result: ExtractResult, *, replay_only: bool) -> None:
    names = {row["ledger_id"]: row.get("name_ko") or row.get("name_en") or "" for row in ledger.read("artists")}
    by_artist: dict[str, list[dict[str, str]]] = {}
    for source in ledger.read("cv_sources"):
        if source.get("active", "true").lower() in ("false", "0", "no"):
            continue
        if not source.get("snapshot_path") or not source.get("content_sha256"):
            continue
        by_artist.setdefault(source["ledger_id"], []).append(source)

    prompt = prompt_text()
    prompt_sha = prompt_sha256()
    model = config.extract_model
    chunk_chars = config.extract_chunk_chars
    extract_dir = config.work / "cv_extract"
    cache_dir = config.extract_cache or (config.work / "cv_cache")

    for ledger_id, sources in sorted(by_artist.items()):
        path = extract_dir / f"{ledger_id}.json"
        if _extraction_current(path, sources, prompt_sha=prompt_sha, model=model, chunk_chars=chunk_chars):
            continue
        documents = _documents(config, sources)
        if not documents:
            result.invalid.append(ledger_id)
            continue
        # The file hash is the whole CV. Piece calls use their own hash so
        # each piece replays alone. Rows are concatenated in piece order and
        # not folded here; apply already merges the same title and venue.
        content_sha = bundle_fingerprint(documents)
        pieces, chunk_count = _pieces(names.get(ledger_id, ""), documents, chunk_chars)
        source_ids = {source["source_id"] for source in sources}
        activities = []
        failed = False
        for rendered, digest in pieces:
            try:
                raw = _complete(
                    cache_dir,
                    prompt=prompt,
                    document=rendered,
                    content_sha256=digest,
                    prompt_sha256=prompt_sha,
                    model=model,
                    temperature=config.extract_temperature,
                    replay_only=replay_only,
                    provider=config.extract_provider,
                    base_url=config.extract_base_url,
                    api_key_env=config.extract_api_key_env,
                )
            except ProviderError:
                result.invalid.append(ledger_id)
                failed = True
                break
            if isinstance(raw, CacheMiss):
                result.replay_misses.append(ledger_id)
                failed = True
                break
            try:
                parsed = parse_extraction(raw)
            except ValidationError:
                result.invalid.append(ledger_id)
                failed = True
                break
            kept, _dropped = without_unknown_sources(parsed, source_ids)
            activities.extend(kept.activities)
        if failed:
            continue
        extract_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "ledger_id": ledger_id,
            "extracted_by": model,
            "prompt_sha256": prompt_sha,
            "content_sha256": content_sha,
            "chunk_chars": chunk_chars,
            "chunks": chunk_count,
            "sources": [{"source_id": source["source_id"], "content_sha256": source["content_sha256"]} for source in sources],
            "activities": [row.model_dump() for row in activities],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        result.extracted.append(ledger_id)


def _pieces(name: str, documents: list[tuple[str, str]], chunk_chars: int) -> tuple[list[tuple[str, str]], int]:
    """Rendered calls ``(document, content sha)`` and the piece count.

    Documents are taken in ``source_id`` order, the order
    ``_render_document`` and ``bundle_fingerprint`` already use. When every
    document is one piece they are sent together, and the sha is
    ``bundle_fingerprint`` of the whole CV, so an existing replay still hits.
    When any document is split, each piece is its own call. That call's sha
    is the SHA-256 of the rendered piece, so one piece can replay without
    the others. ``chunks`` in the extraction file is this piece count (one
    per document when nothing was split).
    """
    ordered = sorted(documents, key=lambda item: item[0])
    grouped: list[tuple[str, str]] = []
    for source_id, text in ordered:
        grouped.extend((source_id, piece) for piece in split_cv(text, chunk_chars))
    count = len(grouped)
    if count == len(ordered):
        return [(_render_document(name, documents), bundle_fingerprint(documents))], count
    pieces: list[tuple[str, str]] = []
    for index, (source_id, piece) in enumerate(grouped, start=1):
        rendered = _render_document(name, [(source_id, piece)], part=(index, count))
        digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        pieces.append((rendered, digest))
    return pieces, count


def _extraction_current(
    path, sources: list[dict[str, str]], *, prompt_sha: str, model: str, chunk_chars: int
) -> bool:
    """True when source hashes, prompt, model, and ``chunk_chars`` still match.

    Production compared source content hashes only. The replay key also
    includes the prompt digest and the model, so a change to either reads the
    CV again instead of keeping an extraction made with the old instructions.
    ``chunk_chars`` is part of the same check. A file from before this setting
    has no key; that counts as 0, the hosted default. Any other budget reads
    the CV again.
    """
    if not path.is_file():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("prompt_sha256") != prompt_sha or data.get("extracted_by") != model:
        return False
    if data.get("chunk_chars", 0) != chunk_chars:
        return False
    done = {item["source_id"]: item.get("content_sha256") for item in data.get("sources", [])}
    return all(done.get(source["source_id"]) == source["content_sha256"] for source in sources)


def _documents(config: Config, sources: list[dict[str, str]]) -> list[tuple[str, str]]:
    """``(source_id, snapshot text)`` for sources that still have a text file."""
    found: list[tuple[str, str]] = []
    for source in sources:
        stored = source.get("snapshot_path") or ""
        if not stored:
            continue
        path = resolve_stored(config, stored + ".txt")
        if not path.is_file():
            continue
        found.append((source["source_id"], path.read_text(encoding="utf-8")))
    return found


def _render_document(
    name: str, documents: list[tuple[str, str]], *, part: tuple[int, int] | None = None
) -> str:
    """The user message: each CV labelled with the source id the model must copy.

    ``part`` is ``(k, n)`` when this call is one piece of a split CV. The
    notice sits before the instruction line. A whole CV omits it, so the
    message stays the one a replay cache was written for.
    """
    chunks = []
    for source_id, text in sorted(documents, key=lambda item: item[0]):
        chunks.append(f"--- CV document source_id={source_id} ---\n{text}")
    if part is not None:
        k, n = part
        chunks.append(f"Part {k} of {n} of this CV.")
    chunks.append(f"Artist: {name}. Extract the activity rows.")
    return "\n\n".join(chunks)


def _complete(
    cache_dir,
    *,
    prompt: str,
    document: str,
    content_sha256: str,
    prompt_sha256: str,
    model: str,
    temperature: float | None,
    replay_only: bool,
    provider: str,
    base_url: str,
    api_key_env: str,
) -> str | CacheMiss:
    replay = ReplayProvider(cache_dir, content_sha256=content_sha256, prompt_sha256=prompt_sha256, model=model)
    try:
        return replay.complete(prompt, document)
    except CacheMiss as miss:
        # A missing Anthropic key stays on the replay path. A local server does not.
        if replay_only or (provider == "anthropic" and not api_key_configured()):
            return miss
    response_mode: str | None = None
    try:
        if provider == "openai_compatible":
            live = OpenAICompatibleProvider(model, base_url, temperature, api_key_env=api_key_env)
            text = live.complete(prompt, document)
            response_mode = live.response_mode
        elif provider == "anthropic":
            text = AnthropicProvider(model, temperature).complete(prompt, document)
        else:
            raise ProviderError(f"unknown provider: {provider}")
    except ProviderError:
        raise
    except Exception as exc:
        # The SDK raises several types (auth, rate limit, connection). Skip this artist.
        raise ProviderError(str(exc)) from exc
    # The next run replays this text. The key includes the model id as given,
    # so a local name and a hosted name never share a file. response_mode is
    # recorded and is not part of that key.
    write_cache(
        cache_dir,
        content_sha256=content_sha256,
        prompt_sha256=prompt_sha256,
        model=model,
        response=text,
        temperature=temperature,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        response_mode=response_mode,
    )
    return text
