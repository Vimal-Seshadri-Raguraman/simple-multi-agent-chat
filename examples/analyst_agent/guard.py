"""`Guard`: the citizenship rules that stop this example from becoming a
runaway agent-to-agent loop (design doc §2, "Citizenship guard").

Four rules, checked in this order:

1. Never reply to your own message.
2. Stay under `max_replies_per_min` (a sliding 60-second window, an
   injectable `clock` so tests are deterministic -- no real sleeps).
3. Cap consecutive agent-to-agent turns at `max_hops`. "Loop depth" is
   the count of consecutive *trailing* entries in `recent` (the channel
   history immediately preceding this event, oldest to newest, NOT
   including the event itself) whose sender is either this agent or a
   known agent -- walking backward from the newest entry and stopping at
   the first sender that is not. A human message anywhere in that
   trailing run stops the count right there (resets it), matching the
   design doc's "depth reset by any human message".
4. Otherwise, allowed.

Sender *type* (human vs. agent) is never on SMAC's wire: `Sender` only
carries `member_id`/`member_name` (agent API keys are capped to
post/read/ack, so `GET /members` 403s for an agent -- see
`smac_link.py`'s module docstring). So rule 3 can't infer "is this an
agent" from the payload alone; the caller supplies `known_agent_ids`, a
set of member ids it independently knows are agents. Anyone not in
`known_agent_ids ∪ {me}` is treated as human for the purposes of this
count. `agent.py`'s `Agent` -- the one caller in this example -- keeps
this set permanently EMPTY: growing it from "senders I've answered"
(an earlier version's approach) is unsound with no sender-type signal
on the wire, since a human's second question looks identical to an
agent's reply in a chain. See `agent.py`'s module docstring for the
concrete failure that caused and why the rate cap, not hop depth, is
this example's real enforced backstop. The parameter stays as the seam
a caller WITH a real sender-type signal (a future SDK improvement)
could use correctly.

Every blocked `Decision.reason` is a short, plain string
("own message", "loop depth 3", "rate cap 6/min") -- Task 5/6's inner
view renders it verbatim, so it needs to already read like a sentence
fragment a human would understand at a glance.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

_RATE_WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class Sender:
    """Wire shape of a message's `Sender` field -- `member_id`/
    `member_name` only, exactly what `app/schemas.py`'s
    `build_message_payload` puts on the wire. No `type`, no `handle`."""

    member_id: str
    member_name: str


@dataclass(frozen=True)
class Message:
    """The minimal shape `Guard.check` needs, for both its `event`
    argument (the mention that would trigger a reply) and each entry of
    `recent` (preceding channel history): who sent it."""

    sender: Sender


@dataclass(frozen=True)
class Decision:
    """The verdict from `Guard.check`. `reason` is `None` exactly when
    `allowed` is `True` -- there is nothing to explain about a message
    the agent is free to answer."""

    allowed: bool
    reason: str | None = None


class Guard:
    """One guard per running agent identity. Stateful across calls: it
    remembers reply timestamps (for the rate cap) between `check()`
    calls, via `record_reply()`, which the caller invokes only after an
    actual reply is posted -- `check()` itself never mutates state, so
    calling it repeatedly to "peek" is always safe.
    """

    def __init__(
        self,
        max_replies_per_min: int,
        max_hops: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_replies_per_min = max_replies_per_min
        self.max_hops = max_hops
        self._clock = clock
        self._reply_times: list[float] = []

    def record_reply(self) -> None:
        """Record that a reply was just sent, starting its minute on the
        rate-cap clock. Call this once per actual reply -- never inside
        `check()`, which must stay side-effect-free."""
        self._reply_times.append(self._clock())

    def _replies_in_window(self) -> int:
        """Prune reply timestamps older than the sliding window and
        return how many remain -- the pruning is what makes the rate cap
        recover once the injected clock advances past 60s (see
        `test_rate_cap_blocks_the_seventh_reply_in_a_minute`)."""
        now = self._clock()
        cutoff = now - _RATE_WINDOW_SECONDS
        self._reply_times = [t for t in self._reply_times if t > cutoff]
        return len(self._reply_times)

    @staticmethod
    def _loop_depth(
        recent: Sequence[Message],
        me_member_id: str,
        known_agent_ids: AbstractSet[str],
    ) -> int:
        """Consecutive trailing `recent` entries sent by this agent or a
        known agent, walking backward from the newest entry. Stops (and
        so effectively resets to that point) at the first entry whose
        sender is neither -- i.e. a human message."""
        agent_like = set(known_agent_ids) | {me_member_id}
        depth = 0
        for message in reversed(recent):
            if message.sender.member_id not in agent_like:
                break
            depth += 1
        return depth

    def check(
        self,
        event: Message,
        me_member_id: str,
        recent: Sequence[Message],
        known_agent_ids: AbstractSet[str] = frozenset(),
    ) -> Decision:
        """Decide whether to answer `event` (the mention that would
        trigger a reply). Pure/read-only: never records a reply itself --
        call `record_reply()` separately once the reply actually posts.
        """
        if event.sender.member_id == me_member_id:
            return Decision(False, "own message")

        if self._replies_in_window() >= self.max_replies_per_min:
            return Decision(False, f"rate cap {self.max_replies_per_min}/min")

        depth = self._loop_depth(recent, me_member_id, known_agent_ids)
        if depth >= self.max_hops:
            return Decision(False, f"loop depth {depth}")

        return Decision(True, None)
