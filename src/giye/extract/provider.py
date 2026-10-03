# SPDX-License-Identifier: MIT
"""Model providers: a live Anthropic call, or a replay of a stored raw response.

``Provider.complete(prompt, document)`` returns the raw response text. Tests
and the demo use ``ReplayProvider`` only. ``AnthropicProvider`` imports the
SDK inside ``complete``, so importing this module does not import ``anthropic``
and does not open a socket.

The replay cache is one JSON file per ``(content sha256, prompt sha256, model)``.
The file holds the raw response plus ``model``, ``prompt_sha256``,
``content_sha256``, ``created_at``, and ``temperature``. Temperature is
recorded and is not part of the key: production's call does not set it, and
two temperatures are not two extractions unless the caller changes the model
or the prompt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol


class CacheMiss(Exception):
    """No stored response for this content hash, prompt hash, and model."""

    def __init__(self, content_sha256: str, prompt_sha256: str, model: str):
        self.content_sha256 = content_sha256
        self.prompt_sha256 = prompt_sha256
        self.model = model
        super().__init__(f"no replay for model={model} content={content_sha256[:12]} prompt={prompt_sha256[:12]}")


class ProviderError(Exception):
    """The provider returned nothing usable (refusal, truncation, or an empty body)."""


class Provider(Protocol):
    """One completion. ``prompt`` is the versioned instructions; ``document`` is the CV text."""

    def complete(self, prompt: str, document: str) -> str:
        """Return the raw model text, which must be the extraction JSON."""


def cache_path(directory: Path, content_sha256: str, prompt_sha256: str, model: str) -> Path:
    """File name for one cache key. The model is stripped to a safe path segment."""
    model_key = re.sub(r"[^A-Za-z0-9._-]+", "_", model)
    return directory / f"{content_sha256}__{prompt_sha256}__{model_key}.json"


def write_cache(
    directory: Path,
    *,
    content_sha256: str,
    prompt_sha256: str,
    model: str,
    response: str,
    temperature: float | None,
    created_at: str,
    synthetic: bool = False,
) -> Path:
    """Write one replay record. ``response`` is the raw model text, not a parsed object."""
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "prompt_sha256": prompt_sha256,
        "content_sha256": content_sha256,
        "created_at": created_at,
        "temperature": temperature,
        "response": response,
    }
    if synthetic:
        payload["synthetic"] = True
    path = cache_path(directory, content_sha256, prompt_sha256, model)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class ReplayProvider:
    """Return the stored raw response for one cache key. Does not call a model."""

    def __init__(self, directory: Path, *, content_sha256: str, prompt_sha256: str, model: str) -> None:
        self.directory = directory
        self.content_sha256 = content_sha256
        self.prompt_sha256 = prompt_sha256
        self.model = model

    def complete(self, prompt: str, document: str) -> str:
        path = cache_path(self.directory, self.content_sha256, self.prompt_sha256, self.model)
        if not path.is_file():
            raise CacheMiss(self.content_sha256, self.prompt_sha256, self.model)
        payload = json.loads(path.read_text(encoding="utf-8"))
        required = ("model", "prompt_sha256", "content_sha256", "created_at", "temperature", "response")
        missing = [key for key in required if key not in payload]
        if missing:
            raise CacheMiss(self.content_sha256, self.prompt_sha256, self.model)
        if (
            payload["model"] != self.model
            or payload["prompt_sha256"] != self.prompt_sha256
            or payload["content_sha256"] != self.content_sha256
            or not isinstance(payload["response"], str)
        ):
            raise CacheMiss(self.content_sha256, self.prompt_sha256, self.model)
        # ``document`` is the CV text sent to a live model. The key already names that text.
        del prompt, document
        return payload["response"]


class AnthropicProvider:
    """Live call. The SDK is imported here, not at module import.

    The request follows ``scripts/extract_cvs_llm.py``: the configured model,
    the versioned prompt as the system text, and the extraction JSON schema as
    the structured output. ``temperature`` is sent only when the config sets
    one. Production omitted the parameter. Tests must not construct a call;
    a run without an API key uses ``ReplayProvider`` instead.
    """

    def __init__(self, model: str, temperature: float | None = None) -> None:
        self.model = model
        self.temperature = temperature

    def complete(self, prompt: str, document: str) -> str:
        import anthropic

        from giye.extract.schema import Extraction

        client = anthropic.Anthropic()
        schema = Extraction.model_json_schema()
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 64000,
            "system": [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            "betas": ["server-side-fallback-2026-07-01"],
            "extra_body": {"fallbacks": "default"},
            "messages": [{"role": "user", "content": document}],
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        with client.beta.messages.stream(**kwargs) as stream:
            message = stream.get_final_message()
        if message.stop_reason == "refusal":
            raise ProviderError(f"refused: {getattr(message, 'stop_details', None)}")
        if message.stop_reason == "max_tokens":
            raise ProviderError("output hit max_tokens")
        text = next((block.text for block in message.content if block.type == "text"), "")
        if not text:
            raise ProviderError("empty model response")
        return text
