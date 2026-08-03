from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.authorization import (
    authorize_channel_read,
    authorize_management_action,
    require_same_workspace,
)
from app.database import get_db
from app.errors import AlreadyAMemberError, ChannelNameTakenError, NotFoundError
from app.models import Channel, ChannelMember, Member
from app.schemas import ChannelCreate, ChannelOut, MemberIdIn, MemberOut
from app.unreads import new_channel_membership

router = APIRouter()


def _get_channel(db: Session, workspace_id: str, channel_id: str) -> Channel:
    channel = (
        db.query(Channel)
        .filter(Channel.channel_id == channel_id, Channel.workspace_id == workspace_id)
        .first()
    )
    if channel is None:
        raise NotFoundError(
            f"Channel '{channel_id}' not found in workspace '{workspace_id}'"
        )
    return channel


@router.post("/workspaces/{workspace_id}/channels", response_model=ChannelOut)
def create_channel(
    workspace_id: str,
    body: ChannelCreate,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Channel:
    authorize_management_action(member)
    require_same_workspace(member, workspace_id)

    duplicate = (
        db.query(Channel)
        .filter(
            Channel.workspace_id == workspace_id,
            Channel.channel_name_key == body.channel_name.lower(),
        )
        .first()
    )
    if duplicate is not None:
        raise ChannelNameTakenError(
            f"A channel named '{body.channel_name}' already exists in this workspace"
        )

    channel = Channel(workspace_id=workspace_id, channel_name=body.channel_name)
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


@router.get("/workspaces/{workspace_id}/channels", response_model=list[ChannelOut])
def list_channels(
    workspace_id: str,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[Channel]:
    require_same_workspace(member, workspace_id)
    return db.query(Channel).filter(Channel.workspace_id == workspace_id).all()


@router.post(
    "/workspaces/{workspace_id}/channels/{channel_id}/members", response_model=MemberOut
)
def add_channel_member(
    workspace_id: str,
    channel_id: str,
    body: MemberIdIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Member:
    authorize_management_action(member)
    require_same_workspace(member, workspace_id)
    _get_channel(db, workspace_id, channel_id)

    target = db.get(Member, body.member_id)
    if target is None:
        raise NotFoundError(f"Member '{body.member_id}' not found")

    if target.workspace_id != workspace_id:
        raise NotFoundError(f"Member '{body.member_id}' not found")

    exists = (
        db.query(ChannelMember)
        .filter(
            ChannelMember.channel_id == channel_id,
            ChannelMember.member_id == body.member_id,
        )
        .first()
    )
    if exists is not None:
        raise AlreadyAMemberError(
            f"Member '{body.member_id}' is already in channel '{channel_id}'"
        )

    db.add(new_channel_membership(db, channel_id, body.member_id))
    db.commit()
    return target


@router.get(
    "/workspaces/{workspace_id}/channels/{channel_id}/members",
    response_model=list[MemberOut],
)
def list_channel_members(
    workspace_id: str,
    channel_id: str,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[Member]:
    require_same_workspace(member, workspace_id)
    _get_channel(db, workspace_id, channel_id)
    authorize_channel_read(db, member, channel_id)
    return (
        db.query(Member)
        .join(ChannelMember, ChannelMember.member_id == Member.member_id)
        .filter(ChannelMember.channel_id == channel_id)
        .all()
    )
