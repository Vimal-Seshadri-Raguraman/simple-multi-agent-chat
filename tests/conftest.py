import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

# Set the test database BEFORE any app imports: the lifespan's init_db() runs at
# TestClient boot and must never touch a developer's legacy smac.db.
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test-lifespan.db"

# Default the suite-wide posting rate limit high BEFORE any app imports (the
# module-level `post_limiter` in app/rate_limit.py is constructed at import
# time from this env var), so the ~190 unrelated tests can never trip it.
# The rate-limit tests themselves construct/monkeypatch a small limiter
# explicitly rather than relying on this default.
os.environ.setdefault("RATE_LIMIT_POSTS", "1000")

# Same rationale, for the agent-join redemption limiter (SMAC-92): it's
# keyed by client IP, and TestClient always presents as "testclient", so
# its budget is shared across every test in the whole suite unless
# defaulted high here. The dedicated rate-limit test monkeypatches its own
# small limiter instance, same pattern as RATE_LIMIT_POSTS above.
os.environ.setdefault("RATE_LIMIT_AGENT_JOIN", "1000")

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


def _create_account(client, email: str) -> dict:
    """POST /accounts: create a global account, returning its auth body
    (account + ACCOUNT-tier tokens). The one place every helper below
    bootstraps an account from, so account creation can't drift out of
    sync between founder_auth/member_auth."""
    response = client.post(
        "/accounts", json={"email": email, "password": _TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()


def founder_auth(client, key: str = "w1", visibility: str = "public") -> dict:
    """Found (once per client per key) a workspace via the real, account-authed
    POST /workspaces: creates a fresh account for f"{key}@test.example", then
    founds with it.

    Results are cached on the TestClient instance so repeated calls with the
    same key reuse one account/workspace/founder pair. Returns the same
    dict shape as before Identity v2, plus `account_id`/`account_token`
    (spec's binding conftest contract, SMAC-79 Task 2).
    """
    cache = getattr(client, "_founder_auth_cache", None)
    if cache is None:
        cache = {}
        client._founder_auth_cache = cache
    if key not in cache:
        account_body = _create_account(client, f"{key}@test.example")
        account_token = account_body["tokens"]["access_token"]
        response = client.post(
            "/workspaces",
            json={
                "workspace_name": f"{key}-workspace",
                "visibility": visibility,
                "display_first_name": "Test",
                "display_last_name": key,
            },
            headers={"Authorization": f"Bearer {account_token}"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        cache[key] = {
            "access_token": body["access_token"],
            "member_id": body["member"]["member_id"],
            "workspace_id": body["workspace"]["workspace_id"],
            "default_channel_id": None,
            "account_id": account_body["account"]["account_id"],
            "account_token": account_token,
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
    """Register (once per client per key) a fresh account for
    f"{key}@test.example" into a founder's public test workspace via the
    real, account-authed POST /workspaces/{id}/register.

    Results are cached on the TestClient instance so repeated calls with the
    same key reuse one account/member pair. Returns the same dict shape as
    before Identity v2, plus `account_id`/`account_token`.
    """
    cache = getattr(client, "_member_auth_cache", None)
    if cache is None:
        cache = {}
        client._member_auth_cache = cache
    if key not in cache:
        workspace_id = founder_auth(client, workspace_key)["workspace_id"]
        account_body = _create_account(client, f"{key}@test.example")
        account_token = account_body["tokens"]["access_token"]
        response = client.post(
            f"/workspaces/{workspace_id}/register",
            json={
                "first_name": "Test",
                "last_name": key,
                "display_name": f"Test {key}",
            },
            headers={"Authorization": f"Bearer {account_token}"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        cache[key] = {
            "access_token": body["access_token"],
            "member_id": body["member"]["member_id"],
            "workspace_id": body["workspace"]["workspace_id"],
            "default_channel_id": None,
            "account_id": account_body["account"]["account_id"],
            "account_token": account_token,
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


_SMAC_TUI_REPO_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    """Grab an ephemeral port from the OS so parallel test runs don't collide."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def real_smac_server(tmp_path_factory: pytest.TempPathFactory):
    """A real `smac-server` (real uvicorn, real migrations) for the TUI
    client's integration tests, spawned once per test module.

    Reuses Task 2's lifecycle manager (`smac_cli.server`, exercised via
    `python -m smac_cli.server` in `test_smac_server_lifecycle.py`)
    rather than reinventing server startup: a sync `httpx`-based client
    can't talk to the ASGI app in-process (`WSGITransport` doesn't speak
    ASGI), so hitting a real, separately-running server is the simplest
    correct way to integration-test `SmacApi`.

    Yields `(url, home_dir)`. `home_dir` is the tmp `$HOME` the server's
    pidfile/db/log live under -- tests that want `SmacApi`'s session
    auto-persistence (which resolves `~/.config/smac/session.json` via
    `smac_cli.paths.session_path()`) should monkeypatch `Path.home` to
    return it, the same pattern `test_smac_server_lifecycle.py` uses.
    """
    home_dir = tmp_path_factory.mktemp("smac-tui-home")
    port = _free_port()
    env = {**os.environ, "HOME": str(home_dir)}

    start = subprocess.run(
        [sys.executable, "-m", "smac_cli.server", "--start", "--port", str(port)],
        cwd=str(_SMAC_TUI_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert start.returncode == 0, start.stdout + start.stderr

    try:
        yield f"http://127.0.0.1:{port}", home_dir
    finally:
        subprocess.run(
            [sys.executable, "-m", "smac_cli.server", "--stop"],
            cwd=str(_SMAC_TUI_REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
