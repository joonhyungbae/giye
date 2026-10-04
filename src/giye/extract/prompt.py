# SPDX-License-Identifier: AGPL-3.0-only
"""The versioned CV extraction prompt.

``prompts/cv_extract_v1.txt`` is the production system prompt
(``extract_cvs_llm.py``, ``SYSTEM``). The SHA-256 of the file bytes is stored
on each cache record and on each extraction file, so a changed prompt is a
different cache key and a reason to read the CV again.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PROMPT_FILE = "cv_extract_v1.txt"


def prompt_path() -> Path:
    """Path of the packaged prompt. It ships with the code, not with an archive."""
    return Path(__file__).resolve().parent / "prompts" / PROMPT_FILE


def prompt_bytes() -> bytes:
    return prompt_path().read_bytes()


def prompt_text() -> str:
    return prompt_bytes().decode("utf-8")


def prompt_sha256() -> str:
    """SHA-256 of the prompt file bytes, including the trailing newline."""
    return hashlib.sha256(prompt_bytes()).hexdigest()
