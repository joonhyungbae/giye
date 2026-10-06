# SPDX-License-Identifier: AGPL-3.0-only
"""What an export leaves out about people: hidden by request, and CVs of the unpublished.

Why: the WARC and the RO-Crate are files that can be moved, zipped and
handed on. A person hidden by request (``HIDDEN_BY_REQUEST``) has a nameless
tombstone on the site; an export that still carried their name, ledger rows,
CV and the reason for hiding would undo that request. Person-level data are
shared only under a data-use agreement (docs/RULES.md), so by default an
export carries no more about a hidden person than the site does.

The rules (one data rule for both exports):

- **Hidden people.** A ledger row is hidden when its ``status`` is
  ``HIDDEN_BY_REQUEST``. Their tokens are the ledger id, the ledger ids merged
  into it (``merged LED-…`` in the note, and retirements pointing at it), and
  their names (``name_ko``, ``name_en``, ``aliases``). A gy_id alone is not a
  token: the site publishes it as a tombstone.
- A CSV row that holds a hidden person's ledger id or gy_id as a cell, or whose
  text contains a hidden token, is left out of the copy. Another text file
  (JSON, Markdown, HTML, TOML, YAML, plain text) that contains a hidden token
  is left out whole, because it cannot be cut by row. A binary file is not
  inspected; the only binaries an export copies are snapshot bodies and CVs,
  which the next two rules cover.
- **CVs.** CV files under ``raw/cv`` and kept captures of a CV URL are
  included only for people the site publishes (``published_ids``, the same set
  ``giye publish`` builds pages from). A CV URL without its text is still
  listed in the RO-Crate unless its owner is hidden.
- **Personal pages.** A kept capture of a hidden person's own link
  (``links.csv``) is left out.
- **Shared pages.** A capture of any other page (a programme's roster page)
  that writes a hidden person's name is kept by default, unredacted: it is the
  evidence for everyone else on it, and a redacted capture would no longer
  match its SHA-256. The export counts these pages and its metadata says they
  are kept. ``leave_out_shared_pages`` (``--leave-out-shared-pages``) leaves
  them out as well, and the metadata says so. A name is found in the decoded
  page text (charset as the collector reads it, HTML entities resolved, NFC);
  a name split by markup or shown only in an image is not found.

``include_hidden`` (``--include-hidden``) turns the hidden-people rules off and
the export's metadata says so. CVs of people who are not published stay out
either way. Why the wording and the flag (final software review, MINOR-4): the
metadata said "the files that name them" are left out, but shared roster pages
that list a hidden person were kept, so the sentence was not true.
"""

from __future__ import annotations

import csv
import html
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from giye.config import Config, ConfigError

HIDDEN = "HIDDEN_BY_REQUEST"
_MERGED = re.compile(r"merged\s+(LED-[0-9A-Za-z]+)")
_TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".html", ".htm", ".txt", ".toml", ".yml", ".yaml", ".tsv"}
# Shorter names would match inside unrelated words.
_MIN_NAME = 2

EXCLUDED_NOTE = (
    "People hidden by request are left out: their ledger rows, other text files of the data "
    "directory that name them, their CVs and captures of their own pages. Shared roster pages "
    "that also list them are kept unredacted, because they are the evidence for everyone else on "
    "them and a redacted capture would no longer match its hash (--leave-out-shared-pages leaves "
    "them out). CVs are included only for published people."
)
EXCLUDED_SHARED_NOTE = (
    "People hidden by request are left out: their ledger rows, other text files of the data "
    "directory that name them, their CVs, captures of their own pages, and captures of shared "
    "pages that name them (--leave-out-shared-pages). CVs are included only for published people."
)
INCLUDED_NOTE = (
    "This export includes people hidden by request (--include-hidden). It holds personal data that "
    "the public site does not show; share it only under a data-use agreement. "
    "CVs are included only for published people."
)


