"""`smac_cli.render`: payload -> display line, pure functions.

`message_line` and `bell_line` never touch the network or the app -- they
take the wire-shaped dicts `SmacApi`/the WebSocket feeds hand back and
return the exact string the TUI writes into the body. Both fall back to
the server's `Sender.member_name` when a `"handle"` key hasn't been
enriched onto the payload yet (see `smac_cli.app._enrich_sender_handle`):
the message-payload wire shape (`app/schemas.py:build_message_payload`)
carries only `member_name` for the sender, never `handle`, so the app
layer resolves and injects `handle` from its own per-workspace member
directory before rendering -- these tests cover both the enriched
(happy path) and un-enriched (fallback) cases.
"""

from __future__ import annotations

from smac_cli.render import bell_line, message_line


def _payload(
    *,
    timestamp: str = "2026-08-03T14:02:30.123456",
    sender_id: str = "sender-1",
    sender_name: str = "Vimal Raguraman",
    sender_handle: str | None = "vraguraman",
    text: str = "hello",
    mentions: list[dict] | None = None,
    channel_name: str = "general",
) -> dict:
    sender: dict = {"member_id": sender_id, "member_name": sender_name}
    if sender_handle is not None:
        sender["handle"] = sender_handle
    return {
        "timestamp": timestamp,
        "workspace": {"workspace_id": "w1", "workspace_name": "AI Finance Co"},
        "Channel": {"channel_id": "c1", "channel_name": channel_name},
        "Sender": sender,
        "Message": {"message_id": "m1", "message_text": text},
        "mentions": mentions or [],
        "channel_refs": [],
    }


# --------------------------------------------------------------------------
# message_line
# --------------------------------------------------------------------------


def test_message_line_basic_format() -> None:
    payload = _payload(text="hello there")

    assert message_line(payload) == "[14:02] vraguraman: hello there"


def test_message_line_falls_back_to_member_name_without_handle() -> None:
    payload = _payload(sender_handle=None, text="hi")

    assert message_line(payload) == "[14:02] Vimal Raguraman: hi"


def test_message_line_replaces_single_mention_token() -> None:
    payload = _payload(
        text="<@member-2> summarize today's numbers",
        mentions=[
            {"member_id": "member-2", "handle": "analyst", "member_name": "Analyst Bot"}
        ],
    )

    assert (
        message_line(payload)
        == "[14:02] vraguraman: @analyst summarize today's numbers"
    )


def test_message_line_replaces_multiple_mention_tokens() -> None:
    payload = _payload(
        text="<@member-2> and <@member-3>, please review",
        mentions=[
            {"member_id": "member-2", "handle": "analyst", "member_name": "Analyst"},
            {"member_id": "member-3", "handle": "risk-bot", "member_name": "Risk Bot"},
        ],
    )

    assert (
        message_line(payload)
        == "[14:02] vraguraman: @analyst and @risk-bot, please review"
    )


def test_message_line_leaves_unknown_token_literal() -> None:
    payload = _payload(text="hey <@ghost-member-id> are you there?", mentions=[])

    assert (
        message_line(payload)
        == "[14:02] vraguraman: hey <@ghost-member-id> are you there?"
    )


def test_message_line_mixed_known_and_unknown_tokens() -> None:
    payload = _payload(
        text="<@member-2> ping <@unknown-id> too",
        mentions=[
            {"member_id": "member-2", "handle": "analyst", "member_name": "Analyst"}
        ],
    )

    assert (
        message_line(payload) == "[14:02] vraguraman: @analyst ping <@unknown-id> too"
    )


def test_message_line_parses_hh_mm_without_fractional_seconds() -> None:
    payload = _payload(timestamp="2026-08-03T09:05:00", text="morning")

    assert message_line(payload) == "[09:05] vraguraman: morning"


def test_message_line_no_mentions_array_present() -> None:
    """Defensive: an empty/absent `mentions` array never crashes -- it just
    means no tokens resolve, matching `build_message_payload`'s "always
    present, empty when nothing resolves" contract, but tolerated here too
    in case a caller hands a minimal/partial payload."""
    payload = _payload(text="<@member-2> hi")
    del payload["mentions"]

    assert message_line(payload) == "[14:02] vraguraman: <@member-2> hi"


# --------------------------------------------------------------------------
# bell_line
# --------------------------------------------------------------------------


def _mention_event(
    *,
    sender_handle: str | None = "risk-bot",
    sender_name: str = "Risk Bot",
    channel_name: str = "reports",
) -> dict:
    return {
        "event": "mention",
        "mention_id": "mn1",
        "created_at": "2026-08-03T14:05:00",
        "mentioned_member_id": "member-1",
        "message": _payload(
            sender_id="sender-2",
            sender_name=sender_name,
            sender_handle=sender_handle,
            channel_name=channel_name,
            text="<@member-1> exposure above threshold",
        ),
    }


def test_bell_line_basic_format() -> None:
    event = _mention_event()

    assert bell_line(event) == "🔔 you were mentioned in #reports by @risk-bot"


def test_bell_line_falls_back_to_member_name_without_handle() -> None:
    event = _mention_event(sender_handle=None, sender_name="Risk Bot")

    assert bell_line(event) == "🔔 you were mentioned in #reports by @Risk Bot"


def test_bell_line_uses_the_mentioning_channel_name() -> None:
    event = _mention_event(channel_name="general")

    assert bell_line(event) == "🔔 you were mentioned in #general by @risk-bot"
