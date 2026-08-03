"""`smac_cli.api` (`SmacApi` + `Session`) and `smac_cli.errors`.

Three layers, cheapest-first:

1. Error-mapping + refresh-on-401 + connection-failure, all via
   `httpx.MockTransport` -- no network, no real server.
2. `Session` save/load, including the chmod 600 permission check.
3. Real-server integration: `SmacApi` against an actual spawned
   `smac-server` (the `real_smac_server` fixture in `tests/conftest.py`),
   exercising the full register/login/whoami/channels/post/mark_read
   round trip.

Plus one drift tripwire (`smac_cli.CLIENT_VERSION == app.__version__`)
that's allowed to import `app` even though `smac_cli` itself never does.
"""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

import httpx
import pytest

import smac_cli
from smac_cli.api import Session, SmacApi
from smac_cli.errors import (
    AuthError,
    NameTakenError,
    NotAMemberError,
    NotFoundError,
    RateLimitedError,
    SessionExpired,
    SmacError,
    Unreachable,
    ValidationError,
)
from smac_cli.paths import session_path

_TEST_PASSWORD = "test-password-123"


def _unique(prefix: str) -> str:
    """A short, collision-resistant name/email-local-part for shared-server tests."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _envelope_transport(
    status_code: int, code: str, message: str
) -> httpx.MockTransport:
    """A transport that answers every request with the same error envelope."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code, json={"error": {"code": code, "message": message}}
        )

    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------
# Error mapping (unit-level, MockTransport)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code, expected_class",
    [
        ("unauthorized", AuthError),
        ("invalid_credentials", AuthError),
        ("invalid_token", AuthError),
        ("not_found", NotFoundError),
        ("invalid_invite", NotFoundError),
        ("not_a_member", NotAMemberError),
        ("rate_limited", RateLimitedError),
        ("workspace_name_taken", NameTakenError),
        ("channel_name_taken", NameTakenError),
        ("email_taken", NameTakenError),
        ("handle_taken", NameTakenError),
        ("invalid_message", ValidationError),
        ("confirmation_required", ValidationError),
        ("forbidden_member_type", SmacError),  # unmapped -> default base class
        ("totally_unknown_future_code", SmacError),
    ],
)
def test_error_envelope_maps_to_expected_class(
    code: str, expected_class: type[SmacError]
) -> None:
    message = f"message for {code}"
    api = SmacApi("http://fake.test", transport=_envelope_transport(400, code, message))

    with pytest.raises(expected_class) as exc_info:
        api.meta()

    error = exc_info.value
    assert type(error) is expected_class
    assert error.code == code
    assert error.message == message
    assert str(error) == message


def test_connection_failure_raises_unreachable_with_exact_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    api = SmacApi("http://fake.test", transport=httpx.MockTransport(handler))

    with pytest.raises(Unreachable) as exc_info:
        api.meta()

    assert (
        str(exc_info.value)
        == "SMAC server is not reachable at http://fake.test — run: smac-server --start"
    )


# --------------------------------------------------------------------------
# Session save/load
# --------------------------------------------------------------------------


