# SPDX-License-Identifier: AGPL-3.0-only
"""Packaging metadata stays consistent with the source tree.

Every non-Python file under ``src/giye`` has to match a ``package-data``
pattern, or a wheel install loses it. The version string is written in four
places (pyproject, ``giye.__version__``, CITATION.cff, CHANGELOG) and has to
agree in all of them and in ``giye --version``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import giye

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"

pytestmark = pytest.mark.skipif(not PYPROJECT.is_file(), reason="needs the source tree")


def _pyproject() -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # Python 3.10
        import tomli as tomllib
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_every_data_file_is_package_data():
    patterns = _pyproject()["tool"]["setuptools"]["package-data"]["giye"]
    package = ROOT / "src" / "giye"
    covered = {path for pattern in patterns for path in package.glob(pattern) if path.is_file()}
    data = {
        path
        for path in package.rglob("*")
        if path.is_file() and path.suffix not in {".py", ".pyc"} and "__pycache__" not in path.parts
    }
    assert sorted(str(p.relative_to(package)) for p in data - covered) == []


def test_version_is_the_same_everywhere():
    version = _pyproject()["project"]["version"]
    assert giye.__version__ == version
    cff = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert re.search(rf"^version: {re.escape(version)}$", cff, re.MULTILINE)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}]" in changelog
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    out = subprocess.run(
        [sys.executable, "-c", "from giye.cli import main; main(['--version'])"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert out.stdout.strip() == f"giye {version}"
