# SPDX-License-Identifier: AGPL-3.0-only
"""Plain HTML pages, one per published person, from a site snapshot.

The website itself is the TanStack application under ``web/``. This renderer
is the archive demo: it does not rank anyone, and every fact keeps the source
link the snapshot stored. A hidden id gets a tombstone with no name. A retired
id gets a page that points at the survivor.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from giye.config import Config


def render(config: Config) -> list[Path]:
    """Write ``<site>/html/index.html`` and one page per id.

    Returns the paths, sorted. Hidden ids get a tombstone and retired ids get
    a redirect. Nobody is ranked, and each fact keeps its source link.
    """
    from giye.config import checked_frames

    # The pages come from the snapshot. The frames file is still checked here
    # so a bad file fails before render, the same way publish fails.
    checked_frames(config)
    site = config.site
    artists = _read(site / "artists.json")
    if not isinstance(artists, list):
        raise TypeError(f"{site / 'artists.json'} must be a list")
    activities = _read(site / "activities.json")
    background = _read(site / "background.json")
    links = _read(site / "links.json")
    collaborations = _read(site / "collaborations.json")
    frames = _read(site / "frames.json")
    stubs = _read(site / "artist_stubs.json")
    redirects = _read(site / "gy_redirects.json")
    citations = _read(site / "citations.json") if (site / "citations.json").is_file() else {}
    out = site / "html"
    out.mkdir(parents=True, exist_ok=True)
    frame_by_code = {row["code"]: row for row in frames if isinstance(row, dict) and row.get("code")}
    by_artist: dict[str, list] = {}
    for row in activities:
        by_artist.setdefault(row.get("artist_id") or "", []).append(row)
    bg_by_artist: dict[str, list] = {}
    for row in background:
        bg_by_artist.setdefault(row.get("artist_id") or "", []).append(row)
    link_by_artist: dict[str, list] = {}
    for row in links:
        link_by_artist.setdefault(row.get("artist_id") or "", []).append(row)
    collab_by_artist: dict[str, list] = {}
    for row in collaborations:
        collab_by_artist.setdefault(row.get("artist_id") or "", []).append(row)
    cite_by_id = {}
    if isinstance(citations, dict):
        for row in citations.get("artists") or []:
            if isinstance(row, dict) and row.get("id"):
                cite_by_id[row["id"]] = row
    written: list[Path] = []
    index_items: list[str] = []
    for artist in artists:
        gy = str(artist.get("id") or "")
        path = out / f"{gy}.html"
        path.write_text(
            _person_page(
                artist,
                activities=by_artist.get(gy, []),
                background=bg_by_artist.get(gy, []),
                links=link_by_artist.get(gy, []),
                collaborations=collab_by_artist.get(gy, []),
                frames=frame_by_code,
                citation=cite_by_id.get(gy),
                archive=config.name,
            ),
            encoding="utf-8",
        )
        written.append(path)
        name = artist.get("name_ko") or gy
        href = html.escape(gy, quote=True)
        index_items.append(f'<li><a href="{href}.html">{html.escape(str(name))}</a> ({html.escape(gy)})</li>')
    for gy, state in stubs.items():
        path = out / f"{gy}.html"
        path.write_text(_tombstone(str(gy), str(state), archive=config.name), encoding="utf-8")
        written.append(path)
    for old, new in redirects.items():
        path = out / f"{old}.html"
        path.write_text(_redirect(str(old), str(new), archive=config.name), encoding="utf-8")
        written.append(path)
    dataset = citations.get("dataset") if isinstance(citations, dict) else None
    index = out / "index.html"
    index.write_text(
        _index(config.name, index_items, dataset if isinstance(dataset, dict) else None, len(stubs)),
        encoding="utf-8",
    )
    written.append(index)
    written.sort()
    return written


def _read(path: Path) -> object:
    """Load one snapshot JSON file. A missing file means publish has not been run."""
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing; run giye publish first")
    return json.loads(path.read_text(encoding="utf-8"))


def _page(title: str, archive: str, body: str) -> str:
    """HTML document shell shared by every page."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{html.escape(title)}</title>\n"
        "</head>\n"
        "<body>\n"
        f"<p>{html.escape(archive)}</p>\n"
        f"{body}\n"
        '<p><a href="index.html">Index</a></p>\n'
        "</body>\n"
        "</html>\n"
    )


def _source(url: str | None, collected: str | None) -> str:
    """A source link and the collection date, or empty when the row has no URL."""
    if not url:
        return ""
    when = f" (collected {html.escape(str(collected))})" if collected else ""
    return f' <a href="{html.escape(str(url), quote=True)}">source</a>{when}'


def _roster_parts(artist: dict, frames: dict) -> list[str]:
    """Roster heading and the edition list. An empty roster says so."""
    editions = []
    for edition in artist.get("frame_editions") or []:
        code = edition.get("frame") or ""
        frame = frames.get(code) or {}
        label = frame.get("name_ko") or frame.get("name_en") or code
        year = edition.get("edition") or ""
        role = edition.get("role") or ""
        text = " ".join(part for part in (str(label), str(year), str(role)) if part)
        editions.append("<li>" + html.escape(text) + _source(frame.get("source_url"), None) + "</li>")
    body = "<ul>\n" + "\n".join(editions) + "\n</ul>" if editions else "<p>No roster edition.</p>"
    return ["<h2>Roster</h2>", body]


