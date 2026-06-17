from pathlib import Path

from catena.config import Settings


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
