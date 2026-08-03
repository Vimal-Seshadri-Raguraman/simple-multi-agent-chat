"""Read-cursor math: the server-side memory of what each member has seen.

Unread semantics: a message is unread for a member when its per-channel
``seq`` exceeds the member's ``last_read_seq`` on the membership row.
Fetching messages never moves the cursor; marking read is explicit.
"""

from sqlalchemy.orm import Session

from app.models import Channel, ChannelMember, Mention, Message


def latest_seq(db: Session, channel_id: str) -> int:
    """The channel's current max seq, or 0 for an empty channel."""
    row = (
        db.query(Message.seq)
        .filter(Message.channel_id == channel_id)
        .order_by(Message.seq.desc())
        .first()
    )
    return row[0] if row else 0


def new_channel_membership(
    db: Session, channel_id: str, member_id: str
) -> ChannelMember:
    """A membership row that starts caught up (never badges old history)."""
    return ChannelMember(
        channel_id=channel_id,
        member_id=member_id,
        last_read_seq=latest_seq(db, channel_id),
    )


def build_unreads_row(
    db: Session, member_id: str, channel: Channel, last_read_seq: int
) -> dict:
    """One unreads entry: message count, first-unread anchor, mention badge."""
    unread_query = db.query(Message).filter(
        Message.channel_id == channel.channel_id, Message.seq > last_read_seq
    )
    unread_count = unread_query.count()
    first_unread = unread_query.order_by(Message.seq.asc()).first()
    mention_count = (
        db.query(Mention)
        .join(Message, Mention.message_id == Message.message_id)
        .filter(
            Mention.mentioned_member_id == member_id,
            Mention.acknowledged_at.is_(None),
            Message.channel_id == channel.channel_id,
        )
        .count()
    )
    return {
        "channel_id": channel.channel_id,
        "channel_name": channel.channel_name,
        "unread_count": unread_count,
        "first_unread_message_id": (
            first_unread.message_id if first_unread is not None else None
        ),
        "mention_count": mention_count,
    }
