import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database_module
from app.database import enable_sqlite_foreign_keys, get_db
from app.main import app
from app.models import Base


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    enable_sqlite_foreign_keys(engine)
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


def founder_auth(client, key: str = "w1", visibility: str = "public") -> dict:
    """Found (once per client per key) a workspace via the real POST /workspaces.

    Results are cached on the TestClient instance so repeated calls with the
    same key reuse one workspace/founder pair.
    """
    cache = getattr(client, "_founder_auth_cache", None)
    if cache is None:
        cache = {}
        client._founder_auth_cache = cache
    if key not in cache:
        response = client.post(
            "/workspaces",
            json={
                "workspace_name": f"{key}-workspace",
                "visibility": visibility,
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
            "workspace_id": body["workspace"]["workspace_id"],
            "default_channel_id": None,
        }
    return cache[key]


def founder_headers(
    client, key: str = "w1", visibility: str = "public"
) -> dict[str, str]:
    """Bearer auth headers for a test workspace founder; founds on first use."""
    return {
        "Authorization": f"Bearer {founder_auth(client, key, visibility)['access_token']}"
    }


def member_auth(client, key: str, workspace_key: str = "w1") -> dict:
    """Register (once per client per key) f"{key}@test.example" into a founder's
    public test workspace via POST /workspaces/{id}/register.

    Results are cached on the TestClient instance so repeated calls with the
    same key reuse one member.
    """
    cache = getattr(client, "_member_auth_cache", None)
    if cache is None:
        cache = {}
        client._member_auth_cache = cache
    if key not in cache:
        workspace_id = founder_auth(client, workspace_key)["workspace_id"]
        response = client.post(
            f"/workspaces/{workspace_id}/register",
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
            "workspace_id": body["workspace"]["workspace_id"],
            "default_channel_id": None,
        }
    return cache[key]


def member_headers(client, key: str, workspace_key: str = "w1") -> dict[str, str]:
    """Bearer auth headers for a test workspace member; registers on first use."""
    return {
        "Authorization": f"Bearer {member_auth(client, key, workspace_key)['access_token']}"
    }


def member_token(client, key: str, workspace_key: str = "w1") -> str:
    """A raw access token for a test workspace member (for WebSocket ?token= URLs)."""
    return member_auth(client, key, workspace_key)["access_token"]


def general_channel_id(client, workspace_key: str = "w1") -> str:
    """The 'general' channel id of a founded test workspace; lists + caches it."""
    founder = founder_auth(client, workspace_key)
    if founder["default_channel_id"] is None:
        response = client.get(
            f"/workspaces/{founder['workspace_id']}/channels",
            headers={"Authorization": f"Bearer {founder['access_token']}"},
        )
        assert response.status_code == 200, response.text
        general = [c for c in response.json() if c["channel_name"] == "general"][0]
        founder["default_channel_id"] = general["channel_id"]
    return founder["default_channel_id"]


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    enable_sqlite_foreign_keys(engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session: Session = testing_session_local()
    try:
        yield session
    finally:
        session.close()
