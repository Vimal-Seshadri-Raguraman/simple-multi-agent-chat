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


_TEST_PASSWORD = "test-password-123"


def _human_auth(client, key: str) -> dict:
    """Register (once per client per key) a human via the real /auth/register.

    Results are cached on the TestClient instance so repeated calls with the
    same key reuse one member — mirroring how the old dev-header helper
    auto-created a member on first use and reused it afterwards.
    """
    cache = getattr(client, "_human_auth_cache", None)
    if cache is None:
        cache = {}
        client._human_auth_cache = cache
    if key not in cache:
        response = client.post(
            "/auth/register",
            json={
                "email": f"{key}@test.example",
                "password": _TEST_PASSWORD,
                "first_name": "Test",
                "last_name": key,
                "display_name": f"Test {key}",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        cache[key] = {
            "access_token": body["access_token"],
            "member_id": body["member"]["member_id"],
        }
    return cache[key]


def human_headers(client, key: str = "m_1") -> dict[str, str]:
    """Bearer auth headers for a test human; auto-registers on first use."""
    return {"Authorization": f"Bearer {_human_auth(client, key)['access_token']}"}


def human_member_id(client, key: str = "m_1") -> str:
    """The real member_id of a test human; auto-registers on first use."""
    return _human_auth(client, key)["member_id"]


def human_token(client, key: str = "m_1") -> str:
    """A raw access token for a test human (for WebSocket ?token= URLs)."""
    return _human_auth(client, key)["access_token"]


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
