from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.authorization import authorize_management_action, authorize_workspace_read
from app.database import get_db
from app.errors import NotFoundError
from app.membership import join_workspace
from app.models import (
    Channel,
    ChannelMember,
    Member,
    Workspace,
    WorkspaceMember,
    new_id,
)
from app.schemas import MemberIdIn, MemberOut, WorkspaceCreate, WorkspaceOut

router = APIRouter()


@router.post("/workspaces", response_model=WorkspaceOut)
def create_workspace(
    body: WorkspaceCreate,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Workspace:
    """Create a workspace with a default 'general' channel; creator joins both."""
    authorize_management_action(member)
    # `default=new_id` on the mapped columns only fires at flush time, so the
    # ids aren't populated on these in-memory objects yet; generate them
    # explicitly up front so `general.channel_id` can be wired onto
    # `workspace.default_channel_id` before the single commit below.
    workspace = Workspace(workspace_id=new_id(), workspace_name=body.workspace_name)
    general = Channel(
        channel_id=new_id(), workspace_id=workspace.workspace_id, channel_name="general"
    )
    workspace.default_channel_id = general.channel_id
    db.add_all(
        [
            workspace,
            general,
            WorkspaceMember(
                workspace_id=workspace.workspace_id, member_id=member.member_id
            ),
            ChannelMember(channel_id=general.channel_id, member_id=member.member_id),
        ]
    )
    db.commit()
    db.refresh(workspace)
    return workspace


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[Workspace]:
    return db.query(Workspace).all()


@router.post("/workspaces/{workspace_id}/members", response_model=MemberOut)
def add_workspace_member(
    workspace_id: str,
    body: MemberIdIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Member:
    authorize_management_action(member)

    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError(f"Workspace '{workspace_id}' not found")

    target = db.get(Member, body.member_id)
    if target is None:
        raise NotFoundError(f"Member '{body.member_id}' not found")

    join_workspace(db, workspace, body.member_id)
    return target


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOut])
def list_workspace_members(
    workspace_id: str,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[Member]:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError(f"Workspace '{workspace_id}' not found")
    authorize_workspace_read(db, member, workspace_id)
    return (
        db.query(Member)
        .join(WorkspaceMember, WorkspaceMember.member_id == Member.member_id)
        .filter(WorkspaceMember.workspace_id == workspace_id)
        .all()
    )
