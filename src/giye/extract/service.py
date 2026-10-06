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
response. A whole-CV call is keyed by its source ids as well as its text
(``replay_key``): two people can share one CV text under two source ids, and
each response names the source ids of its rows. A cache written before that
key (text only) is still read. A reading that would remove every CV row a
person has is a replay miss, not an empty extraction (``_drops_everything``). A CV longer than ``chunk_chars`` is split first
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
from giye.extract.paths import verified_cv_text
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
from giye.extract.text import bundle_fingerprint, replay_key
from giye.ledger.io import write_text_atomic
from giye.ledger.ledger import Ledger
from giye.normalize.language import language_for
from giye.resolve.teams import team_like


@dataclass
class ExtractResult:
    """Counts from one extract run. Misses are ledger ids, not exceptions."""

    registered: int = 0
    skipped_team: list[str] = field(default_factory=list)
    skipped_unmatched: list[str] = field(default_factory=list)
    pull: dict[str, int] = field(default_factory=dict)
    extracted: list[str] = field(default_factory=list)
    # No stored response for the key (replay) or no call made.
    replay_misses: list[str] = field(default_factory=list)
    # A response whose rows all name sources not sent with it (another
    # person's reading of the same text). Kept apart from ``replay_misses``:
    # the cache answered, but the answer is not this person's.
    unknown_sources: list[str] = field(default_factory=list)
    # A response with no rows while the ledger holds rows from these sources.
    empty_readings: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    # (ledger id, message) for a model call that failed: connection, HTTP error,
    # refusal, truncation. Kept apart from ``invalid`` (a response that did not
    # parse), so an unreachable server is not reported as a bad extraction.
    provider_errors: list[tuple[str, str]] = field(default_factory=list)
    apply: ApplyStats | None = None


