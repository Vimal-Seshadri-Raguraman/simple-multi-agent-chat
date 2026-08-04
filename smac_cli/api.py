"""Sync HTTP client for the SMAC server: `Session` persistence + `SmacApi`.

`SmacApi` is the one place in `smac_cli` that speaks HTTP to `app/`'s
REST surface -- everything else in the TUI goes through it rather than
touching `httpx` directly. Every method is synchronous (Textual's worker
threads call these off the event loop) and raises a `smac_cli.errors.
SmacError` subclass instead of letting an `httpx` exception or a raw
error envelope escape.

Identity v2 (SMAC-79 Task 3): the server now has two auth tiers (spec
§2) -- ACCOUNT tokens (global, no workspace) and WORKSPACE tokens
(member-scoped). `Session` carries both pairs; the account pair is
minted once by `signup`/`login` and generally outlives many workspace
pairs (`enter_workspace` mints a fresh workspace pair into the SAME
session whenever the caller moves between workspaces they belong to).

Session semantics (spec "Session" paragraph, `docs/superpowers/specs/
2026-08-03-smac-tui-design.md`): one session at a time, saved to
`~/.config/smac/session.json` (chmod 600) on every successful
signup/login/enter_workspace/refresh, restored on the next launch,
deleted the moment a refresh-on-401 retry also fails.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from smac_cli.errors import NoWorkspaceError, SessionExpired, Unreachable, from_envelope
from smac_cli.paths import session_path

#: Default page size for `messages()` -- mirrors the server's `MAX_LIMIT`
#: in `app/routers/messages.py`; requesting more than this is clamped
#: server-side anyway, so this is just a sane client-side default.
DEFAULT_MESSAGE_LIMIT = 15

_DELETE_CONFIRMATION = "delete"


@dataclass
class Session:
    """A saved login: everything needed to resume talking to one server.

    Identity v2 (SMAC-79 Task 3): `account_access_token`/
    `account_refresh_token` are new -- every session created by this
    client always sets both. The workspace-tier fields (`workspace_id`,
    `access_token`, `refresh_token`) keep their pre-v2 names but are now
    optional: an account fresh off `/register` with no workspace yet has
    a session with account tokens and `None` for all three.

    Backward compatibility: a session.json written by a pre-Identity-v2
    build of this client has `workspace_id`/`access_token`/
    `refresh_token` but no `account_access_token`/`account_refresh_token`
    keys at all. Because those two fields default to `None`, `Session.
    load` still parses such a file successfully (never crashes) rather
    than raising -- `SmacApp._restore_session` is the one place that
    checks `account_access_token is None` and treats that shape as an
    expired session (spec: "session expired — /login"), since every
    server-side refresh token was purged by the Identity v2 migration
    anyway (a live server would reject it exactly the same way).
    """

    url: str
    email: str
    account_access_token: str | None = None
    account_refresh_token: str | None = None
    workspace_id: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None

    def save(self, path: Path) -> None:
        """Write this session to `path` as JSON, chmod 600 (contains secrets).

        Creates the file via `os.open` with mode `0o600` from the very
        first byte (finding C) rather than `write_text` then `chmod`: the
        latter briefly creates the file at the process umask's default
        perms (typically `0o644`) before the follow-up `chmod` call
        lands -- a real, if narrow, window on the very first save where
        another local user could read a freshly-created token file.
        `O_CREAT`'s mode is applied atomically at creation (still subject
        to umask narrowing it further, but never widening it), so there
        is no window where this file exists more permissive than 600. The
        trailing `chmod` still runs too, for the (rarer) case of an
        already-existing file at `path` with different perms from some
        other source -- `os.open`'s mode argument only applies when it
        actually creates the file.
        """
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(self)))
        path.chmod(0o600)

    @classmethod
    def load(cls, path: Path) -> "Session | None":
        """Read a session back from `path`, or `None` if absent/unreadable.

        A missing file, unreadable file, corrupt JSON, or JSON missing a
        required field (`url`/`email`) are all treated the same way --
        "no usable saved session" -- rather than raising, since every
        caller's fallback is identical (fall through to the logged-out
        welcome screen). A pre-Identity-v2 file, which HAS `url`/`email`
        but lacks the newer account-token fields, parses fine (those
        fields default to `None`) -- see the class docstring for how that
        shape is handled by its one caller, `SmacApp._restore_session`.
        """
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return cls(**data)
        except TypeError:
            return None


class SmacApi:
    """Sync client for one SMAC server, optionally holding a live session.

    `transport` is exposed purely for tests: pass an `httpx.MockTransport`
    for unit-level error-mapping/refresh tests, or leave it `None` (the
    default) to talk to a real server over the network -- there is no
    sync ASGI transport that works for this app (`WSGITransport` doesn't
    speak ASGI), so real-server tests spawn `smac-server` for real
    instead.
    """

    def __init__(
        self,
        url: str,
        *,
        session: Session | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.session = session
        self._client = httpx.Client(
            base_url=self.url, transport=transport, timeout=10.0
        )
        # `SmacApp` (smac_cli/app.py) calls the same `SmacApi` instance from
        # several worker threads at once. Both refresh tokens (account and
        # workspace) are single-use/rotating server-side (`app/routers/
        # auth.py:refresh` deletes the row on redemption), so two threads
        # racing to redeem the SAME token would have the loser's redeem
        # rejected -- this lock serializes every refresh attempt (account
        # or workspace) through this instance; see `_try_refresh_workspace`/
        # `_try_refresh_account` for how a loser recognizes a token already
        # rotated out from under it.
        self._refresh_lock = threading.Lock()

    # -- low-level plumbing --------------------------------------------

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        bearer: str | None = None,
    ) -> httpx.Response:
        """Issue one HTTP request, translating connection failures.

        Any failure that never produced an HTTP response (refused
        connection, DNS failure, timeout, ...) becomes `Unreachable` --
        the one error class that isn't mapped from a server envelope.
        """
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else None
        try:
            return self._client.request(
                method, path, json=json_body, params=params, headers=headers
            )
        except httpx.TransportError as exc:
            raise Unreachable(self.url) from exc

    def _parse(self, response: httpx.Response) -> Any:
        """Return a successful response's JSON body, or raise a typed error.

        A 2xx with an empty body (e.g. some future `204`) returns `None`.
        A non-2xx is expected to carry the standard
        `{"error": {"code", "message"}}` envelope (`app/main.py`'s
        exception handlers); anything that doesn't parse that way still
        raises a `SmacError`, just without a specific server-provided code.
        """
        if response.status_code < 400:
            if not response.content:
                return None
            return response.json()
        try:
            error = response.json()["error"]
            code = str(error["code"])
            message = str(error["message"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            code = "http_error"
            message = response.text or f"HTTP {response.status_code}"
        raise from_envelope(code, message)

    def _invalidate_session(self) -> None:
        """Drop the in-memory session and delete its saved file, if any."""
        session_path().unlink(missing_ok=True)
        self.session = None

    # -- refresh chain ------------------------------------------------------
    #
    # Two independent token pairs can each be rotated: `_try_refresh_
    # workspace`/`_try_refresh_account`. Neither invalidates the session on
    # failure by itself -- they just report success/failure -- so the
    # workspace-tier recovery chain (`_recover_workspace_session`) can fall
    # through from a failed workspace refresh to an account-refresh-and-
    # re-mint attempt before giving up. Only the top-level callers
    # (`_authed_request`, `_account_authed_request`, `_recover_workspace_
    # session`) ever actually invalidate.

    def _try_refresh_workspace(self) -> bool:
        """Attempt to rotate the WORKSPACE token pair via `/auth/refresh`.

        Returns `True` on success (the session's workspace fields are
        updated and saved), `False` on failure -- the session is left
        untouched either way; the caller decides what happens next.
        Thread-safe: the refresh token to redeem is captured before
        acquiring `_refresh_lock` and re-checked just after, so a
        concurrent call that already rotated this same token is reported
        as a (no-op) success rather than re-presenting an already-spent
        token to the server.
        """
        if self.session is None or self.session.refresh_token is None:
            return False
        presented = self.session.refresh_token
        with self._refresh_lock:
            if self.session is None:
                return False
            if self.session.refresh_token != presented:
                return True  # a concurrent call already refreshed this
            response = self._send(
                "POST", "/auth/refresh", json_body={"refresh_token": presented}
            )
            if response.status_code != 200:
                return False
            data = response.json()
            self.session.access_token = data["access_token"]
            self.session.refresh_token = data["refresh_token"]
            self.session.save(session_path())
            return True

    def _try_refresh_account(self) -> bool:
        """The account-tier twin of `_try_refresh_workspace` -- same
        contract, rotates `account_access_token`/`account_refresh_token`
        instead."""
        if self.session is None or self.session.account_refresh_token is None:
            return False
        presented = self.session.account_refresh_token
        with self._refresh_lock:
            if self.session is None:
                return False
            if self.session.account_refresh_token != presented:
                return True  # a concurrent call already refreshed this
            response = self._send(
                "POST", "/auth/refresh", json_body={"refresh_token": presented}
            )
            if response.status_code != 200:
                return False
            data = response.json()
            self.session.account_access_token = data["access_token"]
            self.session.account_refresh_token = data["refresh_token"]
            self.session.save(session_path())
            return True

    def _recover_workspace_session(self) -> None:
        """A workspace-tier request 401'd: try to make the session usable
        again, or raise `SessionExpired` (and wipe the session) once every
        option is exhausted.

        The chain (brief, binding): workspace refresh -> account-refresh
        fallback (rotate the account pair, then re-mint a fresh workspace
        pair via `POST /workspaces/{id}/token`) -> `SessionExpired`. The
        fallback exists because the workspace and account refresh tokens
        can go stale independently (e.g. a long-idle client whose
        workspace refresh token was already redeemed/expired while the
        account token is still good) -- falling back keeps the caller
        logged in without forcing a full `/login` whenever the account
        session itself is still perfectly valid.
        """
        if self._try_refresh_workspace():
            return
        if self.session is not None and self.session.workspace_id is not None:
            workspace_id = self.session.workspace_id
            if self._try_refresh_account():
                assert self.session is not None
                response = self._send(
                    "POST",
                    f"/workspaces/{workspace_id}/token",
                    bearer=self.session.account_access_token,
                )
                if response.status_code == 200:
                    data = response.json()
                    self.session.workspace_id = workspace_id
                    self.session.access_token = data["access_token"]
                    self.session.refresh_token = data["refresh_token"]
                    self.session.save(session_path())
                    return
        self._invalidate_session()
        raise SessionExpired()

    def _authed_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Issue a WORKSPACE-tier authenticated request, recovering-and-
        retrying once on a 401.

        Raises `NoWorkspaceError` immediately (no request sent) if the
        session has no workspace token yet -- distinct from
        `SessionExpired`: the account itself is fine, there's just no
        workspace entered (spec: `/register`'s no-workspace landing
        state). A 401 on the first attempt triggers exactly one
        `_recover_workspace_session()` + retry; a 401 on the retry (or a
        failed recovery) raises `SessionExpired` and deletes the saved
        session -- recovery is never attempted more than once per call.
        """
        if self.session is None:
            raise SessionExpired("No active session.")
        if self.session.access_token is None:
            raise NoWorkspaceError()
        response = self._send(
            method,
            path,
            json_body=json_body,
            params=params,
            bearer=self.session.access_token,
        )
        if response.status_code == 401:
            self._recover_workspace_session()  # raises SessionExpired on failure
            if self.session is None or self.session.access_token is None:
                # Finding J: a concurrent force-expiry (another thread's own
                # failed refresh invalidating this shared session) can null
                # `self.session` in the narrow window right after recovery
                # returns here. Surface the same `SessionExpired` a normal
                # failed recovery would, rather than an `AttributeError`.
                raise SessionExpired()
            response = self._send(
                method,
                path,
                json_body=json_body,
                params=params,
                bearer=self.session.access_token,
            )
            if response.status_code == 401:
                self._invalidate_session()
                raise SessionExpired()
        return self._parse(response)

    def _account_authed_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """The ACCOUNT-tier twin of `_authed_request`: one refresh-and-
        retry on a 401, via `_try_refresh_account` only -- there is no
        further fallback tier above "account", so a failed refresh here
        goes straight to `SessionExpired`."""
        if self.session is None or self.session.account_access_token is None:
            raise SessionExpired("No active session.")
        response = self._send(
            method,
            path,
            json_body=json_body,
            params=params,
            bearer=self.session.account_access_token,
        )
        if response.status_code == 401:
            if not self._try_refresh_account():
                self._invalidate_session()
                raise SessionExpired()
            if self.session is None or self.session.account_access_token is None:
                raise SessionExpired()
            response = self._send(
                method,
                path,
                json_body=json_body,
                params=params,
                bearer=self.session.account_access_token,
            )
            if response.status_code == 401:
                self._invalidate_session()
                raise SessionExpired()
        return self._parse(response)

    def _require_workspace_id(self) -> str:
        """The active session's workspace_id, `SessionExpired` if logged
        out entirely, or `NoWorkspaceError` if logged in but no workspace
        has been entered yet."""
        if self.session is None:
            raise SessionExpired("No active session.")
        if self.session.workspace_id is None:
            raise NoWorkspaceError()
        return self.session.workspace_id

    def _apply_workspace_auth_out(self, data: dict[str, Any]) -> tuple[Session, str]:
        """Fold a `WorkspaceAuthOut`-shaped response (every workspace-birth
        door: `POST /workspaces`, `.../register`, `/workspaces/join`) into
        the CURRENT session -- account fields/email untouched, workspace
        fields overwritten -- save it, and return `(session, workspace_
        name)`. `workspace_name` is handed back explicitly because `Session`
        itself never carries it (spec-pinned shape) and the caller doesn't
        always already know it (e.g. `/join <code>` -- the code is the
        only thing the caller had going in)."""
        if self.session is None:
            raise SessionExpired("No active session.")
        workspace = data["workspace"]
        self.session.workspace_id = workspace["workspace_id"]
        self.session.access_token = data["access_token"]
        self.session.refresh_token = data["refresh_token"]
        self.session.save(session_path())
        return self.session, str(workspace["workspace_name"])

    # -- unauthenticated endpoints ---------------------------------------

    def meta(self) -> dict[str, Any]:
        """`GET /meta`: the server/API version handshake."""
        return self._parse(self._send("GET", "/meta"))

    def signup(self, email: str, password: str) -> Session:
        """`POST /accounts`: create a global account (spec §2). Account-
        tier tokens only -- no workspace yet, so `workspace_id`/
        `access_token`/`refresh_token` are all `None` on the returned
        session until `create_workspace`/`join_public`/`join_code`/
        `enter_workspace` mints a workspace pair into it."""
        data = self._parse(
            self._send(
                "POST", "/accounts", json_body={"email": email, "password": password}
            )
        )
        session = Session(
            url=self.url,
            email=email,
            account_access_token=data["tokens"]["access_token"],
            account_refresh_token=data["tokens"]["refresh_token"],
        )
        session.save(session_path())
        self.session = session
        return session

    def login(self, email: str, password: str) -> tuple[Session, list[dict[str, Any]]]:
        """`POST /accounts/login`: global login (spec §2) -- no
        `workspace_id` needed, unlike the retired workspace-scoped
        `/auth/login`. Returns the saved account-only session plus every
        workspace this account already has a profile in (`{workspace_id,
        workspace_name, member_id, handle}` each), real data the retired
        `/auth/discover` only ever simulated."""
        data = self._parse(
            self._send(
                "POST",
                "/accounts/login",
                json_body={"email": email, "password": password},
            )
        )
        session = Session(
            url=self.url,
            email=email,
            account_access_token=data["tokens"]["access_token"],
            account_refresh_token=data["tokens"]["refresh_token"],
        )
        session.save(session_path())
        self.session = session
        return session, list(data["workspaces"])

    def search_public(self, q: str = "") -> list[dict[str, Any]]:
        """`GET /workspaces/search`: public workspaces matching `q` (or all, if blank)."""
        params = {"name": q} if q else None
        return list(self._parse(self._send("GET", "/workspaces/search", params=params)))

    # -- account-tier endpoints -------------------------------------------

    def enter_workspace(self, workspace_id: str) -> None:
        """`POST /workspaces/{id}/token` (account bearer): exchange the
        account token for a fresh WORKSPACE token pair for a workspace
        this account already belongs to, and mint it into the current
        session (saved immediately). Used by `/login`'s memberships
        picker/auto-login branches -- registering into a BRAND NEW
        workspace instead goes through `create_workspace`/`join_public`/
        `join_code`, which mint their own convenience workspace pair
        directly in their response."""
        data = self._account_authed_request("POST", f"/workspaces/{workspace_id}/token")
        if self.session is None:
            raise SessionExpired()
        self.session.workspace_id = workspace_id
        self.session.access_token = data["access_token"]
        self.session.refresh_token = data["refresh_token"]
        self.session.save(session_path())

    def create_workspace(
        self, name: str, visibility: str, first_name: str, last_name: str
    ) -> tuple[Session, str]:
        """`POST /workspaces` (account bearer): found a brand-new
        workspace, linking the caller's EXISTING account as its admin
        (spec §3) -- no email/password here anymore, that's `signup`'s job."""
        data = self._account_authed_request(
            "POST",
            "/workspaces",
            json_body={
                "workspace_name": name,
                "visibility": visibility,
                "display_first_name": first_name,
                "display_last_name": last_name,
            },
        )
        return self._apply_workspace_auth_out(data)

    def join_public(
        self, workspace_id: str, first_name: str, last_name: str
    ) -> tuple[Session, str]:
        """`POST /workspaces/{id}/register` (account bearer): join a
        workspace directly by id (public: open door; private: only with a
        reserved seat matching the caller's account email)."""
        data = self._account_authed_request(
            "POST",
            f"/workspaces/{workspace_id}/register",
            json_body={"first_name": first_name, "last_name": last_name},
        )
        return self._apply_workspace_auth_out(data)

    def join_code(
        self, code: str, first_name: str, last_name: str
    ) -> tuple[Session, str]:
        """`POST /workspaces/join` (account bearer): join the workspace a
        shareable invite code belongs to."""
        data = self._account_authed_request(
            "POST",
            "/workspaces/join",
            json_body={"code": code, "first_name": first_name, "last_name": last_name},
        )
        return self._apply_workspace_auth_out(data)

    def mint_invite_code(self) -> dict[str, Any]:
        """`POST /workspaces/{workspace_id}/invites` (workspace bearer,
        admin): mint a fresh shareable multi-use join code. The full
        `InviteOut` dict is returned (its `"code"` key is what `/invite`
        prints)."""
        workspace_id = self._require_workspace_id()
        return dict(
            self._authed_request(
                "POST",
                f"/workspaces/{workspace_id}/invites",
                json_body={"invite_type": "code"},
            )
        )

    # -- workspace-tier endpoints ------------------------------------------

    def whoami(self) -> dict[str, Any]:
        """`GET /members/me`: the logged-in member's own full profile."""
        return dict(self._authed_request("GET", "/members/me"))

    def channels(self) -> list[dict[str, Any]]:
        """`GET /workspaces/{workspace_id}/channels`: every channel in the workspace."""
        workspace_id = self._require_workspace_id()
        return list(self._authed_request("GET", f"/workspaces/{workspace_id}/channels"))

    def unreads(self) -> dict[str, Any]:
        """`GET /workspaces/{workspace_id}/unreads`: per-channel unread state."""
        workspace_id = self._require_workspace_id()
        return dict(self._authed_request("GET", f"/workspaces/{workspace_id}/unreads"))

    def members(self) -> list[dict[str, Any]]:
        """`GET /workspaces/{workspace_id}/members`: every member (handle
        included) in the workspace -- the TUI's source for resolving a
        message payload's `Sender.member_id` to a `handle`, since the
        message wire shape itself (`app/schemas.py:build_message_payload`)
        only carries `member_name` (see `smac_cli.render`'s module
        docstring for why that's not the same thing)."""
        workspace_id = self._require_workspace_id()
        return list(self._authed_request("GET", f"/workspaces/{workspace_id}/members"))

    def create_channel(self, name: str) -> dict[str, Any]:
        """`POST /workspaces/{workspace_id}/channels`: create a new channel."""
        workspace_id = self._require_workspace_id()
        return dict(
            self._authed_request(
                "POST",
                f"/workspaces/{workspace_id}/channels",
                json_body={"channel_name": name},
            )
        )

    def messages(
        self,
        channel_id: str,
        after: str | None = None,
        limit: int = DEFAULT_MESSAGE_LIMIT,
    ) -> list[dict[str, Any]]:
        """`GET .../channels/{channel_id}/messages`: a page of message history."""
        workspace_id = self._require_workspace_id()
        params: dict[str, Any] = {"limit": limit}
        if after is not None:
            params["after"] = after
        return list(
            self._authed_request(
                "GET",
                f"/workspaces/{workspace_id}/channels/{channel_id}/messages",
                params=params,
            )
        )

    def post(self, channel_id: str, text: str) -> dict[str, Any]:
        """`POST .../channels/{channel_id}/messages`: post a message."""
        workspace_id = self._require_workspace_id()
        return dict(
            self._authed_request(
                "POST",
                f"/workspaces/{workspace_id}/channels/{channel_id}/messages",
                json_body={"message_text": text},
            )
        )

    def mark_read(self, channel_id: str) -> dict[str, Any]:
        """`POST .../channels/{channel_id}/read`: advance the read cursor to latest."""
        workspace_id = self._require_workspace_id()
        return dict(
            self._authed_request(
                "POST", f"/workspaces/{workspace_id}/channels/{channel_id}/read"
            )
        )

    def delete_workspace(self) -> dict[str, Any]:
        """`DELETE /workspaces/{workspace_id}?confirm=delete`: destroy the workspace."""
        workspace_id = self._require_workspace_id()
        return dict(
            self._authed_request(
                "DELETE",
                f"/workspaces/{workspace_id}",
                params={"confirm": _DELETE_CONFIRMATION},
            )
        )

    # -- WebSocket URLs ---------------------------------------------------

    def ws_channel_url(self, channel_id: str) -> str:
        """The `ws://` URL for a channel's live message feed, with a fresh token."""
        workspace_id = self._require_workspace_id()
        return self._ws_url(f"/ws/workspaces/{workspace_id}/channels/{channel_id}")

    def ws_events_url(self) -> str:
        """The `ws://` URL for the caller's private mention-events feed."""
        workspace_id = self._require_workspace_id()
        return self._ws_url(f"/ws/workspaces/{workspace_id}/members/me/events")

    def _ws_url(self, path: str) -> str:
        """Build a `ws(s)://` URL for `path`, refreshing first so the embedded
        token is guaranteed fresh.

        A WebSocket connection is long-lived and authenticates only once,
        at connect time (`app/routers/websocket.py`'s `?token=`) -- unlike
        a REST call, there's no retry-on-401 available once it's open.
        Refreshing unconditionally here (rather than only when a prior
        request happened to 401) is what makes that guarantee hold even
        right after a session was restored from disk on a fresh launch,
        when the access token may already be stale from a previous run.
        Uses the same workspace-refresh-then-account-fallback chain as an
        ordinary 401 recovery (`_recover_workspace_session`), since a
        WebSocket connect has no response to react to if the plain
        refresh alone would have failed.
        """
        self._require_workspace_id()  # NoWorkspaceError if nothing to refresh
        # `_recover_workspace_session` itself starts with a plain workspace
        # refresh attempt (falling back through the account tier only if
        # that fails), so calling it unconditionally here -- rather than
        # trying `_try_refresh_workspace` first and only falling back on
        # failure -- gets the same "always refresh" guarantee in one call.
        self._recover_workspace_session()  # raises SessionExpired on total failure
        assert self.session is not None  # the above raises SessionExpired otherwise
        scheme = "wss" if self.url.startswith("https://") else "ws"
        host_and_port = self.url.split("://", 1)[1]
        return f"{scheme}://{host_and_port}{path}?token={self.session.access_token}"
