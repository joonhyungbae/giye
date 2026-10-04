# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path

from giye.config import load

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo" / "giye.toml"


def test_language_module_is_selected_from_the_config(tmp_path: Path) -> None:
    from giye.normalize.language import LanguageModule, load_language
    from giye.normalize.service import normalize

    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Toy language"
id_prefix = "GY"
[paths]
data = "{(tmp_path / "data").as_posix()}"
[normalize]
language_module = "tests.toy_language:Toy"
""",
        encoding="utf-8",
    )
    config = load(path)
    assert config.language_module == "tests.toy_language:Toy"
    language = load_language(config.language_module)
    assert isinstance(language, LanguageModule)
    assert language.name == "toy-qx"
    assert language.romanise("Qart") == "kart"
    result = normalize(config)
    assert '"language": "toy-qx"' in (result.processed / "manifest.json").read_text(encoding="utf-8")


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
