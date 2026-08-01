from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.authorization import (
    authorize_channel_read,
    authorize_management_action,
    authorize_workspace_read,
)
from app.database import get_db
from app.errors import AlreadyAMemberError, NotAWorkspaceMemberError, NotFoundError
from app.models import Channel, ChannelMember, Member, Workspace, WorkspaceMember
from app.schemas import ChannelCreate, ChannelOut, MemberIdIn, MemberOut

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
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError(f"Workspace '{workspace_id}' not found")

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
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError(f"Workspace '{workspace_id}' not found")
    authorize_workspace_read(db, member, workspace_id)
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
    _get_channel(db, workspace_id, channel_id)

    target = db.get(Member, body.member_id)
    if target is None:
        raise NotFoundError(f"Member '{body.member_id}' not found")

    in_workspace = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.member_id == body.member_id,
        )
        .first()
    )
    if in_workspace is None:
        raise NotAWorkspaceMemberError(
            f"Member '{body.member_id}' must belong to workspace '{workspace_id}' "
            "before joining one of its channels"
        )

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

    db.add(ChannelMember(channel_id=channel_id, member_id=body.member_id))
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
    _get_channel(db, workspace_id, channel_id)
    authorize_channel_read(db, member, channel_id)
    return (
        db.query(Member)
        .join(ChannelMember, ChannelMember.member_id == Member.member_id)
        .filter(ChannelMember.channel_id == channel_id)
        .all()
    )
