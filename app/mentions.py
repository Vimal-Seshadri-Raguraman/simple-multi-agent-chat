"""@handle / #channel parsing: canonicalize at post time, resolve at read time.

Messages store canonical text (`<@member_id>` tokens); handles are mutable
display sugar resolved fresh on every read via `resolve_payload_refs`, so a
handle rename is reflected on every message that mentions that member without
rewriting any stored text. `#channel` references are never rewritten -- they
are resolved purely for the payload's `channel_refs` link array.
"""

import re

from sqlalchemy.orm import Session

from app.models import Channel, Member, Mention, Message, Workspace

HANDLE_PATTERN = re.compile(r"(?<![A-Za-z0-9_])@([a-z0-9-]{2,32})")
TOKEN_PATTERN = re.compile(r"<@([0-9a-fA-F-]{36})>")
CHANNEL_PATTERN = re.compile(r"#([a-zA-Z0-9_-]{1,80})")

# Matches the literal `<@` opener that a genuine mention token always starts
# with. Applied to raw inbound text *before* `@handle` rewriting, so a user
# who types a literal `<@member-id>` string can never masquerade as a real
# mention: only tokens the rewriter itself produces (after this pass) can
# ever match TOKEN_PATTERN in stored text. The zero-width space is invisible
# in any renderer but breaks the `<@` adjacency TOKEN_PATTERN requires.
RAW_TOKEN_OPENER_PATTERN = re.compile(r"<@")
_ZERO_WIDTH_SPACE = "\u200b"  # U+200B, invisible in any renderer


def canonicalize(
    db: Session, workspace_id: str, sender_id: str, text: str
) -> tuple[str, list[Member]]:
    """Rewrite `@handle` -> `<@member_id>` for every handle that resolves.

    Returns (canonical_text, mentioned members -- deduped, sender excluded).
    Unresolved handles (no matching member in this workspace) are left
    untouched in the text and never appear in the mentioned list.

    Before any handle rewriting, literal `<@` sequences already present in
    the raw text are neutralized (see `RAW_TOKEN_OPENER_PATTERN`). Without
    this, typing a literal `<@<member_id>>` would pass through untouched and
    be indistinguishable from a real mention token at read time -- the
    payload would show the member as mentioned with no `Mention` row and no
    event ever created. This guarantees the invariant: every `<@uuid>` token
    in stored text was created by this function, never by raw user input.
    """
    text = RAW_TOKEN_OPENER_PATTERN.sub(f"<{_ZERO_WIDTH_SPACE}@", text)

    members_by_handle = {
        m.handle: m
        for m in db.query(Member).filter(Member.workspace_id == workspace_id).all()
    }
    mentioned: dict[str, Member] = {}

    def _replace(match: re.Match[str]) -> str:
        member = members_by_handle.get(match.group(1))
        if member is None:
            return match.group(0)
        if member.member_id != sender_id:
            mentioned[member.member_id] = member
        return f"<@{member.member_id}>"

    canonical_text = HANDLE_PATTERN.sub(_replace, text)
    return canonical_text, list(mentioned.values())


def resolve_payload_refs(
    db: Session, workspace_id: str, canonical_text: str
) -> tuple[list[Member], list[Channel]]:
    """For reading: resolve stored tokens back to live members/channels.

    Members for every `<@member_id>` token that still exists (in this
    workspace); channels for every `#name` that matches a channel in this
    workspace. Both are deduped, preserving first-occurrence order in the
    text -- so a rename or channel rename is always reflected as of read
    time, not post time.
    """
    member_ids: list[str] = []
    for member_id in TOKEN_PATTERN.findall(canonical_text):
        if member_id not in member_ids:
            member_ids.append(member_id)
    members_by_id = {
        m.member_id: m
        for m in db.query(Member)
        .filter(Member.workspace_id == workspace_id, Member.member_id.in_(member_ids))
        .all()
    }
    mentioned_members = [
        members_by_id[member_id]
        for member_id in member_ids
        if member_id in members_by_id
    ]

    channel_names: list[str] = []
    for channel_name in CHANNEL_PATTERN.findall(canonical_text):
        if channel_name not in channel_names:
            channel_names.append(channel_name)
    channels_by_name = {
        c.channel_name: c
        for c in db.query(Channel)
        .filter(
            Channel.workspace_id == workspace_id,
            Channel.channel_name.in_(channel_names),
        )
        .all()
    }
    referenced_channels = [
        channels_by_name[name] for name in channel_names if name in channels_by_name
    ]

    return mentioned_members, referenced_channels


def build_mention_event(db: Session, mention: Mention) -> dict:
    """The wire shape of one inbox entry: the mention plus its source message.

    Loads the message/channel/workspace/sender chain for `mention` and
    reuses `build_message_payload` for the "message" value, so an inbox
    entry always mirrors exactly what the REST/WebSocket message payload
    looks like right now (handle renames included) -- not a snapshot of
    what it looked like when the mention was created.

    Imports `build_message_payload` locally: `app.schemas` imports
    `resolve_payload_refs` from this module, so a module-level import here
    would be circular.
    """
    from app.schemas import build_message_payload

    message = db.get(Message, mention.message_id)
    assert message is not None  # FK-guaranteed by the mentions table
    channel = db.get(Channel, message.channel_id)
    assert channel is not None  # FK-guaranteed by messages.channel_id
    workspace = db.get(Workspace, channel.workspace_id)
    assert workspace is not None  # FK-guaranteed by channels.workspace_id
    sender = db.get(Member, message.sender_member_id)
    assert sender is not None  # FK-guaranteed by messages.sender_member_id

    return {
        "event": "mention",
        "mention_id": mention.mention_id,
        "created_at": mention.created_at.isoformat(),
        "mentioned_member_id": mention.mentioned_member_id,
        "message": build_message_payload(message, workspace, channel, sender, db),
    }
