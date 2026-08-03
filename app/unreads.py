"""Read-cursor math: the server-side memory of what each member has seen.

Unread semantics: a message is unread for a member when its per-channel
``seq`` exceeds the member's ``last_read_seq`` on the membership row.
Fetching messages never moves the cursor; marking read is explicit.
"""

from sqlalchemy.orm import Session

from app.models import ChannelMember, Message


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
