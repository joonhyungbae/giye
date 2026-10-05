# SPDX-License-Identifier: AGPL-3.0-only
"""Split a long CV into pieces that each fit a model call.

A local context (about 32k tokens) cannot hold a long CV and a JSON reply of
several hundred rows. The share of rows kept falls as the file grows. The
split is a data rule: the same CV is cut the same way every run, whatever
model reads the pieces.
"""

from __future__ import annotations

import re

# A section title is a short line. A wrapped activity line is longer, and a
# dated line is an entry, not the section it sits under. The marker below
# carries the section so a later piece can still choose cv_section (a paper
# under PUBLICATIONS stays a publication).
_HEADING_MAX = 60
_YEAR = re.compile(r"(19|20)\d{2}")


def split_cv(text: str, max_chars: int) -> list[str]:
    """Split ``text`` into pieces of at most ``max_chars`` characters.

    ``max_chars == 0``, or a CV that already fits, returns ``[text]``
    unchanged. The bytes are the input's, so a replay cache keyed on the
    whole CV still hits.

    Otherwise paragraphs (runs of non-empty lines, separated by one or more
    whitespace-only lines) are packed greedily. A paragraph longer than
    ``max_chars`` is packed the same way at line boundaries. A single line
    longer than ``max_chars`` is kept whole: a line is never cut.

    The size limit applies to that CV text. A continuation marker is prefixed
    afterwards and is not part of the budget. Counting it would move the cut
    whenever an earlier heading changed length.

    A line is a heading when, stripped, it is non-empty, at most 60
    characters, and contains no four-digit year (``(19|20)`` then two digits).
    Each piece after the first starts with ``[continued; last heading:
    <heading>]`` and a newline. ``<heading>`` is the stripped text of the last
    heading line that appeared before that piece's first line. The marker is
    omitted when no heading has appeared yet. Section context is what decides
    ``cv_section``: a paper that falls in a later piece is still under
    PUBLICATIONS.

    Every original non-empty line appears in exactly one piece, in order.
    """
    if max_chars == 0 or len(text) <= max_chars:
        return [text]
    paragraphs = _paragraphs(text)
    if not paragraphs:
        return [text]
    return _with_headings(_pack(paragraphs, max_chars))


def _paragraphs(text: str) -> list[list[str]]:
    """Non-empty lines grouped at blank-line boundaries. The line text is kept."""
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line)
        elif current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    return paragraphs


def _render_blocks(blocks: list[list[str]]) -> str:
    return "\n\n".join("\n".join(lines) for lines in blocks)


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and len(stripped) <= _HEADING_MAX and _YEAR.search(stripped) is None


def _pack(paragraphs: list[list[str]], max_chars: int) -> list[list[list[str]]]:
    """Greedy pieces. Each piece is a list of paragraphs, each a list of lines."""
    chunks: list[list[list[str]]] = []
    current: list[list[str]] = []

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = []

    for para in paragraphs:
        if len("\n".join(para)) <= max_chars:
            if current and len(_render_blocks(current + [para])) > max_chars:
                flush()
            current.append(list(para))
            continue
        # The paragraph itself does not fit. Fill the open piece with as
        # many of its lines as will go, then continue in the next piece.
        new_paragraph = True
        for line in para:
            if not current:
                if len(line) <= max_chars:
                    current.append([line])
                else:
                    chunks.append([[line]])
                new_paragraph = False
                continue
            if new_paragraph:
                trial = current + [[line]]
            else:
                trial = current[:-1] + [current[-1] + [line]]
            if len(_render_blocks(trial)) <= max_chars:
                current = trial
                new_paragraph = False
                continue
            flush()
            new_paragraph = False
            if len(line) <= max_chars:
                current = [[line]]
            else:
                chunks.append([[line]])
    flush()
    return chunks


def _with_headings(chunks: list[list[list[str]]]) -> list[str]:
    last_heading: str | None = None
    rendered: list[str] = []
    for index, blocks in enumerate(chunks):
        body = _render_blocks(blocks)
        if index > 0 and last_heading is not None:
            body = f"[continued; last heading: {last_heading}]\n{body}"
        rendered.append(body)
        for block in blocks:
            for line in block:
                if _is_heading(line):
                    last_heading = line.strip()
    return rendered
