# SPDX-License-Identifier: MIT
from pathlib import Path

from giye.config import load

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo" / "giye.toml"


def test_demo_config_resolves_paths_relative_to_file():
    cfg = load(DEMO)
    assert cfg.name.startswith("Synthetic")
    assert cfg.ledger == DEMO.parent / "data" / "ledger"
    assert cfg.work == DEMO.parent / "data" / "work"
    assert cfg.languages == ("ko", "en")
    assert cfg.collector_modules == ("collectors.py",)
    assert "https://example.org/contact" in cfg.user_agent
    roots = dict(cfg.offline_roots)
    assert (roots["https://example.org"] / "robots.txt").is_file()
