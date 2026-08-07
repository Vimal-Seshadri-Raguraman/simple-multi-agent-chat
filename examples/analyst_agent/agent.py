"""`Agent`: the mention loop that ties `smac_link.py`, `brain.py`,
`guard.py`, and `bus.py` together (design doc §2, "The mention loop").

One `Agent` per running identity. Two entry points:

- `run()` — the real loop against a live `SmacLink`: on every (re)connect,
  drain `pending_mentions()` (catch-up) BEFORE consuming live frames from
  `link.events()` (live), reconnecting with exponential backoff+jitter on
  every drop. This is the "catch-up-then-live" discipline the design doc
  calls out as shared with the TUI and web client.
- `chat(text)` — direct chat with the same brain, on its own thread, that
  NEVER touches SMAC (no `link.history`/`link.post`/`link.ack` call
  anywhere in that method).

Every mention `handle_mention()` starts processing is acked EXACTLY once
before it returns -- guard-blocked, paused, successfully replied, or a
`brain.think()`/`link.post()` failure -- via a `try/finally`, so a mention
this agent has already looked at never sits in the inbox forever. A
`mention_id` already seen (redelivered by a drain-then-live race, or any
other retry) is a silent no-op the second time: it was acked the first
time. "Seen" is tracked in a BOUNDED map (oldest evicted first) so a
long-running process's memory never grows without bound.

`known_agent_ids` (passed to `Guard.check`, see `guard.py`'s module
docstring for why the caller has to supply this) grows from every sender
this agent has actually replied to -- the only signal available, since an
agent's API key can't list workspace members to ask "is this a human or
another agent."

Bus events published here (rendered verbatim by the inner view and
`--headless` JSON-lines mode -- never a secret, see `bus.py`'s module
docstring): `mention`, `context`, `skipped`, `paused_skip`, `posted`,
`acked`, `chat_in`, `chat_out`, `disconnected`, `reconnected`, `error`.
`model_call`/`token`/`model_done` come from `brain.think()` itself.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any, Protocol

from analyst_agent.bus import Bus
from analyst_agent.brain import PERSONA, Reply, format_history
from analyst_agent.config import Config
from analyst_agent.guard import Guard, Message as GuardMessage, Sender as GuardSender
from analyst_agent.smac_link import Credentials

#: Reconnect backoff bounds (design doc: "exponential backoff (1s->30s,
#: jitter)"). `attempt` 0 is the first reconnect try right after a drop.
_BACKOFF_MIN_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 30.0

#: Bound on `Agent`'s mention-idempotency memory -- see the module
#: docstring. Generous enough that no real workspace's mention volume
#: between reconnects would plausibly evict a still-relevant id.
_DEFAULT_SEEN_MAX = 2000


def _default_backoff_seconds(attempt: int) -> float:
    """Exponential backoff with full jitter: a random delay between 0
    and `min(30, 1 * 2**attempt)` seconds. Full jitter (not
    "half jitter" or none) avoids every disconnected client racing to
    reconnect at exactly the same moment."""
    ceiling = min(_BACKOFF_MAX_SECONDS, _BACKOFF_MIN_SECONDS * (2**attempt))
    return random.uniform(0, ceiling)


class Disconnect:
    """Sentinel for `run_once_with`'s scripted event list (tests only):
    simulates the live connection dropping mid-stream, so the
    reconnect/catch-up-drain discipline is unit-testable without a real
    WebSocket or a real sleep. Carries no data -- identity is all that
    matters."""


class SmacLinkLike(Protocol):
    """The subset of `SmacLink`'s surface `Agent` depends on, as a
    Protocol (structural typing) rather than the concrete class -- so
    tests can inject a plain fake without also standing up the real
    `httpx.Client`/credentials-file machinery `SmacLink.__init__` and
    `join_or_load()` involve."""

    credentials: Credentials | None

    def history(self, channel_id: str, limit: int = 20) -> list[dict[str, Any]]: ...
    def post(self, channel_id: str, text: str) -> dict[str, Any]: ...
    def pending_mentions(self) -> list[dict[str, Any]]: ...
    def ack(self, mention_id: str) -> None: ...
    def events(self) -> AsyncIterator[dict[str, Any]]: ...


class BrainLike(Protocol):
    """The subset of `Brain`'s surface `Agent` depends on -- same
    rationale as `SmacLinkLike`."""

    async def think(
        self,
        system: str,
        history: list[dict[str, str]],
        trigger: str,
        thread: list[dict[str, str]] | None = None,
    ) -> Reply: ...


def _guard_message(sender_payload: dict[str, Any]) -> GuardMessage:
    """Map a wire `Sender` payload (`{"member_id": ..., "member_name":
    ...}`, possibly `member_id: None` for a removed member -- see
    `app/schemas.py`'s `build_message_payload`) to `guard.py`'s minimal
    `Message`/`Sender` shape. `None` coerces to `""`, which can never
    equal a real member_id (this agent's own or a known agent's), so a
    removed member's history entries are never mistaken for a step in
    an agent-to-agent loop chain."""
    return GuardMessage(
        sender=GuardSender(
            member_id=sender_payload.get("member_id") or "",
            member_name=sender_payload.get("member_name") or "",
        )
    )


class Agent:
    """One running agent identity: `link` (already joined -- see
    `SmacLinkLike`) is the sole source of this agent's own `member_id`/
    `handle`/`workspace_name`, read once at construction from
    `link.credentials`.

    `paused` (design doc's F4): when `True`, `handle_mention` still logs
    a `paused_skip` event and acks every mention it sees (so nothing
    piles up in the inbox while paused), it just never calls the guard,
    the brain, or `link.post`.

    `sleep`/`backoff_seconds` are exposed purely for tests: inject a fake
    `sleep` that returns immediately (recording the delay it was asked
    for) so `run()`'s reconnect loop never actually waits in a test.
    """

    def __init__(
        self,
        link: SmacLinkLike,
        brain: BrainLike,
        guard: Guard,
        bus: Bus,
        config: Config,
        *,
        seen_max: int = _DEFAULT_SEEN_MAX,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff_seconds: Callable[[int], float] = _default_backoff_seconds,
    ) -> None:
        credentials = link.credentials
        if credentials is None:
            raise RuntimeError(
                "SmacLink must be joined (join_or_load()) before building an Agent"
            )
        self.link = link
        self.brain = brain
        self.guard = guard
        self.bus = bus
        self.config = config
        self.paused = False

        self._me_member_id = credentials.member_id
        #: This agent's own handle -- public (not sensitive: it's the
        #: display name every other member already sees on every message
        #: this agent posts), unlike `_me_member_id`/`_workspace_name`
        #: below, which are internal wiring for the guard/persona only.
        self.handle = credentials.handle
        self._workspace_name = credentials.workspace_name
        self._known_agent_ids: set[str] = set()
        self._chat_thread: list[dict[str, str]] = []

        # Bounded idempotency memory (insertion-ordered dict-as-set --
        # see the module docstring). `seen_max` is a constructor kwarg
        # purely so a test can shrink it to exercise eviction cheaply.
        self._seen_mention_ids: dict[str, None] = {}
        self._seen_max = seen_max

        self._sleep = sleep
        self._backoff_seconds = backoff_seconds

    # -- idempotency ---------------------------------------------------

    def _already_seen(self, mention_id: str) -> bool:
        return mention_id in self._seen_mention_ids

    def _remember(self, mention_id: str) -> None:
        self._seen_mention_ids[mention_id] = None
        if len(self._seen_mention_ids) > self._seen_max:
            oldest = next(iter(self._seen_mention_ids))
            del self._seen_mention_ids[oldest]

    # -- the mention loop ------------------------------------------------

    async def handle_mention(self, event: dict[str, Any]) -> None:
        """Handle one mention frame -- from `pending_mentions()`'s drain
        or a live WebSocket frame, same wire shape either way (see
        `app/mentions.py`'s `build_mention_event`): guard check -> fetch
        `?limit=20` channel history for context -> `brain.think()` ->
        `link.post()` -> `link.ack()`.

        See the module docstring for the acked-exactly-once and
        idempotent-redelivery guarantees this method is responsible for.
        """
        mention_id = event["mention_id"]
        if self._already_seen(mention_id):
            return
        self._remember(mention_id)

        message = event["message"]
        channel_id = message["Channel"]["channel_id"]
        sender_payload = message["Sender"]
        text = message["Message"]["message_text"]

        self.bus.publish(
            "mention",
            mention_id=mention_id,
            channel=channel_id,
            sender=sender_payload.get("member_name"),
        )

        posted = False
        try:
            if self.paused:
                self.bus.publish(
                    "paused_skip", mention_id=mention_id, channel=channel_id
                )
                return

            history_payloads = self.link.history(channel_id)
            self.bus.publish("context", channel=channel_id, count=len(history_payloads))

            recent = [_guard_message(p["Sender"]) for p in history_payloads]
            trigger_message = _guard_message(sender_payload)
            decision = self.guard.check(
                trigger_message,
                self._me_member_id,
                recent,
                known_agent_ids=self._known_agent_ids,
            )
            if not decision.allowed:
                self.bus.publish(
                    "skipped",
                    mention_id=mention_id,
                    channel=channel_id,
                    reason=decision.reason,
                )
                return

            system = PERSONA(self.handle, self._workspace_name)
            reply = await self.brain.think(
                system=system,
                history=format_history(history_payloads),
                trigger=text,
            )

            self.link.post(channel_id, reply.text)
            self.guard.record_reply()
            sender_id = sender_payload.get("member_id")
            if sender_id:
                self._known_agent_ids.add(sender_id)
            self.bus.publish("posted", channel=channel_id, text=reply.text)
            posted = True
        finally:
            # Runs on every path above -- including a `brain.think()` or
            # `link.post()` exception propagating out of the `try` -- so
            # a mention this agent has started on is never left unacked.
            self.link.ack(mention_id)
            if posted:
                self.bus.publish("acked", mention_id=mention_id)

    async def chat(self, text: str) -> str:
        """Direct chat: the same brain, this agent's own running thread,
        that NEVER touches SMAC -- no `link.history`/`link.post`/
        `link.ack` call anywhere in this method, so a chat message can
        never leak into a workspace channel (design doc: "the chat
        thread does not post to SMAC and does not mix into channel
        context")."""
        self.bus.publish("chat_in", text=text)
        system = PERSONA(self.handle, self._workspace_name)
        reply = await self.brain.think(
            system=system, history=[], trigger=text, thread=self._chat_thread
        )
        self._chat_thread.append({"role": "user", "content": text})
        self._chat_thread.append({"role": "assistant", "content": reply.text})
        self.bus.publish("chat_out", text=reply.text)
        return reply.text

    async def _safe_handle(self, event: dict[str, Any]) -> None:
        """`handle_mention`, but a failure (a malformed frame, a
        `BrainError`, a `RequestFailed` posting or acking) becomes an
        `error` bus event instead of killing the mention loop -- one bad
        mention must never take the whole agent process down. (The ack
        itself still happened, or was attempted, inside
        `handle_mention`'s own `finally` before this ever sees the
        exception.)
        """
        try:
            await self.handle_mention(event)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - deliberately broad: keep the loop alive
            self.bus.publish("error", message=str(exc))

    async def run(self, *, once: bool = False) -> None:
        """The real mention loop against a live `SmacLink`: on connect
        (and every reconnect), drain `pending_mentions()` (catch-up)
        before consuming live frames from `link.events()` (live) --
        forever, reconnecting with exponential backoff+jitter
        (`disconnected/reconnected` published either side of the wait)
        on every drop or on the socket ending cleanly.

        `once=True` (the CLI's `--once`) returns as soon as this call has
        processed exactly one mention -- from the drain or a live frame,
        whichever comes first -- instead of running forever, so an
        integration test can drive it deterministically.
        """
        limit = 1 if once else None
        handled = 0
        attempt = 0
        while True:
            try:
                for pending_event in self.link.pending_mentions():
                    await self._safe_handle(pending_event)
                    handled += 1
                    if limit is not None and handled >= limit:
                        return
                if attempt:
                    self.bus.publish("reconnected")
                attempt = 0
                async for raw in self.link.events():
                    await self._safe_handle(raw)
                    handled += 1
                    if limit is not None and handled >= limit:
                        return
                # `events()` ended without raising -- the socket closed
                # cleanly (e.g. server restart). Still a disconnect from
                # this loop's point of view: fall through to reconnect.
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.bus.publish("error", message=str(exc))
            self.bus.publish("disconnected", reason="connection closed")
            delay = self._backoff_seconds(attempt)
            attempt += 1
            await self._sleep(delay)

    async def run_once_with(self, events: Sequence[Any]) -> None:
        """TEST-ONLY seam: drive one or more connection cycles from a
        scripted, finite list of frames instead of a real WebSocket, so
        the reconnect/drain discipline (catch-up-then-live, drain again
        on every reconnect, no double-handling across the drain/live
        boundary) is unit-testable with no network and no real sleep.

        A `Disconnect()` in `events` simulates the connection dropping:
        the same catch-up dance runs again (`pending_mentions()` drained
        before resuming) using whatever is left in `events` after it.
        Returns once `events` is exhausted with no trailing `Disconnect`.
        """
        remaining = list(events)
        reconnecting = True
        while reconnecting:
            reconnecting = False
            for pending_event in self.link.pending_mentions():
                await self._safe_handle(pending_event)
            while remaining:
                item = remaining.pop(0)
                if isinstance(item, Disconnect):
                    self.bus.publish("disconnected", reason="connection lost")
                    reconnecting = True
                    break
                await self._safe_handle(item)
