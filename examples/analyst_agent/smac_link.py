"""`SmacLink`: the one place in this example that speaks SMAC's HTTP/WS
wire shapes. `brain.py` (Task 3) knows only Anthropic; `agent.py` (Task 4)
talks to SMAC exclusively through this module's `SmacLink`.

Server contracts consumed here (verified at main `e36a08c`):

    POST {smac_url}/agents/join   {"code": str, "name": str}
      -> 201 {"account_id","member_id","handle","api_key",
               "workspace":{"workspace_id","workspace_name"}}
      -> 404 {"error":{"code":"invite_invalid","message":"Invite is invalid or expired"}}

    Everything below authenticates with the header:  X-API-Key: <api_key>
    GET  {smac_url}/workspaces/{ws}/channels
    GET  {smac_url}/workspaces/{ws}/channels/{ch}/messages?limit=..&after=..
    POST {smac_url}/workspaces/{ws}/channels/{ch}/messages   {"message_text": str}
    GET  {smac_url}/mentions
    POST {smac_url}/mentions/{mention_id}/ack
    WS   {smac_url as ws}/ws/workspaces/{ws}/members/me/events   header X-API-Key (NOT ?token=)

`GET .../messages` is a FORWARD-ONLY pager, not a "give me the latest N"
query (`app/routers/messages.py`): with no `after` cursor it returns the
OLDEST messages in the channel (`Message.seq.asc()`), and `limit` is
clamped server-side to `MAX_LIMIT = 15` regardless of what's requested.
`history()` below compensates for both surprises -- see its own
docstring for how it reaches the actual tail.

Credentials (member_id/handle/api_key/workspace_id/workspace_name) are the
agent's one-time join result -- `Member.api_key_hash` is one-way server-
side, so this is the only place that key is ever recoverable. They are
persisted to a per-agent-name JSON file under `CONFIG_HOME`, created with
mode 0600 from the very first byte (mirroring `smac_cli/api.py`'s
`Session.save`), so a second run loads instead of re-redeeming the
(single-use) invite code.
"""

from __future__ import annotations

import json
import os
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
import websockets

from analyst_agent.config import Config

#: `~/.config/analyst_agent` -- where per-agent credential files live.
#: Read as a module global (not captured into a local at import time) so
#: tests can `monkeypatch.setattr(smac_link, "CONFIG_HOME", tmp_path)`.
CONFIG_HOME = Path.home() / ".config" / "analyst_agent"

_RECOVERY_HINT = "mint a fresh one in Settings → Invites"
_DEFAULT_HISTORY_LIMIT = 20
_REQUEST_TIMEOUT_SECONDS = 10.0

#: The server's own page-size ceiling for `GET .../messages`
#: (`app/routers/messages.py`'s `MAX_LIMIT`) -- `history()`'s pager never
#: asks for more than this per HTTP request, since the server would
#: silently clamp it anyway.
_MAX_HISTORY_PAGE = 15

#: Hard ceiling on how many pages `history()`'s pager will walk in one
#: call. Unreachable against a conformant server (which ends the walk with
#: a short or empty page); it exists so a buggy or hostile one cannot page
#: forever and hang the agent -- the same defense the mention drain has.
_MAX_HISTORY_PAGES = 1000


class SmacLinkError(Exception):
    """Base class for every error `SmacLink` raises."""


class JoinFailed(SmacLinkError):
    """`join_or_load()` could not obtain usable credentials -- either the
    invite code the server rejected, or no code and nothing saved yet."""


class RequestFailed(SmacLinkError):
    """An authenticated request to the SMAC server failed. `str(error)`
    is the server's own envelope message (never a key, never a raw
    traceback)."""


@dataclass(frozen=True)
class Credentials:
    member_id: str
    handle: str
    api_key: str
    workspace_id: str
    workspace_name: str


@dataclass
class _ChannelTail:
    """Per-channel state for `history()`'s forward-cursor tail pager
    (F2 fix). `buffer` is a rolling window of the most recent messages
    seen so far (bounded to the caller's requested `limit` -- oldest
    evicted automatically as new ones are appended); `cursor` is the
    `message_id` of the newest message this pager has fetched, `None`
    until the first page is fetched. Not frozen: both fields mutate in
    place as `history()` pages forward."""

    buffer: "deque[dict[str, Any]]"
    cursor: str | None = None