def _collaboration_parts(collaborations: list) -> list[str]:
    """Collaboration heading and rows. Empty input adds nothing."""
    if not collaborations:
        return []
    rows = []
    for row in collaborations:
        who = row.get("name_ko") or row.get("name_en") or ""
        text = " ".join(part for part in (str(row.get("year") or ""), str(who), str(row.get("topic") or "")) if part)
        rows.append("<li>" + html.escape(text) + _source(row.get("source_url"), row.get("collected_at")) + "</li>")
    return ["<h2>Collaborations</h2>", "<ul>\n" + "\n".join(rows) + "\n</ul>"]


def _link_parts(links: list) -> list[str]:
    """Link heading and anchors. Empty input adds nothing."""
    if not links:
        return []
    parts = ["<h2>Links</h2><ul>"]
    for row in links:
        url = str(row.get("url") or "")
        label = str(row.get("label") or url)
        parts.append(f'<li><a href="{html.escape(url, quote=True)}">{html.escape(label)}</a></li>')
    parts.append("</ul>")
    return parts


def _person_page(
    artist: dict,
    *,
    activities: list,
    background: list,
    links: list,
    collaborations: list,
    frames: dict,
    citation: dict | None,
    archive: str,
) -> str:
    """One person's page: name, roster, activities, and the optional sections."""
    gy = str(artist.get("id") or "")
    name = str(artist.get("name_ko") or gy)
    parts = [f"<h1>{html.escape(name)}</h1>", f"<p>{html.escape(gy)}</p>"]
    if artist.get("name_en"):
        parts.append(f"<p>{html.escape(str(artist['name_en']))}</p>")
    aliases = artist.get("aliases") or []
    if aliases:
        parts.append("<p>" + html.escape(", ".join(str(item) for item in aliases)) + "</p>")
    parts.append("<p>Record" + _source(artist.get("source_url"), artist.get("collected_at")) + "</p>")
    parts.extend(_roster_parts(artist, frames))
    parts.append("<h2>Activities</h2>")
    parts.append(_facts(activities))
    if background:
        parts.append("<h2>Background</h2>")
        parts.append(_facts(background))
    parts.extend(_collaboration_parts(collaborations))
    parts.extend(_link_parts(links))
    same = artist.get("same_name") or []
    if same:
        joined = ", ".join(html.escape(str(item)) for item in same)
        parts.append(f"<p>Open same-name review: {joined}</p>")
    if citation and citation.get("apa"):
        parts.append("<h2>Cite</h2>")
        parts.append(f"<p>{html.escape(str(citation['apa']))}</p>")
    return _page(f"{name} ({gy})", archive, "\n".join(parts))


def _facts(rows: list) -> str:
    """A list of dated facts, each with its source link. Empty says ``None.``"""
    if not rows:
        return "<p>None.</p>"
    items = []
    for row in rows:
        bits = [str(row.get("year") or ""), str(row.get("title") or "")]
        if row.get("venue"):
            bits.append(str(row["venue"]))
        if row.get("role"):
            bits.append(str(row["role"]))
        if row.get("section"):
            bits.append(str(row["section"]))
        text = html.escape(" — ".join(bit for bit in bits if bit))
        items.append("<li>" + text + _source(row.get("source_url"), row.get("collected_at")) + "</li>")
    return "<ul>\n" + "\n".join(items) + "\n</ul>"


def _tombstone(gy: str, state: str, *, archive: str) -> str:
    """A hidden id keeps its URL and drops the name. The stub records the state."""
    body = (
        f"<h1>{html.escape(gy)}</h1>\n"
        f"<p>This permanent id is {html.escape(state)}. No name and no records are published.</p>"
    )
    return _page(gy, archive, body)


def _redirect(old: str, new: str, *, archive: str) -> str:
    """A retired id points at the survivor's current page."""
    href = html.escape(new, quote=True)
    body = (
        f"<h1>{html.escape(old)}</h1>\n"
        f'<p>This id now refers to <a href="{href}.html">{html.escape(new)}</a>.</p>'
    )
    return _page(old, archive, body)


def _index(archive: str, items: list[str], dataset: dict | None, stubs: int) -> str:
    """Index of published people, plus the archive citation when the snapshot has one."""
    body = ["<h1>People</h1>", "<ul>", *items, "</ul>"]
    if stubs:
        body.append(f"<p>{stubs} permanent id(s) are withheld or withdrawn. Their pages carry no name.</p>")
    if dataset and dataset.get("apa"):
        body.append("<h2>Cite this archive</h2>")
        body.append(f"<p>{html.escape(str(dataset['apa']))}</p>")
    return _page(archive, archive, "\n".join(body))
