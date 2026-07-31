import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database_module
from app.database import get_db
from app.main import app
from app.models import Base


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    # The WebSocket route creates its own DB session via `SessionLocal()` directly
    # rather than the `get_db` FastAPI dependency, so the dependency override above
    # doesn't reach it. Patch the module-level SessionLocal too, for the duration
    # of the test, so WS and HTTP requests share the same in-memory test database.
    original_session_local = database_module.SessionLocal
    database_module.SessionLocal = testing_session_local
    try:
        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        database_module.SessionLocal = original_session_local


def human_headers(member_id: str, member_name: str = "Test Human") -> dict[str, str]:
    """Auth headers for a human member; auto-creates the member on first use."""
    return {"X-Dev-Member-Id": member_id, "X-Dev-Member-Name": member_name}


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session: Session = testing_session_local()
    try:
        yield session
    finally:
        session.close()
