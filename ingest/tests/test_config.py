import pytest

from recetario_ingest.config import Config, ConfigError, load_config
from recetario_ingest.cost import Pricing, cost_usd, estimate_tokens


def test_paths_resolve_against_the_config_file(tmp_path):
    (tmp_path / "recetario.toml").write_text('books_dir = "shelf"\ndb_path = "../x.db"\n')

    config = load_config(tmp_path / "recetario.toml")

    assert config.books_dir == tmp_path / "shelf"
    assert config.db_path == (tmp_path / ".." / "x.db")
    assert config.locale == "es-MX"


def test_llm_and_pricing_sections(tmp_path):
    (tmp_path / "c.toml").write_text(
        '[llm]\nmodel = "m1"\nmax_cost_usd = 2.5\n\n[pricing."m1"]\ninput_per_mtok = 3\noutput_per_mtok = 15\n'
    )

    config = load_config(tmp_path / "c.toml")

    assert (config.model, config.max_cost_usd) == ("m1", 2.5)
    assert config.pricing == {"m1": Pricing(3.0, 15.0)}


def test_defaults_when_there_is_no_file():
    assert Config().artifacts_dir.name == "artifacts"


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ("book_dir = 'x'", "unknown key 'book_dir'"),
        ("[llm]\nmodle = 'x'", r"unknown \[llm\] key 'modle'"),
        ("[pricing.m]\ninput_per_mtok = 1", "needs input_per_mtok and output_per_mtok"),
        ("books_dir = ", "c.toml"),
    ],
)
def test_bad_config_names_the_problem(tmp_path, toml, message):
    (tmp_path / "c.toml").write_text(toml)

    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path / "c.toml")


def test_missing_explicit_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_token_and_cost_estimates():
    assert estimate_tokens("a" * 401) == 101
    assert cost_usd(2_000_000, 500_000, Pricing(3.0, 15.0)) == pytest.approx(13.5)


def test_dotenv_fills_missing_variables_but_never_overrides(tmp_path, monkeypatch):
    from recetario_ingest.config import load_dotenv

    (tmp_path / ".env").write_text('# comment\nNEW_KEY="abc"\nKEPT=from-file\nEMPTY=\n')
    monkeypatch.setenv("KEPT", "from-shell")
    monkeypatch.delenv("NEW_KEY", raising=False)
    monkeypatch.delenv("EMPTY", raising=False)

    load_dotenv(tmp_path / ".env")

    import os

    assert (os.environ["NEW_KEY"], os.environ["KEPT"], "EMPTY" in os.environ) == ("abc", "from-shell", False)
    load_dotenv(tmp_path / "missing")  # no file is fine


def _config_with_profiles(tmp_path):
    (tmp_path / "c.toml").write_text(
        '[llm]\nprovider = "claude-code"\nmodel = "haiku"\nconcurrency = 2\n\n'
        '[profiles.haiku-api]\nprovider = "anthropic"\nmodel = "claude-haiku-5-5"\nconcurrency = 8\nmax_cost_usd = 1.5\n'
    )
    return load_config(tmp_path / "c.toml")


def test_a_profile_replaces_the_llm_settings_it_names(tmp_path):
    from recetario_ingest.config import choose_llm

    chosen = choose_llm(_config_with_profiles(tmp_path), profile="haiku-api")

    assert (chosen.provider, chosen.model, chosen.concurrency, chosen.max_cost_usd) == ("anthropic", "claude-haiku-5-5", 8, 1.5)


def test_flags_override_the_profile_and_a_new_provider_needs_its_own_model(tmp_path):
    from recetario_ingest.config import choose_llm

    config = _config_with_profiles(tmp_path)

    assert choose_llm(config, profile="haiku-api", workers=3).concurrency == 3
    assert choose_llm(config, provider="openrouter", model="deepseek/deepseek-chat").model == "deepseek/deepseek-chat"
    with pytest.raises(ConfigError, match="also give --model"):
        choose_llm(config, provider="anthropic")
    with pytest.raises(ConfigError, match="unknown profile 'nope'"):
        choose_llm(config, profile="nope")


def test_concurrency_must_be_a_positive_whole_number(tmp_path):
    (tmp_path / "c.toml").write_text("[llm]\nconcurrency = 0\n")

    with pytest.raises(ConfigError, match="concurrency"):
        load_config(tmp_path / "c.toml")
