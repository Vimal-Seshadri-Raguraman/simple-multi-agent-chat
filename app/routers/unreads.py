"""The catch-up surface: what did this member miss, channel by channel."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.authorization import authorize_channel_read, require_same_workspace
from app.database import get_db
from app.errors import NotFoundError
from app.models import Channel, ChannelMember, Member, Message
from app.schemas import MarkReadIn, UnreadsOut, UnreadsRowOut
from app.unreads import build_unreads_row, latest_seq

router = APIRouter()


@router.get("/workspaces/{workspace_id}/unreads", response_model=UnreadsOut)
def get_unreads(
    workspace_id: str,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> dict:
    """Per-channel unread counts, first-unread anchor, and mention badge."""
    require_same_workspace(member, workspace_id)
    memberships = (
        db.query(ChannelMember, Channel)
        .join(Channel, ChannelMember.channel_id == Channel.channel_id)
        .filter(
            ChannelMember.member_id == member.member_id,
            Channel.workspace_id == workspace_id,
        )
        .order_by(Channel.channel_name.asc())
        .all()
    )
    return {
        "unreads": [
            build_unreads_row(db, member.member_id, channel, cm.last_read_seq)
            for cm, channel in memberships
        ]
    }


@router.post(
    "/workspaces/{workspace_id}/channels/{channel_id}/read",
    response_model=UnreadsRowOut,
)
def mark_read(
    workspace_id: str,
    channel_id: str,
    body: MarkReadIn | None = None,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> dict:
    """Advance the caller's read cursor for a channel.

    An omitted body, an empty body (`{}`), and an explicit
    `{"last_read_message_id": null}` are all equivalent: caught up to the
    channel's latest message. An explicit anchor moves the cursor to that
    message's `seq` -- backwards included, since the client owns "seen".
    A foreign-channel or nonexistent anchor is a uniform 404, matching the
    same anti-enumeration posture as the rest of the API.
    """
    require_same_workspace(member, workspace_id)
    channel = (
        db.query(Channel)
        .filter(Channel.channel_id == channel_id, Channel.workspace_id == workspace_id)
        .first()
    )
    if channel is None:
        raise NotFoundError(
            f"Channel '{channel_id}' not found in workspace '{workspace_id}'"
        )
    authorize_channel_read(db, member, channel_id)

    anchor_id = body.last_read_message_id if body is not None else None
    if anchor_id is None:
        new_cursor = latest_seq(db, channel_id)
    else:
        anchor = (
            db.query(Message)
            .filter(Message.message_id == anchor_id, Message.channel_id == channel_id)
            .first()
        )
        if anchor is None:
            raise NotFoundError(f"Message '{anchor_id}' not found")
        new_cursor = anchor.seq

    membership = db.get(ChannelMember, (channel_id, member.member_id))
    assert membership is not None  # guaranteed by authorize_channel_read
    membership.last_read_seq = new_cursor
    db.commit()
    return build_unreads_row(db, member.member_id, channel, new_cursor)
