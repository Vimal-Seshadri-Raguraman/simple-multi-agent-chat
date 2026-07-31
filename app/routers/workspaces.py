from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.authorization import authorize_management_action
from app.database import get_db
from app.errors import AlreadyAMemberError, NotFoundError
from app.models import Member, Workspace, WorkspaceMember
from app.schemas import MemberIdIn, MemberOut, WorkspaceCreate, WorkspaceOut

router = APIRouter()


@router.post("/workspaces", response_model=WorkspaceOut)
def create_workspace(
    body: WorkspaceCreate,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Workspace:
    authorize_management_action(member)
    workspace = Workspace(workspace_name=body.workspace_name)
    db.add(workspace)
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

    exists = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.member_id == body.member_id,
        )
        .first()
    )
    if exists is not None:
        raise AlreadyAMemberError(
            f"Member '{body.member_id}' is already in workspace '{workspace_id}'"
        )

    db.add(WorkspaceMember(workspace_id=workspace_id, member_id=body.member_id))
    db.commit()
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
    return (
        db.query(Member)
        .join(WorkspaceMember, WorkspaceMember.member_id == Member.member_id)
        .filter(WorkspaceMember.workspace_id == workspace_id)
        .all()
    )
