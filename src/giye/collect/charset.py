# SPDX-License-Identifier: AGPL-3.0-only
"""Decode a fetched body to text: one rule for roster pages and CV text.

Why: older Korean institutional sites serve EUC-KR (in practice CP949) and
often state it only in ``<meta>``, or label it with a name Python does not
know (``ks_c_5601-1987``, ``x-windows-949``). Decoding those bytes as UTF-8
turns every Hangul syllable into U+FFFD while the digits survive, so a CV's
venues reach extraction and the grounding check as noise.

The order (a data rule, the same for every caller):

1. a byte-order mark (UTF-8, UTF-16) — unambiguous, as in the HTML standard;
2. the ``charset`` parameter of the HTTP ``Content-Type``;
3. ``<meta charset>`` or ``<meta http-equiv="Content-Type" content="...charset=...">``
   in the first 2048 bytes;
4. detection: strict UTF-8, then strict CP949;
5. UTF-8 with replacement characters.

A label is mapped before it is used: every Korean legacy label (``euc-kr``,
``ks_c_5601-1987``, ``x-windows-949``, ...) is CP949, a superset of EUC-KR
that also has the syllables Python's strict ``euc_kr`` codec rejects;
``utf8``/``utf8mb4`` are UTF-8; ``iso-8859-1``/``latin1``/``us-ascii`` are
windows-1252, as browsers read them. A label Python does not know is skipped
and the next step decides; it never raises.
"""

from __future__ import annotations

import codecs
import re

__all__ = ["declared_charset", "decode_body", "normalise_label"]

_KOREAN = {
    "euc-kr",
    "euc_kr",
    "euckr",
    "ks_c_5601-1987",
    "ks_c_5601-1989",
    "ks_c_5601",
    "ksc5601",
    "ksc_5601",
    "ks_x_1001",
    "korean",
    "csksc56011987",
    "csEUCKR".lower(),
    "iso-ir-149",
    "windows-949",
    "x-windows-949",
    "cp949",
    "ms949",
    "uhc",
}
_UTF8 = {"utf-8", "utf8", "utf8mb4", "unicode-1-1-utf-8", "x-unicode20utf8"}
_LATIN = {"iso-8859-1", "iso8859-1", "latin1", "latin-1", "l1", "us-ascii", "ascii", "cp1252", "windows-1252"}

_META = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:+-]+)""", re.IGNORECASE)
_PREFIX = 2048


def normalise_label(label: str) -> str | None:
    """Python codec name for a charset label, or None when the label is unknown."""
    text = (label or "").strip().strip("\"'").lower()
    if not text:
        return None
    if text in _KOREAN:
        return "cp949"
    if text in _UTF8:
        return "utf-8"
    if text in _LATIN:
        return "cp1252"
    try:
        return codecs.lookup(text).name
    except LookupError:
        return None


def declared_charset(content_type: str) -> str | None:
    """The codec named by a ``Content-Type`` header's ``charset``, or None."""
    for part in (content_type or "").split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "charset" and value.strip():
            return normalise_label(value)
    return None


def _bom(content: bytes) -> str | None:
    if content.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    return None


def _meta_charset(content: bytes) -> str | None:
    found = _META.search(content[:_PREFIX])
    if not found:
        return None
    return normalise_label(found.group(1).decode("ascii", errors="ignore"))


def decode_body(content: bytes | str, content_type: str = "") -> str:
    """Text of ``content`` under the order in the module docstring. Never raises."""
    if isinstance(content, str):
        return content
    content = bytes(content or b"")
    for codec in (_bom(content), declared_charset(content_type), _meta_charset(content)):
        if codec:
            try:
                return content.decode(codec, errors="replace")
            except LookupError:  # pragma: no cover - normalise_label checked it
                continue
    for codec in ("utf-8", "cp949"):
        try:
            return content.decode(codec)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")
