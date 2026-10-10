# SPDX-License-Identifier: AGPL-3.0-only
"""Career evidence bundle: aggregate tables a later MCP server can read.

What: builds ``data/career/<version>/`` from a Giye archive. The bundle is
counts, quantiles and shares for the roster layer (everyone on an admitted
programme) and the CV layer (people with an extracted public CV). It holds no
ledger row, no ``gy_id``, no name, no title and no URL of any person.

Why: the archive site records; the career service interprets. Disclosure
(D1–D5, ``k`` people per cell, a higher bar for shares) lives in the build, so
reading the bundle cannot recover one career. Rules and their reasons are
``giye.career.rules``.

How to run (the venv interpreter, from a checkout). Two sub-commands:

  .venv/bin/python -m giye.career build --config <giye.toml> --out <directory>
  .venv/bin/python -m giye.career holdout --config <giye.toml> --split-year T --out <directory>
"""

from giye.career.build import CareerBuildError, build

__all__ = ["CareerBuildError", "build"]
