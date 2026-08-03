"""The SMAC MCP bridge: 8 tools that make an MCP client a workspace member."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from smac_mcp.api import SmacApi


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def build_server(api: SmacApi) -> FastMCP:
    """Build the FastMCP app: 8 tools closed over `api`, the member's identity.

    `api` is injected so tests can build a server around an ASGI-backed
    SmacApi (no real network, but the real server logic underneath) --
    the same tool functions registered here are what a live MCP client
    would call over stdio.
    """
    server = FastMCP("smac")

    @server.tool()
    async def whoami() -> str:
        """Who am I in this workspace? Returns your member name, @handle,
        member_id, and workspace. Check this before your first post."""
        return _dump(await api.me())

    @server.tool()
    async def catch_me_up() -> str:
        """What did I miss? One row per channel you belong to: unread_count,
        first_unread_message_id (start reading there), and mention_count —
        how many times you were @mentioned and haven't acknowledged.
        Start every session here. Reading messages does NOT clear these
        numbers; use mark_read and ack_mention."""
        ws = await api.workspace_id()
        return _dump(await api.request("GET", f"/workspaces/{ws}/unreads"))

    @server.tool()
    async def check_mentions(limit: int = 20) -> str:
        """Your unacknowledged @mentions, oldest first, each carrying the
        full message that triggered it — this is how you know you were
        addressed, even in a channel you can't read. Being @mentioned is
        what "triggers" you; nothing else does. Handle each one, then call
        ack_mention — checking this list again does NOT clear it."""
        return _dump(await api.request("GET", "/mentions", params={"limit": limit}))

    @server.tool()
    async def ack_mention(mention_id: str) -> str:
        """Acknowledge a mention AFTER you've handled it (read the message
        and, if warranted, replied). Idempotent — acking an already-acked
        mention is a harmless no-op. A mention_id that isn't yours is
        reported as not found, same as one that doesn't exist."""
        return _dump(await api.request("POST", f"/mentions/{mention_id}/ack"))

    @server.tool()
    async def list_channels() -> str:
        """Channels in your workspace. You can only read or post in a
        channel you're a member of — if you need one you're not in, ask a
        human to add you; the bridge has no self-service join."""
        ws = await api.workspace_id()
        return _dump(await api.request("GET", f"/workspaces/{ws}/channels"))

    @server.tool()
    async def read_messages(
        channel_id: str, after: str | None = None, limit: int = 15
    ) -> str:
        """Read messages from a channel you belong to, oldest-first.
        `after` is a cursor: pass the message_id you last saw to page
        forward; omit it to start from the channel's beginning. Reading
        NEVER marks anything read — call mark_read separately to advance
        your cursor. A channel you're not a member of is reported as
        not found."""
        params: dict[str, Any] = {"limit": limit}
        if after is not None:
            params["after"] = after
        ws = await api.workspace_id()
        return _dump(
            await api.request(
                "GET",
                f"/workspaces/{ws}/channels/{channel_id}/messages",
                params=params,
            )
        )

    @server.tool()
    async def post_message(channel_id: str, text: str) -> str:
        """Post a message to a channel you belong to. Type `@handle` inside
        the text to trigger that member (they'll see it via check_mentions);
        `#channel-name` links a channel without posting there. The response
        shows the canonicalized message text and a `mentions` array naming
        who was triggered. Subject to the workspace's posting rate limit —
        if you're told you're posting too fast, wait a moment before
        retrying. Posting to a channel you're not a member of is reported
        as not found."""
        ws = await api.workspace_id()
        return _dump(
            await api.request(
                "POST",
                f"/workspaces/{ws}/channels/{channel_id}/messages",
                json_body={"message_text": text},
            )
        )

    @server.tool()
    async def mark_read(
        channel_id: str, last_read_message_id: str | None = None
    ) -> str:
        """Advance your read cursor for a channel. Omit
        last_read_message_id to mark yourself caught up to the channel's
        latest message; pass a specific message_id for a partial catch-up.
        This is the only thing that clears catch_me_up's unread_count for
        the channel — reading messages alone does not. A channel you're
        not a member of is reported as not found."""
        ws = await api.workspace_id()
        return _dump(
            await api.request(
                "POST",
                f"/workspaces/{ws}/channels/{channel_id}/read",
                json_body={"last_read_message_id": last_read_message_id},
            )
        )

    return server
