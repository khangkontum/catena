from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_ENV_VAR = "CATENA_CONFIG"
"""Environment variable pointing at an explicit config file. Overrides discovery."""

DATA_DIR_DEFAULT = ".catena"
"""Default per-project data directory when nothing else is configured."""


def config_search_paths() -> list[Path]:
    """Ordered candidate config file locations.

    First existing file wins. The explicit ``CATENA_CONFIG`` override and the
    cwd-local ``./catena.toml`` take precedence over the XDG/home locations so a
    project can shadow a global config.
    """

    paths: list[Path] = []

    explicit = os.environ.get(CONFIG_ENV_VAR)
    if explicit:
        paths.append(Path(explicit).expanduser())

    paths.append(Path("catena.toml"))
    xdg_root = _xdg_config_root()

    paths.extend(
        [
            xdg_root / "catena" / "config.toml",
            xdg_root / "catena" / "catena.toml",
            xdg_root / "catena.toml",
            Path.home() / "catena.toml",
            Path.home() / ".catena.toml",
        ]
    )
    return paths


def find_config_file() -> Path | None:
    """Return the first existing config file from the search path, or ``None``."""

    for candidate in config_search_paths():
        if candidate.is_file():
            return candidate
    return None


def _xdg_config_root() -> Path:
    """``$XDG_CONFIG_HOME`` or ``~/.config``."""

    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg_config) if xdg_config else Path.home() / ".config"


def default_config_path() -> Path:
    """The primary location ``catena config init`` writes to.

    ``$XDG_CONFIG_HOME/catena/config.toml`` (default ``~/.config/catena/config.toml``).
    This is the first XDG-style entry in ``config_search_paths``.
    """

    return _xdg_config_root() / "catena" / "config.toml"


def load_default_config_template() -> str:
    """Return the packaged starter config template (``catena.toml.example``)."""

    from importlib import resources

    return (
        resources.files("catena")
        .joinpath("templates", "catena.toml.example")
        .read_text(encoding="utf-8")
    )


def default_skills_dir() -> Path:
    """Default root for installed agent skills.

    ``$CATENA_SKILLS_DIR`` if set, otherwise ``~/.agents/skills``.
    """

    configured = os.environ.get("CATENA_SKILLS_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".agents" / "skills"


def default_skill_install_path() -> Path:
    """Where ``catena skill install`` writes by default: ``<skills>/catena/SKILL.md``."""

    return default_skills_dir() / "catena" / "SKILL.md"


def load_skill_template() -> str:
    """Return the packaged agent skill template (``SKILL.md``)."""

    from importlib import resources

    return resources.files("catena").joinpath("templates", "SKILL.md").read_text(encoding="utf-8")


def load_config_file() -> tuple[Path | None, dict[str, Any]]:
    """Load the discovered config file.

    Returns a ``(path, data)`` pair. ``path`` is ``None`` when no file is found;
    ``data`` is an empty dict in that case. ``path`` is returned even though the
    caller may not need it, so ``catena config`` can report which file was loaded.
    """

    path = find_config_file()
    if path is None:
        return None, {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        return path, {}
    return path, data


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from a config file and environment variables.

    LLM settings are optional at construction time so local DB-only commands such as
    `catena init`, `catena papers list`, and `catena table` can run before secrets are filled in.
    Methods that call the gateway should call `require_gateway()` first.

    Resolution order for every field (highest priority first):

    1. Environment variables (``LLM_*``, ``CATENA_*``). Keeps the existing
       ``mise.local.toml`` ``[env]`` workflow working.
    2. The discovered TOML config file (``~/.config/catena/config.toml`` and
       friends; see ``find_config_file``).
    3. Hard-coded defaults.
    """

    data_dir: Path
    gateway_base_url: str | None = None
    gateway_api_key: str | None = None
    llm_model: str | None = None
    embedding_model: str | None = None
    embedding_batch_size: int = 64
    top_k: int = 8
    llm_temperature: float = 0.0

    @classmethod
    def from_env(cls) -> Settings:
        _config_path, config = load_config_file()

        data_dir = _resolve_data_dir(config)
        return cls(
            data_dir=data_dir,
            gateway_base_url=_first_env_or_config(
                ("LLM_GATEWAY_BASE_URL", "LLM_BASE_URL"),
                "gateway_base_url",
                config,
            ),
            gateway_api_key=_first_env_or_config(
                ("LLM_GATEWAY_API_KEY", "LLM_API_KEY"),
                "gateway_api_key",
                config,
            ),
            llm_model=_first_env_or_config(("LLM_MODEL",), "llm_model", config),
            embedding_model=_first_env_or_config(
                ("LLM_EMBEDDING_MODEL", "LLM_EMBEDDING"),
                "embedding_model",
                config,
            ),
            embedding_batch_size=_resolve_int(
                "CATENA_EMBEDDING_BATCH_SIZE", "embedding_batch_size", config, 64
            ),
            top_k=_resolve_int("CATENA_TOP_K", "top_k", config, 8),
            llm_temperature=_resolve_float(
                "CATENA_LLM_TEMPERATURE", "llm_temperature", config, 0.0
            ),
        )

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.sqlite_path}"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "catena.sqlite"

    @property
    def lancedb_uri(self) -> Path:
        return self.data_dir / "lancedb"

    @property
    def papers_dir(self) -> Path:
        return self.data_dir / "papers"

    @property
    def gateway_ready(self) -> bool:
        return bool(
            self.gateway_base_url
            and self.gateway_api_key
            and self.llm_model
            and self.embedding_model
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        self.lancedb_uri.mkdir(parents=True, exist_ok=True)

    def require_gateway(self) -> None:
        missing = []
        if not self.gateway_base_url:
            missing.append("LLM_GATEWAY_BASE_URL")
        if not self.gateway_api_key:
            missing.append("LLM_GATEWAY_API_KEY")
        if not self.llm_model:
            missing.append("LLM_MODEL")
        if not self.embedding_model:
            missing.append("LLM_EMBEDDING_MODEL")
        if missing:
            raise RuntimeError(
                "Missing gateway settings: "
                + ", ".join(missing)
                + ". Provide them in a config file (see `catena config`) or mise.local.toml."
            )


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _first_env_or_config(
    env_keys: tuple[str, ...], config_key: str, config: dict[str, Any]
) -> str | None:
    for key in env_keys:
        if key in os.environ:
            return _blank_to_none(os.environ.get(key))
    return _blank_to_none(_config_str(config, config_key))


def _config_str(config: dict[str, Any], key: str) -> str | None:
    value = config.get(key)
    if isinstance(value, str):
        return value
    return None


def _resolve_data_dir(config: dict[str, Any]) -> Path:
    env_value = _blank_to_none(os.environ.get("CATENA_DATA_DIR"))
    if env_value is not None:
        return Path(env_value).expanduser()
    configured = _config_str(config, "data_dir")
    if configured:
        return Path(configured).expanduser()
    return Path(DATA_DIR_DEFAULT)


def _resolve_int(env_key: str, config_key: str, config: dict[str, Any], default: int) -> int:
    env_value = _blank_to_none(os.environ.get(env_key))
    if env_value is not None:
        return int(env_value)
    raw = config.get(config_key)
    if isinstance(raw, bool):
        return default
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped:
            return int(stripped)
    return default


def _resolve_float(env_key: str, config_key: str, config: dict[str, Any], default: float) -> float:
    env_value = _blank_to_none(os.environ.get(env_key))
    if env_value is not None:
        return float(env_value)
    raw = config.get(config_key)
    if isinstance(raw, bool):
        return default
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped:
            return float(stripped)
    return default
