# SPDX-License-Identifier: AGPL-3.0-only
"""Model providers: a live Anthropic call, a local OpenAI-compatible server, or a replay.

``Provider.complete(prompt, document)`` returns the raw response text. Tests
and the demo use ``ReplayProvider`` only. ``AnthropicProvider`` imports the
SDK inside ``complete``, and ``OpenAICompatibleProvider`` imports ``requests``
there too, so importing this module does not open a socket.

The replay cache is one JSON file per ``(content sha256, prompt sha256, model)``.
The file holds the raw response plus ``model``, ``prompt_sha256``,
``content_sha256``, ``created_at``, and ``temperature``. Temperature is
recorded and is not part of the key. The live call omits temperature unless
the config sets one, and two temperatures are not two extractions unless the
caller changes the model or the prompt. A local call also stores ``response_mode`` (which structured-
output shape the server accepted). That field is not part of the key either.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Protocol

from giye.ledger.io import write_text_atomic


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
    response_mode: str | None = None,
) -> Path:
    """Write one replay record. ``response`` is the raw model text, not a parsed object.

    ``response_mode`` is set by a local call (``json_schema``, ``json_object``,
    or ``none``). It is omitted for a hosted or hand-written record. It does
    not change the cache path.
    """
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "prompt_sha256": prompt_sha256,
        "content_sha256": content_sha256,
        "created_at": created_at,
        "temperature": temperature,
        "response": response,
    }
    if response_mode is not None:
        payload["response_mode"] = response_mode
    if synthetic:
        payload["synthetic"] = True
    path = cache_path(directory, content_sha256, prompt_sha256, model)
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


class ReplayProvider:
    """Return the stored raw response for one cache key. Does not call a model."""

    def __init__(self, directory: Path, *, content_sha256: str, prompt_sha256: str, model: str) -> None:
        self.directory = directory
        self.content_sha256 = content_sha256
        self.prompt_sha256 = prompt_sha256
        self.model = model

    def complete(self, prompt: str, document: str) -> str:
        """Return the stored raw response. ``prompt`` and ``document`` are ignored; the cache key already names them."""
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

    The request sends the configured model, the versioned prompt as the system
    text, and the extraction JSON schema as the structured output.
    ``temperature`` is sent only when the config sets one, so an omitted key
    does not change the model's default. Tests must not construct a call;
    a run without an API key uses ``ReplayProvider`` instead.
    """

    def __init__(self, model: str, temperature: float | None = None) -> None:
        self.model = model
        self.temperature = temperature

    def complete(self, prompt: str, document: str) -> str:
        """Call the model. Returns the raw text, which must be the extraction JSON."""
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


class OpenAICompatibleProvider:
    """Chat Completions on an OpenAI-compatible server (Ollama, vLLM, llama.cpp).

    One POST to ``{base_url}/chat/completions``. The system message is the
    versioned prompt and the user message is the CV text. ``temperature`` is
    sent only when set, matching ``AnthropicProvider``. The extraction schema
    is the same object ``AnthropicProvider`` sends, under
    ``response_format.type = json_schema`` with ``strict`` true.

    A server that rejects that shape answers HTTP 400. One retry asks for
    ``json_object`` and appends the schema JSON to the system prompt. If that
    attempt is also HTTP 400, one further retry omits ``response_format`` and
    sends the original system prompt. Any other HTTP error stops. The mode
    that returned is ``response_mode``: ``json_schema``, ``json_object``, or
    ``none``. Redirects are not followed, so the CV text stays on the host
    named by ``base_url``.

    The bearer token is ``api_key`` when that was passed, otherwise the
    environment variable named by ``api_key_env`` (default
    ``GIYE_LLM_API_KEY``). An empty value sends no Authorization header.
    A local server does not need a key.

    ``requests`` is imported inside ``complete``. Constructing this object
    does not open a socket, and neither does importing the module.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        temperature: float | None = None,
        api_key: str | None = None,
        timeout: float = 600,
        api_key_env: str = "GIYE_LLM_API_KEY",
        reasoning_effort: str | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.api_key = api_key
        self.timeout = timeout
        self.api_key_env = api_key_env
        self.reasoning_effort = reasoning_effort
        self.response_mode: str | None = None

    def complete(self, prompt: str, document: str) -> str:
        """POST one completion. Returns the raw text. Retries a rejected schema as JSON, then as plain text."""
        import requests

        from giye.extract.schema import Extraction

        schema = Extraction.model_json_schema()
        url = f"{self.base_url}/chat/completions"
        # json_schema first. json_object carries the schema in the prompt because
        # that mode has no schema field. The last try is unconstrained text.
        schema_text = json.dumps(schema, ensure_ascii=False)
        attempts: tuple[tuple[str, dict | None, str], ...] = (
            (
                "json_schema",
                {
                    "type": "json_schema",
                    "json_schema": {"name": "extraction", "schema": schema, "strict": True},
                },
                prompt,
            ),
            ("json_object", {"type": "json_object"}, prompt + "\n" + schema_text),
            ("none", None, prompt),
        )
        payload: dict = {"model": self.model}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        # Sent only when set, like temperature. "none" stops a thinking model
        # from writing its reasoning before the JSON (Ollama maps it to think=false).
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        headers = self._headers()

        def post(body: dict) -> requests.Response:
            """POST one attempt. A connection error becomes ``ProviderError``. Redirects are not followed."""
            try:
                # A redirect would repeat the CV text at another URL. Stay on base_url.
                return requests.post(
                    url, json=body, headers=headers, timeout=self.timeout, allow_redirects=False
                )
            except requests.RequestException as exc:
                raise ProviderError(f"connection error: {exc}") from exc

        response: requests.Response | None = None
        last = len(attempts) - 1
        for index, (mode, response_format, system) in enumerate(attempts):
            body = {
                **payload,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": document},
                ],
            }
            if response_format is not None:
                body["response_format"] = response_format
            response = post(body)
            # Only a 400 is "this response_format is refused". Other errors stop.
            if response.status_code == 400 and index != last:
                continue
            self.response_mode = mode
            break
        if response is None:
            raise ProviderError("empty body")
        if response.status_code >= 400:
            detail = response.text[:300].replace("\n", " ").strip()
            raise ProviderError(f"HTTP {response.status_code}: {detail}")
        if not response.content:
            raise ProviderError("empty body")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"HTTP {response.status_code}: response was not JSON") from exc
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            raise ProviderError("empty content")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise ProviderError("finish_reason is length")
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("empty content")
        return content

    def _headers(self) -> dict[str, str]:
        """Bearer from the constructor, or from the named environment variable."""
        if self.api_key is not None:
            token = self.api_key
        else:
            token = os.environ.get(self.api_key_env, "")
        token = token.strip()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}
