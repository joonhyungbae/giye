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


def test_shipped_field_is_package_data():
    """The default field file resolves inside the installed package, not the repository root.

    A non-editable install has no ``fields/`` next to site-packages, so the file
    must be found through importlib.resources.
    """
    from importlib import resources

    import giye
    from giye.field import shipped_field, shipped_field_path

    path = shipped_field_path()
    assert path.is_file()
    assert path.resolve().is_relative_to(Path(giye.__file__).resolve().parent)
    assert path == Path(str(resources.files("giye") / "fields" / "korean-media-art" / "field.toml"))
    field = shipped_field()
    assert field.team_words
    assert field.edition_aliases


def test_cadence_is_published_only_when_declared(tmp_path: Path) -> None:
    import json

    from giye.publish import publish

    demo = load(DEMO)
    assert demo.cadence == {}
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Cadence"
id_prefix = "GY"
[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{(DEMO.parent / "frames.yml").as_posix()}"
[publish]
site_url = "https://example.org"
[publish.cadence]
weekly = "link check"
""",
        encoding="utf-8",
    )
    cfg = load(path)
    assert cfg.cadence == {"weekly": "link check"}
    publish(cfg)
    coverage = json.loads((cfg.site / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["cadence"] == {"weekly": "link check"}


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "giye.toml"
    path.write_text('[archive]\nname = "Synthetic"\n' + body, encoding="utf-8")
    return path


def test_user_agent_has_no_placeholder_default(tmp_path: Path) -> None:
    import pytest

    from giye.collect.fetch import ContactError, fetcher_from_config

    config = load(_write(tmp_path, ""))
    assert config.user_agent == ""
    with pytest.raises(ContactError):
        fetcher_from_config(config)


def test_unknown_collect_key_and_table_warn(tmp_path: Path) -> None:
    import pytest

    from giye.config import ConfigWarning

    body = '[collect]\nuser-agent = "Bot/1 (+mailto:ops@archive.test)"\nmin_delay = 30\n[colect]\nx = 1\n'
    with pytest.warns(ConfigWarning) as caught:
        load(_write(tmp_path, body))
    messages = " ".join(str(item.message) for item in caught)
    assert "user-agent" in messages and "min_delay" in messages and "[colect]" in messages


def test_wrong_types_and_bad_numbers_are_config_errors(tmp_path: Path) -> None:
    import pytest

    for body in ('collect = "x"\n', '[collect]\nuser_agent = 5\n', "[collect]\nmin_delay_s = -5\n",
                 "[collect]\ntimeout_s = 0\n"):
        path = tmp_path / "giye.toml"
        path.write_text(body + '[archive]\nname = "Synthetic"\n' if body.startswith("collect") else
                        '[archive]\nname = "Synthetic"\n' + body, encoding="utf-8")
        with pytest.raises((TypeError, ValueError)):
            load(path)


def test_cli_prints_one_line_for_a_wrong_table_type(tmp_path: Path, capsys) -> None:
    from giye.cli import main

    path = tmp_path / "giye.toml"
    path.write_text('paths = "oops"\n[archive]\nname = "Synthetic"\n', encoding="utf-8")
    assert main(["evidence", "--config", str(path)]) == 2
    assert capsys.readouterr().err.startswith("giye: error:")
