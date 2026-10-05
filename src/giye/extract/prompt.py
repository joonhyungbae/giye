# SPDX-License-Identifier: AGPL-3.0-only
"""The versioned CV extraction prompt.

``prompts/cv_extract_v1.txt`` is the packaged system prompt and the default.
A field file can name its
own prompt file (``[extract] prompt``), because the prompt describes the field.
The SHA-256 of the file bytes is stored on each cache record and on each
extraction file, so a changed or different prompt is a different cache key and
a reason to read the CV again; replay finds the record made with the same prompt.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PROMPT_FILE = "cv_extract_v1.txt"


def prompt_path(path: Path | None = None) -> Path:
    """``path`` (a field file's prompt) when given, else the packaged default prompt."""
    if path is not None:
        return Path(path)
    return Path(__file__).resolve().parent / "prompts" / PROMPT_FILE


def prompt_bytes(path: Path | None = None) -> bytes:
    """Raw bytes of the prompt file, including the trailing newline the hash covers."""
    return prompt_path(path).read_bytes()


def prompt_text(path: Path | None = None) -> str:
    """The prompt decoded as UTF-8, the system text sent to the model."""
    return prompt_bytes(path).decode("utf-8")


def prompt_sha256(path: Path | None = None) -> str:
    """SHA-256 of the prompt file bytes, including the trailing newline."""
    return hashlib.sha256(prompt_bytes(path)).hexdigest()
