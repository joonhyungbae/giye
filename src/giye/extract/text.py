# SPDX-License-Identifier: AGPL-3.0-only
"""CV URL kinds, text extraction, and the whitespace-free content hash.

The hash ignores whitespace so a re-wrapped page is not a new CV. HWP (OLE)
text is not read, and a column layout is not reconstructed in a browser: a
``.hwp`` body yields no text and the pull is recorded as a failure. HTML is
read with the standard library parser.
"""

from __future__ import annotations

import hashlib
import html
import io
import re
import subprocess
import tempfile
import zipfile
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_SKIP_TAGS = {"script", "style", "noscript"}
_BREAK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "li", "br", "tr", "section", "article"}


def detect_kind(url: str) -> str:
    """Classify a CV URL from the host and the path.

    ``web_layout`` is not inferred from the URL; a caller sets it when the
    years sit in a separate column. This release fetches that kind as ordinary
    HTML (no browser layout pass).
    """
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    if host == "drive.google.com":
        return "gdrive_folder" if "/folders/" in path else "gdrive_file"
    if host == "docs.google.com" and "/document/d/" in path:
        return "gdoc"
    if host == "docs.google.com" and "/spreadsheets/d/" in path:
        return "gsheet"
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith(".docx"):
        return "docx"
    if path.endswith(".hwp"):
        return "hwp"
    return "web"


def fetch_target(kind: str, url: str) -> str:
    """The URL to download. Share links are rewritten to the file export.

    robots.txt is checked on this rewritten URL, not on the share link,
    because that is the request that is sent.
    """
    if kind == "gdrive_file":
        match = re.search(r"/file/d/([A-Za-z0-9_-]+)", url)
        file_id = match.group(1) if match else (parse_qs(urlparse(url).query).get("id") or [None])[0]
        if not file_id:
            raise ValueError("drive file id not found in url")
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    if kind == "gdoc":
        match = re.search(r"/document/d/([A-Za-z0-9_-]+)", url)
        if not match:
            raise ValueError("google doc id not found in url")
        return f"https://docs.google.com/document/d/{match.group(1)}/export?format=pdf"
    if kind == "gsheet":
        match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
        if not match:
            raise ValueError("google sheet id not found in url")
        return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv"
    if "dropbox.com" in urlparse(url).netloc.lower():
        # A share link serves a preview page unless dl=1 asks for the file itself.
        rewritten = re.sub(r"([?&])dl=0", r"\1dl=1", url)
        if "dl=" not in url:
            rewritten += ("&" if "?" in url else "?") + "dl=1"
        return rewritten
    return url


def normalize(text: str) -> str:
    """Drop blank lines and zero-width characters; collapse spaces inside a line."""
    lines = [re.sub(r"[ \t]+", " ", ZERO_WIDTH.sub("", line)).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def fingerprint(text: str) -> str:
    """Content hash that ignores all whitespace, so re-wrapped lines are not a change."""
    collapsed = re.sub(r"\s+", "", ZERO_WIDTH.sub("", text))
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()


def bundle_fingerprint(documents: list[tuple[str, str]]) -> str:
    """Hash of every CV text in ``source_id`` order.

    The replay cache is keyed by this digest, the prompt digest, and the model.
    One changed CV changes the digest, so the previous response is not reused.
    Whitespace inside each text does not count, matching ``fingerprint``.
    """
    ordered = [text for _source_id, text in sorted(documents, key=lambda item: item[0])]
    return fingerprint("\n".join(ordered))


def looks_garbled(text: str) -> bool:
    """True when extracted text is too short or too little of it is letters.

    The provider accepts a string, so the PDF is still reduced to text. The
    pull records this flag when that text is nothing readable.
    """
    body = re.sub(r"\s+", "", text)
    if len(body) < 200:
        return True
    readable = len(re.findall(r"[가-힣A-Za-z0-9]", body))
    return readable / len(body) < 0.55


class _HTMLText(HTMLParser):
    """Visible text, with a newline at block tags. Skips script and style."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Skip script and style; break the line at a block tag."""
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BREAK_TAGS and not self._skip:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Leave a skipped region, or break the line when a block tag ends."""
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BREAK_TAGS and not self._skip:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        """Keep visible text. Script and style contents are dropped."""
        if not self._skip:
            self.parts.append(data)


def html_text(body: bytes | str) -> str:
    """Visible text of an HTML CV, with a newline where a block tag was."""
    raw = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    parser = _HTMLText()
    parser.feed(raw)
    return "".join(parser.parts)


def extract_text(body: bytes, ext: str) -> str:
    """Text of a downloaded CV. ``ext`` is the file kind (``html``, ``pdf``, …)."""
    if ext in ("html", "htm"):
        return html_text(body)
    if ext == "pdf":
        return _pdf_text(body)
    if ext == "docx":
        return _docx_text(body)
    if ext == "hwp":
        # HWP 5 is an OLE document. It is not read here, so the body stays empty.
        return ""
    if ext in ("txt", "md", "csv"):
        return body.decode("utf-8", errors="replace")
    return ""


def extension_for(content: bytes, content_type: str, kind: str) -> str:
    """Choose the snapshot extension from magic bytes, then the content type, then the kind."""
    if content[:5] == b"%PDF-":
        return "pdf"
    ctype = (content_type or "").lower()
    if "html" in ctype:
        return "html"
    if "csv" in ctype or kind == "gsheet":
        return "csv"
    if kind == "pdf":
        return "pdf"
    if kind == "docx":
        return "docx"
    if kind == "hwp":
        return "hwp"
    if kind in ("web", "web_layout"):
        return "html" if b"<html" in content[:500].lower() or b"<p" in content[:500].lower() else "txt"
    return "bin"


def _pdf_text(body: bytes) -> str:
    """``pdftotext -layout`` keeps the visual line breaks a CV uses. A missing binary raises ``FileNotFoundError``."""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(body)
        tmp.flush()
        out = subprocess.run(
            ["pdftotext", "-layout", tmp.name, "-"],
            capture_output=True,
            check=True,
        )
    return out.stdout.decode("utf-8", errors="replace")


def _docx_text(body: bytes) -> str:
    """Paragraphs from ``word/document.xml``. No extra dependency."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))
