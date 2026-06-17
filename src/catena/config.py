from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables.

    LLM settings are optional at construction time so local DB-only commands such as
    `catena init`, `catena papers list`, and `catena table` can run before secrets are filled in.
    Methods that call the gateway should call `require_gateway()` first.
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
        data_dir = Path(os.environ.get("CATENA_DATA_DIR", ".catena")).expanduser()
        return cls(
            data_dir=data_dir,
            gateway_base_url=_blank_to_none(
                os.environ.get("LLM_GATEWAY_BASE_URL") or os.environ.get("LLM_BASE_URL")
            ),
            gateway_api_key=_blank_to_none(
                os.environ.get("LLM_GATEWAY_API_KEY") or os.environ.get("LLM_API_KEY")
            ),
            llm_model=_blank_to_none(os.environ.get("LLM_MODEL")),
            embedding_model=_blank_to_none(
                os.environ.get("LLM_EMBEDDING_MODEL") or os.environ.get("LLM_EMBEDDING")
            ),
            embedding_batch_size=int(os.environ.get("CATENA_EMBEDDING_BATCH_SIZE", "64")),
            top_k=int(os.environ.get("CATENA_TOP_K", "8")),
            llm_temperature=float(os.environ.get("CATENA_LLM_TEMPERATURE", "0")),
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
                + ". Fill them in mise.local.toml."
            )


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
