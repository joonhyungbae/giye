# SPDX-License-Identifier: MIT
from pathlib import Path

from giye.config import load

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo" / "giye.toml"


def test_demo_config_resolves_paths_relative_to_file():
    cfg = load(DEMO)
    assert cfg.name.startswith("Synthetic")
    assert cfg.ledger == DEMO.parent / "data" / "ledger"
    assert cfg.languages == ("ko", "en")
