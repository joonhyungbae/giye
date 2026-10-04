# SPDX-License-Identifier: AGPL-3.0-only
"""Citation text for a published record.

The three strings are the ones the production site copies to the clipboard
(``CiteDialog``): APA, Chicago, and BibTeX. The author there is always
``기예 Giye``. Here the author, the public origin, and the dataset version come
from ``[publish]`` so another field can cite its own site. The sentence shape
is unchanged, including the BibTeX key prefix ``giye_``.
"""

from __future__ import annotations


def citation_texts(
    *,
    author: str,
    title: str,
    record_id: str | None,
    version: str,
    url: str,
    year: int,
    accessed: str,
) -> dict[str, str]:
    """APA, Chicago, and BibTeX for one record, or for the dataset when ``record_id`` is empty.

    ``accessed`` is an ISO date (``YYYY-MM-DD``). Production fills it with the
    viewer's local date at the moment they open the dialog; a snapshot has to
    choose one date, so the publisher uses the build date (UTC).
    """
    if record_id:
        bracket = f"[Artist record {record_id}, Dataset v{version}]"
        key = record_id.replace("-", "_")
    else:
        bracket = f"[Dataset v{version}]"
        key = "dataset"
    note = bracket[1:-1]
    bibtex = (
        f"@misc{{giye_{key},\n"
        f"  author       = {{{{{author}}}}},\n"
        f"  title        = {{{title}}},\n"
        f"  note         = {{{note}}},\n"
        f"  year         = {{{year}}},\n"
        f"  howpublished = {{\\url{{{url}}}}},\n"
        f"  urldate      = {{{accessed}}}\n"
        f"}}"
    )
    return {
        "apa": f"{author}. ({year}). {title} {bracket}. Retrieved {accessed}, from {url}",
        "chicago": f'{author}. "{title}." {bracket} {year}. Accessed {accessed}. {url}.',
        "bibtex": bibtex,
    }
