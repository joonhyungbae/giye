# SPDX-License-Identifier: AGPL-3.0-only
"""CV source registry.

One row is appended per location. The same URL for the same person is not
added twice (two members of a duo may still share a page: the match is per
``ledger_id``, not global). The id is ``CV-<ledger_id>-<lang>``, with ``-2``,
``-3``, … when that id is taken.

Crawling sites for a CV link is out of scope. Registering a row is what writes
``cv_sources.csv``. A team row is refused here unless ``allow_team`` is set,
because a member's personal CV must not be stored as the team's (rule T1,
``giye.resolve.teams``). ``team=`` on a person is not a team row.
"""

from __future__ import annotations

from giye.extract.text import detect_kind
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import CV_SOURCES_FIELDS, empty_row
from giye.normalize.language import language_for
from giye.resolve.teams import team_like


def register(
    ledger: Ledger,
    ledger_id: str,
    lang: str,
    url: str,
    *,
    kind: str | None = None,
    note: str = "",
    fetch_url: str = "",
    source_id: str = "",
    allow_team: bool = False,
) -> tuple[str, bool]:
    """Append a CV location. Returns ``(source_id, added)``.

    ``url`` is where the artist publishes the CV. ``fetch_url``, when the page
    renders its text from somewhere else, is what pull downloads instead.

    ``source_id`` overrides the generated id. The demo cache names its sources
    in advance (``CV-DEMO-…``) because a ledger id issued at collect time is
    random and could not be written into a hand-made response. Leave
    ``source_id`` empty to generate ``CV-<ledger_id>-<lang>``.
    """
    if lang not in ("ko", "en", "mixed"):
        raise ValueError(f"lang must be ko, en, or mixed (got {lang!r})")
    artists = {row["ledger_id"]: row for row in ledger.read("artists")}
    artist = artists.get(ledger_id)
    if artist is None:
        raise KeyError(ledger_id)
    reason = team_like(
        artist, words=ledger.config.field_config.compiled_team_words(), language=language_for(ledger.config)
    )
    if reason and not allow_team:
        raise ValueError(
            f"skip team row ({reason}): {ledger_id} {artist.get('name_ko') or artist.get('name_en')} ← {url}. "
            "Confirm this page is the team's own CV, then register with allow_team."
        )
    rows = ledger.read("cv_sources")
    for row in rows:
        if row["url"] == url and row["ledger_id"] == ledger_id:
            return row["source_id"], False
    if source_id:
        taken_row = next((row for row in rows if row["source_id"] == source_id), None)
        if taken_row is not None:
            raise ValueError(f"source_id {source_id} is already registered")
        assigned = source_id
    else:
        assigned = _next_source_id(rows, ledger_id, lang)
    rows.append(
        empty_row(
            CV_SOURCES_FIELDS,
            source_id=assigned,
            ledger_id=ledger_id,
            lang=lang,
            kind=kind or detect_kind(fetch_url or url),
            url=url,
            fetch_url=fetch_url,
            active="true",
            note=note,
        )
    )
    ledger.write("cv_sources", rows, task="register-cv")
    return assigned, True


def _next_source_id(rows: list[dict[str, str]], ledger_id: str, lang: str) -> str:
    base = f"CV-{ledger_id}-{lang}"
    assigned, n = base, 2
    taken = {row["source_id"] for row in rows}
    while assigned in taken:
        assigned, n = f"{base}-{n}", n + 1
    return assigned
