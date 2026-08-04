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
    NoWorkspaceError,
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
        ("workspace_token_required", AuthError),
        ("account_token_required", AuthError),
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
        email="a@test.example",
        account_access_token="stale-account-access",
        account_refresh_token="stale-account-refresh",
        workspace_id="w1",
        access_token="stale-access",
        refresh_token="stale-refresh",
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
    `SmacApi` instance) in the narrow window between `_recover_workspace_
    session()` returning successfully here and the retry reading
    `self.session.access_token`. Before the guard this crashed with
    `AttributeError`; it must now raise the same clean `SessionExpired` a
    normal failed refresh would.
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

    def fake_recover() -> None:
        # Simulate a concurrent thread's own (successful, from ITS point of
        # view) redemption of the same rotating token, followed by a force
        # -expiry that nulls the shared session -- without ever making a
        # real network call here, since the point under test is purely
        # `_authed_request`'s handling of `self.session` being `None`
        # right after `_recover_workspace_session()` returns.
        api.session = None

    monkeypatch.setattr(api, "_recover_workspace_session", fake_recover)

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
# Identity v2 (SMAC-79 Task 3): NoWorkspaceError + the account-refresh
# fallback tier of the workspace 401 recovery chain.
# --------------------------------------------------------------------------


def test_workspace_call_with_no_workspace_entered_raises_no_workspace_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account-only session (fresh off `/register`, no workspace token
    minted yet) must never even attempt an HTTP call for a workspace-tier
    method -- and must never be mistaken for `SessionExpired` (the
    account itself is perfectly valid)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    session = Session(
        url="http://fake.test",
        email="a@test.example",
        account_access_token="aat",
        account_refresh_token="art",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    api = SmacApi(
        "http://fake.test", session=session, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(NoWorkspaceError):
        api.channels()


def test_401_falls_back_to_account_refresh_and_remints_workspace_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Binding refresh chain (brief): workspace refresh -> account-refresh
    fallback -> re-mint via `POST /workspaces/{id}/token` -> retry. Here
    the WORKSPACE refresh token is dead (already rotated/expired) but the
    ACCOUNT token is still good, so the caller stays logged in without a
    fresh `/login`."""
    session = _seed_session(tmp_path, monkeypatch)
    calls = {"channels": 0, "workspace_refresh": 0, "account_refresh": 0, "mint": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh":
            import json as _json

            presented = _json.loads(request.content)["refresh_token"]
            if presented == "stale-refresh":
                calls["workspace_refresh"] += 1
                return httpx.Response(
                    401,
                    json={"error": {"code": "invalid_token", "message": "dead"}},
                )
            assert presented == "stale-account-refresh"
            calls["account_refresh"] += 1
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-account-access",
                    "refresh_token": "fresh-account-refresh",
                    "token_type": "bearer",
                    "expires_in": 900,
                },
            )
        if request.url.path == "/workspaces/w1/token":
            calls["mint"] += 1
            assert request.headers["Authorization"] == "Bearer fresh-account-access"
            return httpx.Response(
                200,
                json={
                    "access_token": "reminted-access",
                    "refresh_token": "reminted-refresh",
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
            assert request.headers["Authorization"] == "Bearer reminted-access"
            return httpx.Response(
                200, json=[{"channel_id": "c1", "channel_name": "general"}]
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    api = SmacApi(
        "http://fake.test", session=session, transport=httpx.MockTransport(handler)
    )

    result = api.channels()

    assert result == [{"channel_id": "c1", "channel_name": "general"}]
    assert calls == {
        "channels": 2,
        "workspace_refresh": 1,
        "account_refresh": 1,
        "mint": 1,
    }
    assert api.session is not None
    assert api.session.access_token == "reminted-access"
    assert api.session.account_access_token == "fresh-account-access"


def test_401_with_both_refresh_tokens_dead_raises_session_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both tiers of the fallback exhausted: the account refresh token is
    ALSO dead, so there's nothing left but `SessionExpired` -- the saved
    session is wiped, same as the pre-Identity-v2 single-tier failure."""
    _seed_session(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh":
            return httpx.Response(
                401, json={"error": {"code": "invalid_token", "message": "dead"}}
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


# --------------------------------------------------------------------------
# Real-server integration
# --------------------------------------------------------------------------


def test_signup_then_create_workspace_whoami_channels_post_mark_read_round_trip(
    real_smac_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, home_dir = real_smac_server
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    api = SmacApi(url)
    email = f"{_unique('founder')}@test.example"

    account_session = api.signup(email, _TEST_PASSWORD)
    assert account_session.account_access_token
    assert account_session.workspace_id is None
    assert session_path().exists()
    assert stat.S_IMODE(session_path().stat().st_mode) == 0o600

    session, workspace_name = api.create_workspace(
        _unique("wksp"), "private", "Ada", "Lovelace"
    )
    assert session.workspace_id
    assert session.access_token
    assert workspace_name

    me = api.whoami()
    assert me["handle"]
    assert "email" not in me  # Identity v2 spec §7: member payloads never expose email

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


def test_login_then_enter_workspace_flow(
    real_smac_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, home_dir = real_smac_server
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    email = f"{_unique('discoverable')}@test.example"

    founding_api = SmacApi(url)
    founding_api.signup(email, _TEST_PASSWORD)
    _, workspace_name = founding_api.create_workspace(
        _unique("wksp"), "private", "Grace", "Hopper"
    )
    assert founding_api.session is not None
    workspace_id = founding_api.session.workspace_id

    fresh_api = SmacApi(url)
    session, memberships = fresh_api.login(email, _TEST_PASSWORD)
    assert session.workspace_id is None  # login is account-only, no workspace yet
    assert any(
        m["workspace_id"] == workspace_id and m["workspace_name"] == workspace_name
        for m in memberships
    )

    fresh_api.enter_workspace(workspace_id)

    assert fresh_api.session is not None
    assert fresh_api.session.workspace_id == workspace_id
    assert fresh_api.whoami()["handle"]


def test_login_wrong_password_raises_auth_error_byte_identical_to_unknown_email(
    real_smac_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec §7 security invariant, client-side view: `POST /accounts/
    login` fails uniformly for an unknown email and a wrong password --
    `SmacApi.login` must not paper over that by swallowing the error or
    reshaping it into something branch-able; both cases are the identical
    `AuthError`."""
    url, home_dir = real_smac_server
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    email = f"{_unique('founder')}@test.example"
    api = SmacApi(url)
    api.signup(email, _TEST_PASSWORD)

    with pytest.raises(AuthError) as wrong_password:
        SmacApi(url).login(email, "not-the-real-password")
    with pytest.raises(AuthError) as unknown_email:
        SmacApi(url).login(f"{_unique('nobody')}@test.example", _TEST_PASSWORD)

    assert wrong_password.value.message == unknown_email.value.message


def test_join_public_workspace_directly(
    real_smac_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, home_dir = real_smac_server
    monkeypatch.setattr(Path, "home", lambda: home_dir)

    founder_api = SmacApi(url)
    founder_api.signup(f"{_unique('founder')}@test.example", _TEST_PASSWORD)
    founder_api.create_workspace(_unique("wksp"), "public", "Ada", "Lovelace")
    assert founder_api.session is not None
    workspace_id = founder_api.session.workspace_id

    joiner_api = SmacApi(url)
    joiner_api.signup(f"{_unique('joiner')}@test.example", _TEST_PASSWORD)
    session, workspace_name = joiner_api.join_public(workspace_id, "Alan", "Turing")

    assert session.workspace_id == workspace_id
    assert workspace_name
    assert joiner_api.whoami()["handle"]


def test_join_code_redeems_shareable_invite(
    real_smac_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, home_dir = real_smac_server
    monkeypatch.setattr(Path, "home", lambda: home_dir)

    founder_api = SmacApi(url)
    founder_api.signup(f"{_unique('founder')}@test.example", _TEST_PASSWORD)
    _, workspace_name = founder_api.create_workspace(
        _unique("wksp"), "private", "Ada", "Lovelace"
    )
    invite = founder_api.mint_invite_code()
    assert invite["code"]
    assert founder_api.session is not None
    workspace_id = founder_api.session.workspace_id

    joiner_api = SmacApi(url)
    joiner_api.signup(f"{_unique('joiner')}@test.example", _TEST_PASSWORD)
    session, joined_name = joiner_api.join_code(invite["code"], "Alan", "Turing")

    assert session.workspace_id == workspace_id
    assert joined_name == workspace_name
    assert joiner_api.whoami()["handle"]


def test_mint_invite_code_any_human_member_not_admin_only(
    real_smac_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`POST /workspaces/{id}/invites` is gated to human members of the
    workspace (`app/authorization.py:authorize_management_action`), not
    specifically admins -- unchanged by this task (`app/routers/
    invites.py` wasn't touched). A non-admin member can mint a code just
    like the founder can."""
    url, home_dir = real_smac_server
    monkeypatch.setattr(Path, "home", lambda: home_dir)

    founder_api = SmacApi(url)
    founder_api.signup(f"{_unique('founder')}@test.example", _TEST_PASSWORD)
    founder_api.create_workspace(_unique("wksp"), "public", "Ada", "Lovelace")
    assert founder_api.session is not None
    workspace_id = founder_api.session.workspace_id

    member_api = SmacApi(url)
    member_api.signup(f"{_unique('member')}@test.example", _TEST_PASSWORD)
    member_api.join_public(workspace_id, "Alan", "Turing")

    invite = member_api.mint_invite_code()
    assert invite["code"]


def test_search_public_finds_founded_public_workspace(
    real_smac_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, home_dir = real_smac_server
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    workspace_name = _unique("searchable")

    api = SmacApi(url)
    api.signup(f"{_unique('founder')}@test.example", _TEST_PASSWORD)
    api.create_workspace(workspace_name, "public", "Ada", "Lovelace")

    results = SmacApi(url).search_public(workspace_name)

    assert any(w["workspace_name"] == workspace_name for w in results)


# --------------------------------------------------------------------------
# CLIENT_VERSION drift tripwire (this test only -- may import `app`)
# --------------------------------------------------------------------------


def test_client_version_matches_server_version() -> None:
    import app

    assert smac_cli.CLIENT_VERSION == app.__version__
