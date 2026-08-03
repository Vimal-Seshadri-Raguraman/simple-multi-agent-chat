"""Payload -> display line: `message_line` and `bell_line`.

Both are pure functions of the wire-shaped dicts the rest of `smac_cli`
already deals in (`SmacApi.messages()`/`.post()`'s return value, the
channel WebSocket's per-message payload, and the mention-events feed's
`{"event": "mention", ..., "message": {...}}` envelope -- see
`app/schemas.py:build_message_payload` and `app/mentions.py:
build_mention_event` for the server-side shapes these mirror).

**The sender-handle gap:** `build_message_payload`'s `"Sender"` sub-dict
carries only `member_id` and `member_name` -- never `handle` -- because
the server has no per-message reason to resolve it. But the spec's
message-line format (`[HH:MM] handle: text`) and bell-line format need
the sender's *handle*, not their display name (`member_name` is "Vimal
Raguraman"; `handle` is "vraguraman", and the two are never assumed
equal). `smac_cli.app` bridges this by maintaining a per-workspace
`member_id -> handle` directory (`GET /workspaces/{id}/members`) and
enriching `payload["Sender"]["handle"]` onto every payload before
handing it to these functions -- so `message_line`/`bell_line` stay pure
single-argument functions while still being able to show the right
handle. When enrichment hasn't happened (or a member's handle can't be
resolved, e.g. right after a restart with a cold directory), both
functions fall back to `member_name` rather than raising or showing a
blank -- something is always better than a crash or an empty label.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

#: A canonicalized mention token, e.g. `<@3f9c...>`. Deliberately generic
#: (any non-`>` run of characters) rather than hardcoded to the server's
#: UUID shape (`app/mentions.py:TOKEN_PATTERN`) -- a token this loose
#: still only ever matches what the server actually emits, and staying
#: loose means a future id-format change on the server side doesn't
#: silently stop rendering mentions correctly here.
_MENTION_TOKEN = re.compile(r"<@([^>]+)>")


def _sender_handle(sender: dict[str, Any]) -> str:
    """The sender's handle if enriched onto the payload, else `member_name`."""
    handle = sender.get("handle")
    if handle:
        return str(handle)
    return str(sender.get("member_name", ""))


def _format_hh_mm(timestamp: str) -> str:
    """`HH:MM` from an ISO 8601 timestamp (`Message.created_at.isoformat()`).

    No timezone conversion is applied -- the server stores naive UTC
    (`app.models.utcnow`) and this is a same-machine client (spec's
    Out of Scope), so the clock value is shown as recorded rather than
    guessing at a conversion that isn't actually meaningful here.
    """
    return datetime.fromisoformat(timestamp).strftime("%H:%M")


def _replace_mentions(
    text: str,
    mentions: list[dict[str, Any]],
    extra_handles: dict[str, str] | None = None,
) -> str:
    """`<@member_id>` -> `@handle` for every id in `mentions` (falling back
    to `extra_handles` for an id `mentions` doesn't cover); anything else
    (an id with no entry in either -- e.g. the member left, or a spoofed
    literal token that never resolved server-side either) is left exactly
    as stored, per the spec's "unknown token -> literal" rule.

    **Why `extra_handles` exists (finding G):** `build_message_payload`
    excludes the sender from its own `mentions` array (the server has no
    reason to tell you who you already know you are) -- but `canonicalize`
    (`app/mentions.py`) still rewrites a self-mention like `@vraguraman`
    (posted by vraguraman) into `<@own-member-id>` same as any other
    mention. Without a fallback, that token has no entry in `mentions` and
    renders as the literal `<@own-member-id>` even though the reader is
    the very person it refers to. `smac_cli.app` passes `{self_member_id:
    self_handle}` here so a self-mention resolves to `@yourhandle` like
    every other mention, instead of leaking a raw id.
    """
    handles_by_id = {m["member_id"]: m["handle"] for m in mentions}
    if extra_handles:
        for member_id, handle in extra_handles.items():
            handles_by_id.setdefault(member_id, handle)

    def _replace(match: "re.Match[str]") -> str:
        handle = handles_by_id.get(match.group(1))
        return f"@{handle}" if handle is not None else match.group(0)

    return _MENTION_TOKEN.sub(_replace, text)


def message_line(
    payload: dict[str, Any], extra_handles: dict[str, str] | None = None
) -> str:
    """`[HH:MM] handle: text` for one channel message payload.

    `<@member_id>` tokens in the message text are rewritten to `@handle`
    using the payload's own `mentions` array (present on every message
    payload per `build_message_payload`, empty when nothing resolves),
    falling back to `extra_handles` (see `_replace_mentions`'s docstring
    -- this is how a self-mention resolves to `@yourhandle` instead of a
    raw `<@uuid>`, finding G); a token with no matching entry in either
    is left in the text literally.
    """
    sender = payload["Sender"]
    handle = _sender_handle(sender)
    hh_mm = _format_hh_mm(payload["timestamp"])
    text = _replace_mentions(
        payload["Message"]["message_text"],
        payload.get("mentions") or [],
        extra_handles,
    )
    return f"[{hh_mm}] {handle}: {text}"


def bell_line(event: dict[str, Any]) -> str:
    """`🔔 you were mentioned in #<channel> by @<handle>` for one mention event.

    `event` is the mention-events feed's envelope
    (`{"event": "mention", ..., "message": {...}}`, `app.mentions.
    build_mention_event`'s shape) -- the channel and sender both come
    from the embedded message payload, since that's the message the
    mention token actually appeared in.
    """
    message = event["message"]
    channel_name = message["Channel"]["channel_name"]
    handle = _sender_handle(message["Sender"])
    return f"🔔 you were mentioned in #{channel_name} by @{handle}"