def api_key_configured() -> bool:
    """True when the process has an Anthropic key in the environment.

    An ``ant auth login`` profile is not probed. That probe imports the SDK and
    can reach the network, so a missing environment key means replay the cache
    for the Anthropic provider only.
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
    # --replay-only reaches no network: CVs already on disk are replayed as they
    # are, and only sources served from a configured offline root are pulled.
    result.pull = pull_active(ledger, fetcher, today=today, offline_only=replay_only)
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
        reason = team_like(
            artist, words=config.field_config.compiled_team_words(), language=language_for(config)
        )
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
    # Origins that already have rows, for the guard against a reading that would remove them all.
    standing = {row.get("origin") or "" for row in ledger.read("activities")}
    by_artist: dict[str, list[dict[str, str]]] = {}
    for source in ledger.read("cv_sources"):
        if source.get("active", "true").lower() in ("false", "0", "no"):
            continue
        if not source.get("snapshot_path") or not source.get("content_sha256"):
            continue
        by_artist.setdefault(source["ledger_id"], []).append(source)

    # The field file may name its own prompt; None is the packaged default.
    prompt_file = config.field_config.extract_prompt
    prompt = prompt_text(prompt_file)
    prompt_sha = prompt_sha256(prompt_file)
    model = config.extract_model
    chunk_chars = config.extract_chunk_chars
    extract_dir = config.work / "cv_extract"
    cache_dir = config.extract_cache or (config.work / "cv_cache")

    for ledger_id, sources in sorted(by_artist.items()):
        path = extract_dir / f"{ledger_id}.json"
        if _extraction_current(path, sources, prompt_sha=prompt_sha, model=model, chunk_chars=chunk_chars):
            continue
        # A merge moves every CV source onto the survivor and leaves each
        # reading in the file it was written to. The survivor's own file then
        # omits the other source, and hashing both texts together misses the
        # cache: each CV is keyed by its own text, not by the joined text.
        # The files already on disk are that reading.
        if _sources_already_extracted(
            extract_dir, sources, prompt_sha=prompt_sha, model=model, chunk_chars=chunk_chars
        ):
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
        seen = 0
        failed = False
        # Several whole CVs used to be one cache key, the hash of the texts
        # joined together. Each CV is stored under its own text hash: that is
        # the key from when the CV belonged to its own row. A merge puts both
        # sources on the survivor, and the joined hash is not in the cache, so
        # the next run would call the model again. Replay each document on the
        # key it was stored under. A bundle that is already cached is left to
        # the loop below (this returns None when any document misses).
        separate = None
        if len(documents) > 1 and chunk_count == len(documents):
            separate = _replay_each_document(
                cache_dir,
                name=names.get(ledger_id, ""),
                documents=documents,
                prompt=prompt,
                prompt_sha=prompt_sha,
                model=model,
            )
        if separate is not None:
            activities, seen = separate
        else:
            for rendered, digest, legacy in pieces:
                try:
                    raw = _complete(
                        cache_dir,
                        prompt=prompt,
                        document=rendered,
                        content_sha256=digest,
                        legacy_sha256=legacy,
                        source_ids=source_ids,
                        prompt_sha256=prompt_sha,
                        model=model,
                        temperature=config.extract_temperature,
                        replay_only=replay_only,
                        provider=config.extract_provider,
                        base_url=config.extract_base_url,
                        api_key_env=config.extract_api_key_env,
                        reasoning_effort=config.extract_reasoning_effort,
                    )
                except ProviderError as exc:
                    result.provider_errors.append((ledger_id, str(exc)))
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
                seen += len(parsed.activities)
            if failed:
                continue
        dropped = _drops_everything(seen, activities, source_ids, standing)
        if dropped == "unknown_sources":
            result.unknown_sources.append(ledger_id)
            continue
        if dropped == "empty":
            result.empty_readings.append(ledger_id)
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
        write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
        result.extracted.append(ledger_id)


def _replay_each_document(
    cache_dir,
    *,
    name: str,
    documents: list[tuple[str, str]],
    prompt: str,
    prompt_sha: str,
    model: str,
) -> tuple[list, int] | None:
    """Cached activities for each CV and the row count read, or None when any document is not cached.

    The replay key of one CV is ``replay_key`` of that document alone (or,
    for an older cache, ``bundle_fingerprint`` of its text). Joining every
    text the survivor now holds is a different key, so a merge would orphan
    the cache. Reading each document back on its own key keeps the stored
    response. This does not call a model: a miss returns None and the caller
    tries the joined key, then a live call. A response none of whose rows
    names this source (another person's reading of the same text) is a miss.
    """
    activities = []
    seen = 0
    for source_id, text in sorted(documents, key=lambda item: item[0]):
        single = [(source_id, text)]
        try:
            raw = _replay(
                cache_dir,
                prompt=prompt,
                document=_render_document(name, single),
                keys=(replay_key(single), bundle_fingerprint(single)),
                prompt_sha=prompt_sha,
                model=model,
                source_ids={source_id},
            )
        except CacheMiss:
            return None
        try:
            parsed = parse_extraction(raw)
        except ValidationError:
            return None
        kept, _dropped = without_unknown_sources(parsed, {source_id})
        if parsed.activities and not kept.activities:
            return None
        activities.extend(kept.activities)
        seen += len(parsed.activities)
    return activities, seen


def _replay(
    cache_dir,
    *,
    prompt: str,
    document: str,
    keys: tuple[str, ...],
    prompt_sha: str,
    model: str,
    source_ids: set[str] | None = None,
) -> str:
    """The stored response under the first key that has one. Raises the first key's ``CacheMiss``.

    ``keys`` is the current key, then the older text-only key. The older key
    is shared by everyone who holds the same text, so a response found there
    whose rows all name other sources is another person's reading and is
    skipped (``source_ids`` are the sources of this call).
    """
    first: CacheMiss | None = None
    for index, key in enumerate(dict.fromkeys(key for key in keys if key)):
        try:
            raw = ReplayProvider(cache_dir, content_sha256=key, prompt_sha256=prompt_sha, model=model).complete(
                prompt, document
            )
        except CacheMiss as miss:
            first = first or miss
            continue
        if index and source_ids is not None and _names_only_other_sources(raw, source_ids):
            first = first or CacheMiss(key, prompt_sha, model)
            continue
        return raw
    assert first is not None
    raise first


def _names_only_other_sources(raw: str, source_ids: set[str]) -> bool:
    """True when a stored response has rows and none of them names one of ``source_ids``."""
    try:
        parsed = parse_extraction(raw)
    except ValidationError:
        return False
    return bool(parsed.activities) and not any(row.source_id in source_ids for row in parsed.activities)


def _drops_everything(seen: int, kept: list, source_ids: set[str], standing: set[str]) -> str:
    """Why this reading would remove every CV row the person has from these sources, or "".

    Two signs of a reading that is not this person's: the response had rows
    but none named a source sent with it (``unknown_sources``: a cache entry
    written for another person who holds the same text), or it has no rows
    while the ledger holds rows from these sources (``empty``). Writing it
    would make apply delete those rows and mark the CV as read. It is not
    written, so the ledger keeps its rows and a later run reads the CV again.
    Each sign has its own counter: neither is a replay miss, because the
    cache did answer.
    """
    if kept:
        return ""
    if seen:
        return "unknown_sources"
    return "empty" if any(f"cv:{source_id}" in standing for source_id in source_ids) else ""


def _pieces(
    name: str, documents: list[tuple[str, str]], chunk_chars: int
) -> tuple[list[tuple[str, str, str | None]], int]:
    """Rendered calls ``(document, cache key, older cache key)`` and the piece count.

    Documents are taken in ``source_id`` order, the order
    ``_render_document`` and ``bundle_fingerprint`` already use. When every
    document is one piece they are sent together. The key is ``replay_key``
    of the whole CV (source ids and texts); the older key,
    ``bundle_fingerprint`` of the texts alone, is read when the new one is
    not cached. When any document is split, each piece is its own call. That
    call's key is the SHA-256 of the rendered piece, which already names the
    source id, so one piece can replay without the others and there is no
    older key. ``chunks`` in the extraction file is this piece count (one
    per document when nothing was split).
    """
    ordered = sorted(documents, key=lambda item: item[0])
    grouped: list[tuple[str, str]] = []
    for source_id, text in ordered:
        grouped.extend((source_id, piece) for piece in split_cv(text, chunk_chars))
    count = len(grouped)
    if count == len(ordered):
        rendered = _render_document(name, documents)
        return [(rendered, replay_key(documents), bundle_fingerprint(documents))], count
    pieces: list[tuple[str, str, str | None]] = []
    for index, (source_id, piece) in enumerate(grouped, start=1):
        rendered = _render_document(name, [(source_id, piece)], part=(index, count))
        digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        pieces.append((rendered, digest, None))
    return pieces, count


def _sources_already_extracted(
    extract_dir, sources: list[dict[str, str]], *, prompt_sha: str, model: str, chunk_chars: int
) -> bool:
    """True when some extraction files together already cover ``sources``.

    See the call in ``_extract_pending``. A file from before this check, with
    no ``chunk_chars``, counts as 0, the same rule as ``_extraction_current``.
    """
    if not extract_dir.is_dir():
        return False
    needed = {source["source_id"]: source.get("content_sha256") for source in sources}
    if not needed:
        return False
    found: dict[str, str | None] = {}
    for path in extract_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("prompt_sha256") != prompt_sha or data.get("extracted_by") != model:
            continue
        if data.get("chunk_chars", 0) != chunk_chars:
            continue
        for item in data.get("sources") or []:
            if isinstance(item, dict) and item.get("source_id"):
                found[item["source_id"]] = item.get("content_sha256")
    return all(found.get(source_id) == digest for source_id, digest in needed.items())


def _extraction_current(
    path, sources: list[dict[str, str]], *, prompt_sha: str, model: str, chunk_chars: int
) -> bool:
    """True when source hashes, prompt, model, and ``chunk_chars`` still match.

    Source content hashes, the prompt digest, and the model must all still
    match. A change to the prompt or the model reads the CV again instead of
    keeping an extraction made with the old instructions.
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
        # Checked against content_sha256: an edited text is refused, not read.
        text = verified_cv_text(config, source)
        if text is None:
            continue
        found.append((source["source_id"], text))
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
    legacy_sha256: str | None = None,
    source_ids: set[str] | None = None,
    provider: str,
    base_url: str,
    api_key_env: str,
    reasoning_effort: str | None = None,
) -> str | CacheMiss:
    """Replay the cache, or call the configured model on a miss. A miss with no key returns ``CacheMiss``.

    ``legacy_sha256`` is the key a cache written before ``replay_key`` used;
    it is read after ``content_sha256`` misses, unless its rows all name
    sources other than ``source_ids``. A live response is written under
    ``content_sha256`` only.
    """
    try:
        return _replay(
            cache_dir,
            prompt=prompt,
            document=document,
            keys=(content_sha256, legacy_sha256 or ""),
            prompt_sha=prompt_sha256,
            model=model,
            source_ids=source_ids,
        )
    except CacheMiss as miss:
        # A missing Anthropic key stays on the replay path. A local server does not.
        if replay_only or (provider == "anthropic" and not api_key_configured()):
            return miss
    response_mode: str | None = None
    try:
        if provider == "openai_compatible":
            live = OpenAICompatibleProvider(
                model, base_url, temperature, api_key_env=api_key_env, reasoning_effort=reasoning_effort
            )
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