def _slug(agent_name: str) -> str:
    """Filesystem-safe stem for `agent_name` -- lowercased, spaces
    collapsed to `-`. Not required to be reversible, only stable and
    collision-avoiding for the common case of one agent per name."""
    return "-".join(agent_name.strip().lower().split()) or "agent"


def credentials_path(agent_name: str) -> Path:
    """The saved-credentials file for an agent named `agent_name`, under
    the *current* `CONFIG_HOME` -- looked up fresh on every call so tests
    can monkeypatch `CONFIG_HOME` per-case."""
    return CONFIG_HOME / f"{_slug(agent_name)}.json"


def _server_message(response: httpx.Response) -> str:
    """Extract the server's own `error.message` from a non-2xx envelope
    (`app/main.py`'s exception handlers), or fall back to raw text/status
    for anything that doesn't parse that way."""
    try:
        error = response.json()["error"]
        return str(error["message"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return response.text or f"HTTP {response.status_code}"


class SmacLink:
    """Talks to one SMAC server on behalf of one agent identity.

    `transport` is exposed purely for tests: pass an `httpx.MockTransport`
    to drive this against a fake server with no network at all. Left
    `None` (the default), `httpx.Client` talks to `config.smac_url` for
    real.
    """

    def __init__(
        self, config: Config, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.config = config
        self.credentials: Credentials | None = None
        self._client = httpx.Client(
            base_url=config.smac_url,
            transport=transport,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        #: `history()`'s forward-cursor tail state, one per channel this
        #: agent has ever asked about -- see `history()`'s own docstring
        #: and `_ChannelTail`.
        self._tails: dict[str, _ChannelTail] = {}

    # -- credentials: load/save/join -------------------------------------

    def load_credentials(self) -> Credentials | None:
        """Read this agent's saved credentials from disk, or `None` if
        there aren't any (missing file, unreadable, or corrupt JSON --
        all treated the same: "nothing usable saved")."""
        path = credentials_path(self.config.agent_name)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Credentials(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def save_credentials(self, creds: Credentials) -> None:
        """Persist `creds` to this agent's credentials file, mode 0600
        from the moment the file is created (`os.open`'s mode argument
        applies atomically at creation, before any content is written --
        no window where a partially-written key file is world-readable),
        with a trailing `chmod` for the rarer case of an already-existing
        file at this path with looser permissions from some other
        source."""
        path = credentials_path(self.config.agent_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(creds)))
        path.chmod(0o600)

    def join_or_load(self) -> Credentials:
        """Prefer saved credentials; only redeem `config.agent_code`
        against `POST /agents/join` if nothing is saved yet. A single-use
        invite code must never be presented twice, so this is the only
        path that ever calls `/agents/join`."""
        existing = self.load_credentials()
        if existing is not None:
            self.credentials = existing
            return existing

        if not self.config.agent_code:
            raise JoinFailed(
                "No saved credentials and SMAC_AGENT_CODE is not set -- "
                f"{_RECOVERY_HINT}, then set SMAC_AGENT_CODE in your .env"
            )

        response = self._client.post(
            "/agents/join",
            json={"code": self.config.agent_code, "name": self.config.agent_name},
        )
        if response.status_code != 201:
            raise JoinFailed(f"{_server_message(response)} -- {_RECOVERY_HINT}")

        data = response.json()
        workspace = data["workspace"]
        creds = Credentials(
            member_id=data["member_id"],
            handle=data["handle"],
            api_key=data["api_key"],
            workspace_id=workspace["workspace_id"],
            workspace_name=workspace["workspace_name"],
        )
        self.save_credentials(creds)
        self.credentials = creds
        return creds

    # -- authed HTTP plumbing --------------------------------------------

    def _require_credentials(self) -> Credentials:
        if self.credentials is None:
            raise SmacLinkError("SmacLink used before join_or_load() succeeded")
        return self.credentials

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._require_credentials().api_key}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        response = self._client.request(
            method, path, params=params, json=json_body, headers=self._headers()
        )
        if response.status_code >= 400:
            raise RequestFailed(_server_message(response))
        if not response.content:
            return None
        return response.json()

    # -- authed endpoints --------------------------------------------------

    def channels(self) -> list[dict[str, Any]]:
        """`GET /workspaces/{ws}/channels`."""
        workspace_id = self._require_credentials().workspace_id
        return list(self._request("GET", f"/workspaces/{workspace_id}/channels"))

    def history(
        self, channel_id: str, limit: int = _DEFAULT_HISTORY_LIMIT
    ) -> list[dict[str, Any]]:
        """The `limit` most recent messages in `channel_id` -- the TAIL,
        oldest of the returned batch first. `GET .../messages` itself
        has no "give me the latest N" query (see the module docstring):
        with no cursor it returns the OLDEST messages, clamped to 15 per
        page server-side. To reach the actual tail, this keeps a
        per-channel forward cursor (`after=<last-seen message_id>`) and
        a rolling buffer of the last `limit` messages (`_ChannelTail`):
        the first call on a channel pages forward from the very start,
        one `_MAX_HISTORY_PAGE`-sized request at a time, until a page
        comes back short of a full page (the real end of the channel,
        for now); every later call on the same channel resumes from
        exactly where the previous one left off, so it only fetches
        what's new since the last mention -- bounded, incremental work
        per call, not a re-page of the whole channel every time.

        Changing `limit` between calls on the same channel resizes the
        rolling buffer (trimmed/kept as-is) without discarding the
        cursor, so switching how much context a caller wants never
        forces a re-fetch of history already paged through.
        """
        tail = self._tails.get(channel_id)
        if tail is None:
            tail = _ChannelTail(buffer=deque(maxlen=limit))
            self._tails[channel_id] = tail
        elif tail.buffer.maxlen != limit:
            tail.buffer = deque(tail.buffer, maxlen=limit)

        workspace_id = self._require_credentials().workspace_id
        page_size = min(limit, _MAX_HISTORY_PAGE)
        # Bounded for the same reason the mention drain is: a server that
        # never returns a short or empty page (buggy, or hostile) would
        # otherwise page here forever. The tail only needs `limit`
        # messages, so this ceiling is unreachable against a conformant
        # server -- it exists purely so a non-conformant one can't hang
        # the agent.
        for _ in range(_MAX_HISTORY_PAGES):
            params: dict[str, Any] = {"limit": page_size}
            if tail.cursor is not None:
                params["after"] = tail.cursor
            page = list(
                self._request(
                    "GET",
                    f"/workspaces/{workspace_id}/channels/{channel_id}/messages",
                    params=params,
                )
            )
            if not page:
                break
            tail.buffer.extend(page)
            tail.cursor = page[-1]["Message"]["message_id"]
            if len(page) < page_size:
                break
        return list(tail.buffer)

    def post(self, channel_id: str, text: str) -> dict[str, Any]:
        """`POST /workspaces/{ws}/channels/{ch}/messages`."""
        workspace_id = self._require_credentials().workspace_id
        return dict(
            self._request(
                "POST",
                f"/workspaces/{workspace_id}/channels/{channel_id}/messages",
                json_body={"message_text": text},
            )
        )

    def pending_mentions(self) -> list[dict[str, Any]]:
        """`GET /mentions`: the caller's own unacknowledged mentions."""
        return list(self._request("GET", "/mentions"))

    def ack(self, mention_id: str) -> None:
        """`POST /mentions/{mention_id}/ack`."""
        self._request("POST", f"/mentions/{mention_id}/ack")

    # -- live events --------------------------------------------------------

    def _ws_url(self) -> str:
        workspace_id = self._require_credentials().workspace_id
        scheme = "wss" if self.config.smac_url.startswith("https://") else "ws"
        host_and_port = self.config.smac_url.split("://", 1)[1]
        return (
            f"{scheme}://{host_and_port}/ws/workspaces/{workspace_id}/members/me/events"
        )

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed JSON frames from this member's private event feed
        (mentions pushed live) as they arrive. One connection, no
        reconnect -- `agent.py` (Task 4) wraps this with reconnect/backoff
        and a catch-up drain of `pending_mentions()`.
        """
        api_key = self._require_credentials().api_key
        async with websockets.connect(
            self._ws_url(), additional_headers={"X-API-Key": api_key}
        ) as websocket:
            async for raw in websocket:
                yield json.loads(raw)
