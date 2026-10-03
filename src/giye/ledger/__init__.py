# SPDX-License-Identifier: MIT
"""Stage 3: ledger tables, CSV I/O with backups, permanent IDs and their retirement.

People are ``artists.csv``. A ``gy_id`` is never reused or renumbered. Activity
ids are uuid5 of a fixed namespace (the same string as the production archive).
A CV-derived row belongs to the owner of its CV source.
"""

from giye.ledger.ids import (
    ACTIVITY_NAMESPACE,
    activity_id_for,
    activity_id_key,
    allocate_gy_id,
    take_activity_id,
)
from giye.ledger.io import read_csv, write_csv
from giye.ledger.ledger import Ledger, cv_row_owner, repoint_cv_activities
from giye.ledger.schemas import (
    ACTIVITIES_FIELDS,
    ARTISTS_FIELDS,
    CV_SOURCES_FIELDS,
    GY_RETIRED_FIELDS,
    MEMBERSHIP_FIELDS,
    TABLES,
)

__all__ = [
    "ACTIVITIES_FIELDS",
    "ACTIVITY_NAMESPACE",
    "ARTISTS_FIELDS",
    "CV_SOURCES_FIELDS",
    "GY_RETIRED_FIELDS",
    "MEMBERSHIP_FIELDS",
    "TABLES",
    "Ledger",
    "activity_id_for",
    "activity_id_key",
    "allocate_gy_id",
    "cv_row_owner",
    "read_csv",
    "repoint_cv_activities",
    "take_activity_id",
    "write_csv",
]
