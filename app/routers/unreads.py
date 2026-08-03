"""The catch-up surface: what did this member miss, channel by channel."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.authorization import require_same_workspace
from app.database import get_db
from app.models import Channel, ChannelMember, Member
from app.schemas import UnreadsOut
from app.unreads import build_unreads_row

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