@dataclass
class Privacy:
    """The tokens and URLs an export leaves out, and what it left out."""

    include_hidden: bool = False
    leave_out_shared_pages: bool = False
    hidden_ids: set[str] = field(default_factory=set)
    hidden_gy: set[str] = field(default_factory=set)
    hidden_names: set[str] = field(default_factory=set)
    published: set[str] = field(default_factory=set)
    # CV source URL → ledger id of its owner.
    cv_owner: dict[str, str] = field(default_factory=dict)
    # source_id → ledger id of its owner.
    cv_source_owner: dict[str, str] = field(default_factory=dict)
    hidden_links: set[str] = field(default_factory=set)
    left_out_files: list[str] = field(default_factory=list)
    left_out_rows: int = 0
    left_out_captures: int = 0
    # Captures of shared pages that write a hidden person's name (kept, or left
    # out with ``leave_out_shared_pages``).
    shared_pages_naming_hidden: int = 0

    @property
    def note(self) -> str:
        """The sentence the export's metadata carries."""
        if self.include_hidden:
            return INCLUDED_NOTE
        return EXCLUDED_SHARED_NOTE if self.leave_out_shared_pages else EXCLUDED_NOTE

    def names_hidden(self, body: bytes, content_type: str = "") -> bool:
        """True when a capture's decoded text writes a hidden person's name or ledger id."""
        tokens = self.tokens
        if not tokens or not body:
            return False
        from giye.collect.charset import decode_body

        text = unicodedata.normalize("NFC", html.unescape(decode_body(body, content_type)))
        return any(token in text for token in tokens)

    def shared_capture_left_out(self, row: dict, body: bytes) -> bool:
        """Count a shared capture that names a hidden person; True when the flag leaves it out."""
        if not self.names_hidden(body, str(row.get("content_type") or "")):
            return False
        self.shared_pages_naming_hidden += 1
        return self.leave_out_shared_pages

    @property
    def tokens(self) -> set[str]:
        return set() if self.include_hidden else self.hidden_ids | self.hidden_names

    def private_url(self, url: str) -> bool:
        """True for a CV URL of a person who is not published, or a hidden person's own link."""
        if not url:
            return False
        if url in self.cv_owner and self.cv_owner[url] not in self.published:
            return True
        return not self.include_hidden and url in self.hidden_links

    def hidden_url(self, url: str) -> bool:
        """True for a CV URL or own link of a hidden person: not even listed as a URL."""
        if self.include_hidden or not url:
            return False
        return self.cv_owner.get(url) in self.hidden_ids or url in self.hidden_links

    def cv_file_allowed(self, path: Path, cv_root: Path) -> bool:
        """``raw/cv/<ledger id>/<source id>/…`` is copied only for a published owner."""
        try:
            parts = path.resolve().relative_to(cv_root.resolve()).parts
        except ValueError:
            return False
        if len(parts) < 2:
            return False
        owner = self.cv_source_owner.get(parts[1]) or parts[0]
        return owner in self.published

    def content(self, path: Path) -> bytes | None:
        """Bytes to copy for ``path``: filtered CSV rows, the file as is, or None to leave it out."""
        data = path.read_bytes()
        tokens = self.tokens
        if not tokens or path.suffix.lower() not in _TEXT_SUFFIXES:
            return data
        text = data.decode("utf-8", errors="replace")
        if not any(token in text for token in tokens):
            return data
        if path.suffix.lower() == ".csv":
            return self._filtered_csv(text)
        self.left_out_files.append(path.name)
        return None

    def _filtered_csv(self, text: str) -> bytes:
        reader = csv.reader(io.StringIO(text, newline=""))
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        ids = self.hidden_ids | self.hidden_gy
        for number, row in enumerate(reader):
            if number and (any(cell in ids for cell in row) or any(t in cell for cell in row for t in self.tokens)):
                self.left_out_rows += 1
                continue
            writer.writerow(row)
        return out.getvalue().encode("utf-8")


def privacy_for(config: Config, *, include_hidden: bool = False, leave_out_shared_pages: bool = False) -> Privacy:
    """Read the ledger and the frames file and build the export's ``Privacy``."""
    ledger = config.ledger
    artists = _rows(ledger / "artists.csv")
    hidden_rows = [row for row in artists if row.get("status") == HIDDEN]
    privacy = Privacy(include_hidden=include_hidden, leave_out_shared_pages=leave_out_shared_pages)
    privacy.hidden_ids = {row["ledger_id"] for row in hidden_rows if row.get("ledger_id")}
    for row in hidden_rows:
        privacy.hidden_ids.update(_MERGED.findall(row.get("reviewer_note") or ""))
        if row.get("gy_id"):
            privacy.hidden_gy.add(row["gy_id"])
        for name in (row.get("name_ko"), row.get("name_en"), *re.split(r"[|;]", row.get("aliases") or "")):
            name = unicodedata.normalize("NFC", name or "").strip()
            if len(name) >= _MIN_NAME:
                privacy.hidden_names.add(name)
    for row in _rows(ledger / "gy_retired.csv"):
        if row.get("merged_into_ledger_id") in privacy.hidden_ids and row.get("gy_id"):
            privacy.hidden_gy.add(row["gy_id"])
    privacy.published = _published(config, artists)
    for row in _rows(ledger / "cv_sources.csv"):
        owner = row.get("ledger_id") or ""
        for key in ("url", "fetch_url"):
            if row.get(key):
                privacy.cv_owner[row[key].strip()] = owner
        if row.get("source_id"):
            privacy.cv_source_owner[row["source_id"]] = owner
    for source in config.extract_sources:
        for url in (source.url, source.fetch_url):
            if url and url not in privacy.cv_owner:
                privacy.cv_owner[url] = source.ledger_id
    for row in _rows(ledger / "links.csv"):
        if row.get("ledger_id") in privacy.hidden_ids and row.get("url"):
            privacy.hidden_links.add(row["url"].strip())
    return privacy


def _published(config: Config, artists: list[dict[str, str]]) -> set[str]:
    """Ledger ids ``giye publish`` makes pages for. Empty when the frames file cannot be read."""
    from giye.config import checked_frames
    from giye.normalize.rules import published_ids
    from giye.publish.snapshot import _edition_resolver, _frames_document

    try:
        registry = checked_frames(config)
        frame_rows = list(_frames_document(config.frames).get("frames") or [])
    except (ConfigError, OSError, TypeError, ValueError):
        return set()
    decisions = {frame.code: frame.eligibility.decision for frame in registry.frames}
    edition_of = _edition_resolver(frame_rows, config.field_config)
    return published_ids(
        artists,
        _rows(config.ledger / "frame_membership.csv"),
        _rows(config.ledger / "scope.csv"),
        frame_rows,
        edition_of,
        decisions,
    )


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]
