"""In-memory sliding-window rate limiting for message posting.

This is the agent-loop circuit breaker: a misbehaving or looping agent
that hammers `POST .../messages` gets cut off per-member before any DB
work happens. State lives entirely in process memory by design — it
resets on restart, which is fine for a local-first, single-process
deployment. It is not shared across workers/processes.
"""

import os
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Caps the number of events per key within a rolling time window.

    Each key (e.g. a member id) gets its own independent budget of
    `max_events` within any `window_seconds`-long sliding window.
    """

    def __init__(self, max_events: int, window_seconds: float) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Record an event for `key` if it's within budget; return whether allowed.

        Prunes timestamps older than the window before checking, so the
        window actually slides rather than being a fixed bucket.
        """
        now = time.monotonic()
        events = self._events[key]
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= self.max_events:
            return False
        events.append(now)
        return True


post_limiter = SlidingWindowRateLimiter(
    int(os.getenv("RATE_LIMIT_POSTS", "10")),
    float(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "10")),
)

#: `POST /agents/join` (SMAC-92) is unauthenticated -- there is no member_id
#: to key a per-caller budget by yet, so this one is keyed by client IP
#: instead. Caps brute-forcing a bogus/expired agent invite code.
agent_join_limiter = SlidingWindowRateLimiter(
    int(os.getenv("RATE_LIMIT_AGENT_JOIN", "5")),
    float(os.getenv("RATE_LIMIT_AGENT_JOIN_WINDOW_SECONDS", "60")),
)
