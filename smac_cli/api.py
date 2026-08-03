"""Sync HTTP client for the SMAC server: `Session` persistence + `SmacApi`.

`SmacApi` is the one place in `smac_cli` that speaks HTTP to `app/`'s
REST surface -- everything else in the TUI (a later task on this
branch) goes through it rather than touching `httpx` directly. Every
method is synchronous (Textual's worker threads call these off the
event loop) and raises a `smac_cli.errors.SmacError` subclass instead of
letting an `httpx` exception or a raw error envelope escape.

Session semantics (spec "Session" paragraph, `docs/superpowers/specs/
2026-08-03-smac-tui-design.md`): one session at a time, saved to
`~/.config/smac/session.json` (chmod 600) on every successful
login/register/refresh, restored on the next launch, deleted the moment
a refresh-on-401 retry also fails.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from smac_cli.errors import SessionExpired, Unreachable, from_envelope
from smac_cli.paths import session_path

#: Default page size for `messages()` -- mirrors the server's `MAX_LIMIT`
#: in `app/routers/messages.py`; requesting more than this is clamped
#: server-side anyway, so this is just a sane client-side default.
DEFAULT_MESSAGE_LIMIT = 15

_DELETE_CONFIRMATION = "delete"


@dataclass
class Session:
    """A saved login: everything needed to resume talking to one workspace.

    Mirrors the on-disk shape pinned by the spec:
    `{url, workspace_id, access_token, refresh_token, email}`.
    """

    url: str
    workspace_id: str
    access_token: str
    refresh_token: str
    email: str

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

        A missing file, unreadable file, corrupt JSON, or JSON missing an
        expected field are all treated the same way -- "no usable saved
        session" -- rather than raising, since every caller's fallback is
        identical (fall through to the logged-out welcome screen).
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
        # several worker threads at once (command handlers, mark-read,
        # load-older, and -- SMAC-72 task 5 -- the channel feed's and the
        # mention bell's own background threads all calling `ws_channel_url`/
        # `ws_events_url`, both of which refresh unconditionally). The
        # refresh token is single-use/rotating server-side (`app/routers/
        # auth.py:refresh` deletes it on redemption), so two threads racing
        # to redeem the SAME token would have the loser's redeem rejected --
        # and `_refresh`'s failure path wipes `self.session` entirely,
        # taking down every other in-flight call sharing this instance.
        # This lock serializes the redeem; see `_refresh` for how the loser
        # recognizes a token already rotated out from under it.
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

    def _refresh(self) -> None:
        """Rotate the current session's tokens via `/auth/refresh`.

        On success, the new tokens are saved immediately (same file,
        same session). On failure, the saved session is deleted and
        `SessionExpired` is raised -- there is nothing left to retry but
        `/login`.

        Thread-safe against a concurrent `_refresh()` on the same
        instance (see `__init__`'s docstring on `_refresh_lock`): the
        refresh token to redeem is captured *before* acquiring the lock,
        and re-checked just after -- if it no longer matches
        `self.session.refresh_token`, another thread already redeemed it
        (and this thread's own session is already up to date from that),
        so this call simply returns instead of re-sending a token the
        server has already rotated out from under it.
        """
        if self.session is None:
            raise SessionExpired("No active session.")
        presented = self.session.refresh_token
        with self._refresh_lock:
            if self.session is None:
                raise SessionExpired("No active session.")
            if self.session.refresh_token != presented:
                return  # a concurrent call already refreshed this session
            response = self._send(
                "POST",
                "/auth/refresh",
                json_body={"refresh_token": presented},
            )
            if response.status_code != 200:
                self._invalidate_session()
                raise SessionExpired()
            data = response.json()
            self.session.access_token = data["access_token"]
            self.session.refresh_token = data["refresh_token"]
            self.session.save(session_path())

    def _authed_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Issue an authenticated request, refreshing-and-retrying once on a 401.

        A 401 on the first attempt triggers exactly one `/auth/refresh` +
        retry; a 401 on the retry (or a failed refresh) raises
        `SessionExpired` and deletes the saved session -- refresh is never
        attempted more than once per call.
        """
        if self.session is None:
            raise SessionExpired("No active session.")
        response = self._send(
            method,
            path,
            json_body=json_body,
            params=params,
            bearer=self.session.access_token,
        )
        if response.status_code == 401:
            self._refresh()
            if self.session is None:
                # Finding J: a concurrent force-expiry -- another thread's
                # own failed refresh redeeming this same token first, then
                # invalidating the shared session -- can null `self.session`
                # in the narrow window between `_refresh()` returning
                # successfully here and this retry reading `self.session.
                # access_token` below. Surface the same `SessionExpired` a
                # normal failed refresh would, rather than an
                # `AttributeError` crashing whatever worker called this.
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

    def _require_workspace_id(self) -> str:
        """The active session's workspace_id, or `SessionExpired` if logged out."""
        if self.session is None:
            raise SessionExpired("No active session.")
        return self.session.workspace_id

    def _session_from_auth_out(self, email: str, data: dict[str, Any]) -> Session:
        """Build+save a `Session` from a `WorkspaceAuthOut`-shaped response."""
        session = Session(
            url=self.url,
            workspace_id=data["workspace"]["workspace_id"],
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            email=email,
        )
        session.save(session_path())
        self.session = session
        return session

    # -- unauthenticated endpoints ---------------------------------------

    def meta(self) -> dict[str, Any]:
        """`GET /meta`: the server/API version handshake."""
        return self._parse(self._send("GET", "/meta"))

    def discover(self, email: str, password: str) -> list[dict[str, Any]]:
        """`POST /auth/discover`: every workspace these credentials open."""
        data = self._parse(
            self._send(
                "POST",
                "/auth/discover",
                json_body={"email": email, "password": password},
            )
        )
        return list(data["workspaces"])

    def login(self, workspace_id: str, email: str, password: str) -> Session:
        """`POST /auth/login`: exchange credentials for a token pair, save the session."""
        data = self._parse(
            self._send(
                "POST",
                "/auth/login",
                json_body={
                    "workspace_id": workspace_id,
                    "email": email,
                    "password": password,
                },
            )
        )
        session = Session(
            url=self.url,
            workspace_id=workspace_id,
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            email=email,
        )
        session.save(session_path())
        self.session = session
        return session

    def register_found(
        self,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        workspace_name: str,
        visibility: str,
    ) -> Session:
        """`POST /workspaces`: found a brand-new workspace + admin account."""
        data = self._parse(
            self._send(
                "POST",
                "/workspaces",
                json_body={
                    "email": email,
                    "password": password,
                    "first_name": first_name,
                    "last_name": last_name,
                    "workspace_name": workspace_name,
                    "visibility": visibility,
                },
            )
        )
        return self._session_from_auth_out(email, data)

    def register_into(
        self,
        workspace_id: str,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
    ) -> Session:
        """`POST /workspaces/{id}/register`: join an existing (public, or
        invited-into) workspace with a brand-new account."""
        data = self._parse(
            self._send(
                "POST",
                f"/workspaces/{workspace_id}/register",
                json_body={
                    "email": email,
                    "password": password,
                    "first_name": first_name,
                    "last_name": last_name,
                },
            )
        )
        return self._session_from_auth_out(email, data)

    def search_public(self, q: str = "") -> list[dict[str, Any]]:
        """`GET /workspaces/search`: public workspaces matching `q` (or all, if blank)."""
        params = {"name": q} if q else None
        return list(self._parse(self._send("GET", "/workspaces/search", params=params)))

    # -- authenticated endpoints ------------------------------------------

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
        """
        self._refresh()
        assert self.session is not None  # _refresh() raises SessionExpired otherwise
        scheme = "wss" if self.url.startswith("https://") else "ws"
        host_and_port = self.url.split("://", 1)[1]
        return f"{scheme}://{host_and_port}{path}?token={self.session.access_token}"
