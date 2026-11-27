"""Database engine and sessions. The same code runs on PostgreSQL and SQLite."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from alembic.config import Config
from app.core.config import BACKEND_DIR

logger = logging.getLogger(__name__)


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return create_engine(url, pool_pre_ping=True, pool_size=8, max_overflow=8)
    if parsed.database:
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = make_engine(url)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    @property
    def backend(self) -> str:
        return self.engine.dialect.name

    def session(self) -> Session:
        return self.session_factory()

    def sessions(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session

    def migrate(self) -> None:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        config.attributes["database_url"] = self.url
        command.upgrade(config, "head")
        logger.info("Database schema is at head (%s)", self.backend)

    def dispose(self) -> None:
        self.engine.dispose()
