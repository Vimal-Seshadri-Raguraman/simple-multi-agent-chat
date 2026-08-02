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
    # workspaces.default_channel_id and channels.workspace_id form an FK
    # cycle. Under SQLite `PRAGMA foreign_keys=ON` (the production engine
    # config in app/database.py), a single `add_all([...])` + commit lets
    # SQLAlchemy's topological insert ordering pick either table first --
    # when it picks `channels` before its parent `workspaces` row exists,
    # that's an IntegrityError on every workspace creation. Sequential
    # flushes make the insert order explicit while keeping this atomic: a
    # flush is not a commit, so a failure at any point below rolls back the
    # whole transaction (nothing is visible to other sessions until the
    # final `db.commit()`).
    workspace = Workspace(workspace_id=new_id(), workspace_name=body.workspace_name)
    db.add(workspace)
    db.flush()  # workspace row now exists; default_channel_id stays NULL for now

    general = Channel(
        channel_id=new_id(), workspace_id=workspace.workspace_id, channel_name="general"
    )
    db.add(general)
    db.flush()  # channel row now exists, satisfying the FK we're about to set

    workspace.default_channel_id = general.channel_id
    db.add(
        WorkspaceMember(workspace_id=workspace.workspace_id, member_id=member.member_id)
    )
    db.add(ChannelMember(channel_id=general.channel_id, member_id=member.member_id))
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