def test_session_save_sets_chmod_600(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    session = Session(
        url="http://x.test",
        workspace_id="w1",
        access_token="a",
        refresh_token="r",
        email="e@test.example",
    )

    session.save(path)

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_session_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    original = Session(
        url="http://x.test",
        workspace_id="w1",
        access_token="a",
        refresh_token="r",
        email="e@test.example",
    )
    original.save(path)

    loaded = Session.load(path)

    assert loaded == original


def test_session_load_missing_file_returns_none(tmp_path: Path) -> None:
    assert Session.load(tmp_path / "does-not-exist.json") is None


def test_session_load_corrupt_json_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text("not json at all {{{")

    assert Session.load(path) is None


def test_session_load_missing_field_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text('{"url": "http://x.test"}')  # missing required fields

    assert Session.load(path) is None


def test_session_save_creates_file_via_os_open_with_no_perms_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding C: the file must be created with mode 0o600 from its very
    first byte (`os.open(..., O_CREAT, 0o600)`), not `write_text` then
    `chmod` -- the latter briefly creates the file at the process umask's
    default perms before the chmod call lands. Spying on `os.open` proves
    the fix uses the atomic-at-creation path rather than merely asserting
    the end state (which the old, vulnerable code also satisfied)."""
    path = tmp_path / "session.json"
    observed_create_modes: list[int] = []
    real_open = os.open

    def spy_open(file: object, flags: int, mode: int = 0o777) -> int:
        if flags & os.O_CREAT:
            observed_create_modes.append(mode)
        return real_open(file, flags, mode)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", spy_open)
    session = Session(
        url="http://x.test",
        workspace_id="w1",
        access_token="a",
        refresh_token="r",
        email="e@test.example",
    )

    session.save(path)

    assert observed_create_modes == [0o600]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --------------------------------------------------------------------------
# Refresh-on-401 (unit-level, MockTransport)
# --------------------------------------------------------------------------


def _seed_session(home_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    session = Session(
        url="http://fake.test",
        workspace_id="w1",
        access_token="stale-access",
        refresh_token="stale-refresh",
        email="a@test.example",
    )
    session.save(session_path())
    return session


def test_refresh_on_401_then_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _seed_session(tmp_path, monkeypatch)
    calls = {"channels": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh":
            assert request.headers.get("Authorization") is None
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-access",
                    "refresh_token": "fresh-refresh",
                    "token_type": "bearer",
                    "expires_in": 900,
                },
            )
        if request.url.path == "/workspaces/w1/channels":
            calls["channels"] += 1
            if calls["channels"] == 1:
                assert request.headers["Authorization"] == "Bearer stale-access"
                return httpx.Response(
                    401, json={"error": {"code": "invalid_token", "message": "expired"}}
                )
            assert request.headers["Authorization"] == "Bearer fresh-access"
            return httpx.Response(
                200, json=[{"channel_id": "c1", "channel_name": "general"}]
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    api = SmacApi(
        "http://fake.test", session=session, transport=httpx.MockTransport(handler)
    )

    result = api.channels()

    assert result == [{"channel_id": "c1", "channel_name": "general"}]
    assert calls["channels"] == 2  # exactly one retry
    assert api.session is not None
    assert api.session.access_token == "fresh-access"
    assert api.session.refresh_token == "fresh-refresh"
    reloaded = Session.load(session_path())
    assert reloaded is not None
    assert reloaded.access_token == "fresh-access"


def test_401_then_refresh_itself_fails_raises_session_expired_and_deletes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_session(tmp_path, monkeypatch)
    assert session_path().exists()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh":
            return httpx.Response(
                401,
                json={"error": {"code": "invalid_token", "message": "refresh expired"}},
            )
        if request.url.path == "/workspaces/w1/channels":
            return httpx.Response(
                401, json={"error": {"code": "invalid_token", "message": "expired"}}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    session = Session.load(session_path())
    assert session is not None
    api = SmacApi(
        "http://fake.test", session=session, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(SessionExpired):
        api.channels()

    assert api.session is None
    assert not session_path().exists()


def test_401_then_refresh_succeeds_but_retry_still_401_raises_session_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_session(tmp_path, monkeypatch)
    calls = {"channels": 0, "refresh": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh":
            calls["refresh"] += 1
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-access",
                    "refresh_token": "fresh-refresh",
                    "token_type": "bearer",
                    "expires_in": 900,
                },
            )
        if request.url.path == "/workspaces/w1/channels":
            calls["channels"] += 1
            return httpx.Response(
                401, json={"error": {"code": "invalid_token", "message": "still bad"}}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    session = Session.load(session_path())
    assert session is not None
    api = SmacApi(
        "http://fake.test", session=session, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(SessionExpired):
        api.channels()

    assert calls["channels"] == 2  # original + exactly one retry, no loop
    assert calls["refresh"] == 1  # exactly one refresh attempt
    assert api.session is None
    assert not session_path().exists()


def test_401_then_refresh_succeeds_but_session_nulled_concurrently_raises_session_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding J: `self.session` can be nulled by a concurrent force-expiry
    (another thread's own failed refresh invalidating this shared
    `SmacApi` instance) in the narrow window between `_refresh()` returning
    successfully here and the retry reading `self.session.access_token`.
    Before the guard this crashed with `AttributeError`; it must now raise
    the same clean `SessionExpired` a normal failed refresh would.
    """
    _seed_session(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/workspaces/w1/channels":
            return httpx.Response(
                401, json={"error": {"code": "invalid_token", "message": "expired"}}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    session = Session.load(session_path())
    assert session is not None
    api = SmacApi(
        "http://fake.test", session=session, transport=httpx.MockTransport(handler)
    )

    def fake_refresh() -> None:
        # Simulate a concurrent thread's own (successful, from ITS point of
        # view) redemption of the same rotating token, followed by a force
        # -expiry that nulls the shared session -- without ever making a
        # real network call here, since the point under test is purely
        # `_authed_request`'s handling of `self.session` being `None`
        # right after `_refresh()` returns.
        api.session = None

    monkeypatch.setattr(api, "_refresh", fake_refresh)

    with pytest.raises(SessionExpired):
        api.channels()


def test_ws_channel_url_refreshes_and_embeds_fresh_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_session(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/refresh"
        return httpx.Response(
            200,
            json={
                "access_token": "fresh-ws-token",
                "refresh_token": "fresh-refresh",
                "token_type": "bearer",
                "expires_in": 900,
            },
        )

    session = Session.load(session_path())
    assert session is not None
    api = SmacApi(
        "http://fake.test", session=session, transport=httpx.MockTransport(handler)
    )

    url = api.ws_channel_url("c1")

    assert url == "ws://fake.test/ws/workspaces/w1/channels/c1?token=fresh-ws-token"


def test_ws_events_url_refreshes_and_embeds_fresh_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_session(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/refresh"
        return httpx.Response(
            200,
            json={
                "access_token": "fresh-ws-token",
                "refresh_token": "fresh-refresh",
                "token_type": "bearer",
                "expires_in": 900,
            },
        )

    session = Session.load(session_path())
    assert session is not None
    api = SmacApi(
        "http://fake.test", session=session, transport=httpx.MockTransport(handler)
    )

    url = api.ws_events_url()

    assert (
        url == "ws://fake.test/ws/workspaces/w1/members/me/events?token=fresh-ws-token"
    )


# --------------------------------------------------------------------------
# Real-server integration
# --------------------------------------------------------------------------


def test_register_found_then_whoami_channels_post_mark_read_round_trip(
    real_smac_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, home_dir = real_smac_server
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    api = SmacApi(url)
    email = f"{_unique('founder')}@test.example"

    session = api.register_found(
        email=email,
        password=_TEST_PASSWORD,
        first_name="Ada",
        last_name="Lovelace",
        workspace_name=_unique("wksp"),
        visibility="private",
    )

    assert session.workspace_id
    assert session.access_token
    assert session_path().exists()
    assert stat.S_IMODE(session_path().stat().st_mode) == 0o600

    me = api.whoami()
    assert me["email"] == email

    channels = api.channels()
    assert any(c["channel_name"] == "general" for c in channels)
    general = next(c for c in channels if c["channel_name"] == "general")

    posted = api.post(general["channel_id"], "hello world")
    assert posted["Message"]["message_text"] == "hello world"

    unreads_before = api.unreads()
    general_row = next(
        r for r in unreads_before["unreads"] if r["channel_id"] == general["channel_id"]
    )
    assert general_row["unread_count"] == 0  # own post already advances the cursor

    marked = api.mark_read(general["channel_id"])
    assert marked["channel_id"] == general["channel_id"]
    assert marked["unread_count"] == 0


def test_discover_then_login_flow(
    real_smac_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, home_dir = real_smac_server
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    email = f"{_unique('discoverable')}@test.example"

    founding_api = SmacApi(url)
    founding_api.register_found(
        email=email,
        password=_TEST_PASSWORD,
        first_name="Grace",
        last_name="Hopper",
        workspace_name=_unique("wksp"),
        visibility="private",
    )
    assert founding_api.session is not None
    workspace_id = founding_api.session.workspace_id

    fresh_api = SmacApi(url)
    matches = fresh_api.discover(email, _TEST_PASSWORD)
    assert any(w["workspace_id"] == workspace_id for w in matches)

    session = fresh_api.login(workspace_id, email, _TEST_PASSWORD)

    assert session.workspace_id == workspace_id
    assert fresh_api.whoami()["email"] == email


def test_register_into_public_workspace(
    real_smac_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, home_dir = real_smac_server
    monkeypatch.setattr(Path, "home", lambda: home_dir)

    founder_api = SmacApi(url)
    founder_api.register_found(
        email=f"{_unique('founder')}@test.example",
        password=_TEST_PASSWORD,
        first_name="Ada",
        last_name="Lovelace",
        workspace_name=_unique("wksp"),
        visibility="public",
    )
    assert founder_api.session is not None
    workspace_id = founder_api.session.workspace_id

    joiner_api = SmacApi(url)
    joiner_email = f"{_unique('joiner')}@test.example"
    session = joiner_api.register_into(
        workspace_id, joiner_email, _TEST_PASSWORD, "Alan", "Turing"
    )

    assert session.workspace_id == workspace_id
    assert joiner_api.whoami()["email"] == joiner_email


def test_search_public_finds_founded_public_workspace(
    real_smac_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, home_dir = real_smac_server
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    workspace_name = _unique("searchable")

    api = SmacApi(url)
    api.register_found(
        email=f"{_unique('founder')}@test.example",
        password=_TEST_PASSWORD,
        first_name="Ada",
        last_name="Lovelace",
        workspace_name=workspace_name,
        visibility="public",
    )

    results = SmacApi(url).search_public(workspace_name)

    assert any(w["workspace_name"] == workspace_name for w in results)


# --------------------------------------------------------------------------
# CLIENT_VERSION drift tripwire (this test only -- may import `app`)
# --------------------------------------------------------------------------


def test_client_version_matches_server_version() -> None:
    import app

    assert smac_cli.CLIENT_VERSION == app.__version__
