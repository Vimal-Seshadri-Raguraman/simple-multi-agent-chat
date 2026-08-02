import os
from pathlib import Path
from typing import Generator

from alembic import command
from alembic.config import Config as AlembicConfig
from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./smac.db")
_is_sqlite = DATABASE_URL.startswith("sqlite")


def enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Attach a `PRAGMA foreign_keys=ON` listener to a SQLite engine.

    SQLite enforces foreign keys per-connection and defaults them off, so
    this has to run on every new DBAPI connection rather than once at
    startup. Shared by the production engine below and by the test-fixture
    engines in tests/conftest.py, so both exercise the same FK-cycle insert
    ordering the production engine does (see create_workspace's sequential
    flushes in app/routers/workspaces.py for why that matters).
    """

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:
    enable_sqlite_foreign_keys(engine)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(url: str) -> AlembicConfig:
    """Programmatic Alembic config pointing at this repo's migration scripts."""
    config = AlembicConfig(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def init_db() -> None:
    """Bring the database to the latest schema. Called once at app startup.

    Runs `alembic upgrade head`: builds a fresh database from the migration
    chain, no-ops on an up-to-date one, and applies pending revisions to an
    older one — users keep their data across upgrades.
    """
    command.upgrade(_alembic_config(DATABASE_URL), "head")
