"""Background WebSocket listener threads: `ChannelFeed` and `EventBell`.

Both connect via `websockets.sync.client` on a dedicated **daemon**
thread -- never the Textual event loop, which every blocking call in
this app is kept off of (see `smac_cli/app.py`'s module docstring). Each
delivers every decoded JSON payload to a caller-supplied callback
*directly on that background thread*; hopping back onto the UI thread
(`App.call_from_thread`) is the callback's own job, not this module's --
`smac_cli.live` intentionally never imports `textual`, so it stays
independently testable (a fake/echo WebSocket server, no Textual app
required) and reusable outside the TUI.

**Reconnection:** any failure at all -- a dropped connection, a refused
one, a bad `ws_url_provider` -- is treated identically: back off (1s,
doubling, capped at 30s, +0-25% jitter so many clients reconnecting at
once don't thunder-herd) and try again, forever, until `stop()`. The
`except Exception` in `_run` is deliberately broad: a reconnect loop
that can itself crash the daemon thread on some unanticipated failure
mode defeats the entire point of "auto-reconnect", and in the shell's
own unit tests (`tests/test_tui_shell.py`) the stub `FakeApi` doesn't
even implement `ws_channel_url`/`ws_events_url` -- the resulting
`AttributeError` is exactly the kind of failure this loop must absorb
rather than propagate.
"""

from __future__ import annotations

import json
import random
import threading
from typing import Any, Callable

from websockets.sync.client import ClientConnection, connect as ws_connect

_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 30.0
_JITTER_FRACTION = 0.25
#: How long a single `recv()` blocks before looping back to recheck
#: `stop()` -- small enough that `stop()` takes effect promptly, large
#: enough to not busy-loop.
_RECV_POLL_SECONDS = 1.0


class _ReconnectingFeed:
    """Shared reconnect-loop machinery behind `ChannelFeed`/`EventBell`.

    `ws_url_provider` is called fresh on every (re)connect attempt --
    `SmacApi.ws_channel_url`/`.ws_events_url` mint a freshly-refreshed
    token per call (see `smac_cli/api.py`), so a token that went stale
    while this feed sat disconnected is never replayed.
    """

    def __init__(
        self,
        ws_url_provider: Callable[[], str],
        on_payload: Callable[[dict[str, Any]], None],
    ) -> None:
        self._ws_url_provider = ws_url_provider
        self._on_payload = on_payload
        self._stop_event = threading.Event()
        self._ws_lock = threading.Lock()
        self._ws: ClientConnection | None = None
        self._ever_connected = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Signal the background thread to stop and unblock it promptly.

        Closing the live socket (if one is open right now) interrupts an
        in-progress `recv()` immediately rather than waiting out its
        poll timeout; a socket that's mid-(re)connect-attempt or asleep
        in the backoff wait notices `_stop_event` on its own within
        `_RECV_POLL_SECONDS`/one backoff tick. Does not join the thread
        -- it's a daemon, so process exit (or the next test) reaps it
        either way.
        """
        self._stop_event.set()
        with self._ws_lock:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass

    def _on_disconnected(self) -> None:
        """Hook for a subclass to react to a connection drop. No-op by default."""

    def _on_reconnected(self) -> None:
        """Hook for a subclass to react to a successful *re*-connect (not
        the very first connect of this feed's lifetime). No-op by default."""

    def _run(self) -> None:
        backoff = _INITIAL_BACKOFF
        while not self._stop_event.is_set():
            try:
                url = self._ws_url_provider()
                with ws_connect(url, open_timeout=10) as ws:
                    with self._ws_lock:
                        self._ws = ws
                    if self._ever_connected:
                        self._on_reconnected()
                    self._ever_connected = True
                    backoff = _INITIAL_BACKOFF  # a healthy connect resets it
                    while not self._stop_event.is_set():
                        try:
                            raw = ws.recv(timeout=_RECV_POLL_SECONDS)
                        except TimeoutError:
                            continue
                        payload = json.loads(raw)
                        self._on_payload(payload)
            except Exception:
                if self._ever_connected:
                    self._on_disconnected()
            finally:
                with self._ws_lock:
                    self._ws = None

            if self._stop_event.is_set():
                break
            jitter = random.uniform(0, backoff * _JITTER_FRACTION)
            self._stop_event.wait(backoff + jitter)
            backoff = min(backoff * 2, _MAX_BACKOFF)


class ChannelFeed(_ReconnectingFeed):
    """The live feed for one channel's WebSocket (`/ws/.../channels/{id}`).

    Delivers every message payload broadcast on the channel to
    `on_payload`, plus two synthetic control payloads the server never
    sends -- `{"event": "disconnected"}` right when the connection
    drops, and `{"event": "reconnected"}` on the following successful
    reattach -- so the caller can show the spec's system lines and
    trigger a history refresh through the exact same `on_payload`
    conduit, keeping the constructor's 2-argument shape
    (`ChannelFeed(ws_url_provider, on_payload)`) intact rather than
    growing extra callback parameters for what is a rare event.
    """

    def _on_disconnected(self) -> None:
        self._on_payload({"event": "disconnected"})

    def _on_reconnected(self) -> None:
        self._on_payload({"event": "reconnected"})


class EventBell(_ReconnectingFeed):
    """The caller's private mention-events feed
    (`/ws/.../members/me/events`) -- delivers every `{"event":
    "mention", ...}` envelope to `on_event`. Reconnects silently (no
    synthetic control payloads): unlike a channel's message history,
    there's no "refresh" needed for a feed of point-in-time
    notifications, and the spec's reconnect UI is scoped to the channel
    feed only.
    """
