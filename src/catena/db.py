from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine, select

from catena.config import Settings
from catena.models import DEFAULT_TABLE_NAME, ExtractionTable

ALEMBIC_SCRIPT_LOCATION = "catena:migrations"


def make_engine(settings: Settings) -> Engine:
    settings.ensure_dirs()
    return create_engine(settings.database_url, connect_args={"check_same_thread": False})


def init_db(engine: Engine) -> None:
    upgrade_db(engine)
    _ensure_default_table(engine)


def make_alembic_config(database_url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", ALEMBIC_SCRIPT_LOCATION)
    config.set_main_option("sqlalchemy.url", database_url or "sqlite:///.catena/catena.sqlite")
    return config


def upgrade_db(engine: Engine, revision: str = "head") -> None:
    config = make_alembic_config(str(engine.url))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def show_db_current(engine: Engine) -> None:
    config = make_alembic_config(str(engine.url))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.current(config)


def show_db_history() -> None:
    command.history(make_alembic_config())


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def _ensure_default_table(engine: Engine) -> None:
    with Session(engine, expire_on_commit=False) as session:
        default_table = session.exec(
            select(ExtractionTable).where(ExtractionTable.name == DEFAULT_TABLE_NAME)
        ).first()
        if default_table is None:
            session.add(
                ExtractionTable(
                    name=DEFAULT_TABLE_NAME,
                    description="Default extraction table",
                )
            )
            session.commit()
