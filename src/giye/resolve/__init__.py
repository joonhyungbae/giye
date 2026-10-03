# SPDX-License-Identifier: MIT
"""Stage 4: same-person rules (E1–E4, X1), team guard (T1), merges."""

from giye.resolve.evidence import EVENT_WORDS, event_pattern, evidence_e1, url_key
from giye.resolve.service import ResolveResult, resolve, resolve_ledger
from giye.resolve.teams import expand_teams, person_like, team_like, team_person_mismatch

__all__ = [
    "EVENT_WORDS",
    "ResolveResult",
    "event_pattern",
    "evidence_e1",
    "expand_teams",
    "person_like",
    "resolve",
    "resolve_ledger",
    "team_like",
    "team_person_mismatch",
    "url_key",
]
