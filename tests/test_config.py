import textwrap
from pathlib import Path

from catena.config import (
    CONFIG_ENV_VAR,
    Settings,
    config_search_paths,
    default_config_path,
    find_config_file,
    load_default_config_template,
)

_LLM_ENVS = (
    "LLM_GATEWAY_BASE_URL",
    "LLM_BASE_URL",
    "LLM_GATEWAY_API_KEY",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_EMBEDDING_MODEL",
    "LLM_EMBEDDING",
    "CATENA_DATA_DIR",
    "CATENA_EMBEDDING_BATCH_SIZE",
    "CATENA_TOP_K",
    "CATENA_LLM_TEMPERATURE",
    "CATENA_CELL_CONCURRENCY",
)


def _clear_llm_env(monkeypatch) -> None:
    for key in _LLM_ENVS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)


def _write(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_settings_blank_gateway_is_allowed(monkeypatch):
    monkeypatch.setenv("CATENA_DATA_DIR", ".tmp-catena")
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_EMBEDDING_MODEL", "")

    settings = Settings.from_env()

    assert settings.data_dir == Path(".tmp-catena")
    assert not settings.gateway_ready
    assert settings.sqlite_path == Path(".tmp-catena/catena.sqlite")


def test_explicit_config_env_var_is_searched_first(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = _write(
        tmp_path / "explicit.toml",
        """
        gateway_base_url = "https://gateway.example/v1"
        gateway_api_key = "key-from-file"
        llm_model = "gpt-x"
        embedding_model = "embed-x"
        """,
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config))

    assert find_config_file() == config
    settings = Settings.from_env()

    assert settings.gateway_base_url == "https://gateway.example/v1"
    assert settings.gateway_api_key == "key-from-file"
    assert settings.llm_model == "gpt-x"
    assert settings.embedding_model == "embed-x"
    assert settings.gateway_ready


def test_env_overrides_config_file(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _write(
        tmp_path / "explicit.toml",
        """
        gateway_base_url = "https://file.example/v1"
        gateway_api_key = "key-from-file"
        llm_model = "file-model"
        embedding_model = "file-embed"
        embedding_batch_size = 1
        top_k = 1
        llm_temperature = 0.25
        cell_concurrency = 2
        """,
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "explicit.toml"))

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "key-from-env")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_EMBEDDING", "env-embed")
    monkeypatch.setenv("CATENA_TOP_K", "42")
    monkeypatch.setenv("CATENA_CELL_CONCURRENCY", "4")

    settings = Settings.from_env()

    assert settings.gateway_base_url == "https://env.example/v1"
    assert settings.gateway_api_key == "key-from-env"
    assert settings.llm_model == "env-model"
    assert settings.embedding_model == "env-embed"
    # env beats file
    assert settings.top_k == 42
    assert settings.cell_concurrency == 4
    # file still supplies fields env left alone
    assert settings.embedding_batch_size == 1
    assert settings.llm_temperature == 0.25


def test_config_typed_values_are_coerced(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _write(
        tmp_path / "explicit.toml",
        """
        embedding_batch_size = 128
        top_k = 16
        llm_temperature = 0.1
        cell_concurrency = 3
        """,
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "explicit.toml"))

    settings = Settings.from_env()

    assert settings.embedding_batch_size == 128
    assert settings.top_k == 16
    assert settings.llm_temperature == 0.1
    assert settings.cell_concurrency == 3


def test_cell_concurrency_is_at_least_one(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _write(
        tmp_path / "explicit.toml",
        """
        cell_concurrency = 0
        """,
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "explicit.toml"))

    assert Settings.from_env().cell_concurrency == 1


def test_config_data_dir_expands_user(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(
        tmp_path / "explicit.toml",
        """
        data_dir = "~/catena-data"
        """,
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "explicit.toml"))

    settings = Settings.from_env()

    assert settings.data_dir == tmp_path / "catena-data"


def test_search_order_prefers_xdg_subdir_config_toml(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.chdir(tmp_path)  # ensures no cwd ./catena.toml interferes
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    xdg = tmp_path / "xdg"
    home = tmp_path / "home"
    (xdg / "catena").mkdir(parents=True)
    (home).mkdir(parents=True)

    # Populate every candidate path. The XDG/catena/config.toml must win.
    (xdg / "catena" / "config.toml").write_text('llm_model = "config.toml"\n')
    (xdg / "catena" / "catena.toml").write_text('llm_model = "catena.toml"\n')
    (xdg / "catena.toml").write_text('llm_model = "xdg-catena.toml"\n')
    (home / "catena.toml").write_text('llm_model = "home-catena.toml"\n')
    (home / ".catena.toml").write_text('llm_model = "dotcatena.toml"\n')

    found = find_config_file()
    assert found == xdg / "catena" / "config.toml"
    assert Settings.from_env().llm_model == "config.toml"


def test_search_order_falls_back_to_home_dotcatena(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    home = tmp_path / "home"
    home.mkdir()

    only = home / ".catena.toml"
    only.write_text('llm_model = "only-here"\n')

    assert find_config_file() == only
    assert Settings.from_env().llm_model == "only-here"


def test_config_search_paths_includes_documented_locations(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)

    paths = [str(p) for p in config_search_paths()]

    # cwd-local override
    assert "catena.toml" in paths
    # XDG-style locations
    assert str(tmp_path / "xdg" / "catena" / "config.toml") in paths
    assert str(tmp_path / "xdg" / "catena" / "catena.toml") in paths
    assert str(tmp_path / "xdg" / "catena.toml") in paths
    # home locations
    assert str(tmp_path / "home" / "catena.toml") in paths
    assert str(tmp_path / "home" / ".catena.toml") in paths


def test_require_gateway_message_mentions_config_file(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("CATENA_DATA_DIR", str(tmp_path / ".catena"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    try:
        Settings.from_env().require_gateway()
    except RuntimeError as exc:
        assert "config file" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected RuntimeError")


def test_default_config_path_respects_xdg(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert default_config_path() == tmp_path / "xdg" / "catena" / "config.toml"


def test_default_config_path_falls_back_to_home(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    assert default_config_path() == tmp_path / "home" / ".config" / "catena" / "config.toml"


def test_load_default_config_template_ships_documented_keys():
    template = load_default_config_template()
    assert "data_dir" in template
    assert "gateway_base_url" in template
    assert "llm_model" in template
    assert "embedding_model" in template
    assert "cell_concurrency" in template


def test_default_skills_dir_respects_env(monkeypatch, tmp_path):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("CATENA_SKILLS_DIR", str(tmp_path / "custom-skills"))
    from catena.config import default_skills_dir

    assert default_skills_dir() == tmp_path / "custom-skills"


def test_default_skills_dir_falls_back_to_home(monkeypatch, tmp_path):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CATENA_SKILLS_DIR", raising=False)
    from catena.config import default_skills_dir

    assert default_skills_dir() == tmp_path / "home" / ".agents" / "skills"


def test_load_skill_template_has_frontmatter():
    from catena.config import load_skill_template

    text = load_skill_template()
    assert text.startswith("---\n")
    assert "name: catena" in text
