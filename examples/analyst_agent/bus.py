"""`Bus`: a tiny in-process async pub/sub of typed `Event`s (design doc
§2). Every view (inner activity stream, headless JSON-lines printer,
Task 6's TUI) subscribes to the same `Bus` instance; nothing else couples
them together -- `agent.py`/`brain.py` just call `publish()` and never
know or care who, if anyone, is listening.

Two safety properties this module owns:

- **A late-attaching view isn't blank.** `history(limit)` replays the
  last `BUS_HISTORY_MAX` events (a bounded `deque`, so a long-running
  agent process never grows this backlog without limit) -- switching to
  the inner-view pane after the agent has been running a while still
  shows recent activity, not an empty pane.
- **A slow or wedged subscriber can never stall the agent.** Each
  subscriber gets its own `asyncio.Queue` (so one reader's pace never
  affects another's), and that queue is bounded. `publish()` is
  non-blocking always: if a subscriber's queue is full, the new event is
  DROPPED for that subscriber only (drop-newest) rather than backing up
  `publish()` or growing memory without bound. `history()` is the
  recovery path for a subscriber that falls behind and resumes.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: Backlog size for `Bus.history()`. Bounds memory for a long-running
#: agent process -- a late-attaching view sees at most this many past
#: events, never the whole run's history.
BUS_HISTORY_MAX = 500

#: Per-subscriber queue depth. See the module docstring's drop policy.
_SUBSCRIBER_QUEUE_MAX = 200


@dataclass(frozen=True)
class Event:
    """One thing that happened, for views to render. `fields` is a
    free-form payload (e.g. `{"channel": "#ops", "reason": "loop depth
    3"}`) -- deliberately untyped here so `bus.py` never needs to change
    when `agent.py`/`brain.py` add a new kind of event. Never put a
    secret (API key, Anthropic key) in `fields`: the inner view and
    `--headless` JSON-lines mode render it as-is."""

    kind: str
    at: datetime
    fields: dict[str, Any] = field(default_factory=dict)


class Bus:
    """See the module docstring for the backlog/drop-policy guarantees.
    `publish()` is synchronous (call it from anywhere -- no `await`
    needed); `subscribe()` is an async generator.
    """

    def __init__(
        self,
        *,
        history_max: int = BUS_HISTORY_MAX,
        subscriber_queue_max: int = _SUBSCRIBER_QUEUE_MAX,
    ) -> None:
        self._history: deque[Event] = deque(maxlen=history_max)
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._subscriber_queue_max = subscriber_queue_max

    def publish(self, kind: str, **fields: Any) -> Event:
        """Build an `Event` timestamped now (UTC), append it to the
        backlog, and hand it to every live subscriber. Never blocks and
        never raises for a full/wedged subscriber -- see the module
        docstring's drop-newest policy."""
        event = Event(kind=kind, at=datetime.now(timezone.utc), fields=dict(fields))
        self._history.append(event)
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop-newest for this one wedged subscriber; history() recovers it
        return event

    async def subscribe(self) -> AsyncGenerator[Event, None]:
        """Yield events published from this point on, forever, until the
        consumer stops iterating (breaking out of `async for`, calling
        `.aclose()`, or the task holding it being cancelled) -- the
        `finally` unsubscribes cleanly either way, so a departed view
        never leaks a queue. Typed as `AsyncGenerator` rather than the
        narrower `AsyncIterator` precisely so callers can call
        `.aclose()` for deterministic cleanup (e.g. in tests).

        Does NOT replay `history()` -- callers that want backlog call
        `history()` first, then `subscribe()` for what comes next.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._subscriber_queue_max)
        self._subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(queue)

    def history(self, limit: int) -> list[Event]:
        """The most recent `limit` events, oldest first, capped at
        whatever the backlog actually holds (at most `BUS_HISTORY_MAX`
        regardless of how large `limit` is)."""
        if limit <= 0:
            return []
        return list(self._history)[-limit:]
