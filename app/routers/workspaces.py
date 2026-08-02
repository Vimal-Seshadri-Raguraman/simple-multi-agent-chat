from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounts import create_member_account
from app.auth import get_current_member
from app.authorization import require_same_workspace
from app.database import get_db
from app.errors import NotFoundError
from app.models import (
    Channel,
    ChannelMember,
    Member,
    Workspace,
    WorkspaceRecord,
    new_id,
)
from app.routers.auth import _issue_token_pair
from app.schemas import (
    FoundWorkspaceIn,
    MemberOut,
    MemberSelfOut,
    WorkspaceAuthOut,
    WorkspaceOut,
)

router = APIRouter()


@router.post("/workspaces", response_model=WorkspaceAuthOut)
def found_workspace(
    body: FoundWorkspaceIn, db: Session = Depends(get_db)
) -> WorkspaceAuthOut:
    """Found a workspace: workspace + 'general' + admin account + audit record, atomically."""
    workspace = Workspace(
        workspace_id=new_id(),
        workspace_name=body.workspace_name,
        visibility=body.visibility,
    )
    db.add(workspace)
    db.flush()
    general = Channel(
        channel_id=new_id(),
        workspace_id=workspace.workspace_id,
        channel_name="general",
    )
    db.add(general)
    db.flush()
    workspace.default_channel_id = general.channel_id
    founder = create_member_account(
        db,
        workspace,
        email=body.email,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
        display_name=body.display_name,
        company=body.company,
        occupation=body.occupation,
        job_role=body.job_role,
        is_admin=True,
    )
    db.add(
        WorkspaceRecord(
            workspace_id=workspace.workspace_id,
            workspace_name=workspace.workspace_name,
            created_by=founder.member_id,
        )
    )
    db.commit()
    db.refresh(founder)
    tokens = _issue_token_pair(db, founder)
    return WorkspaceAuthOut(
        member=MemberSelfOut.model_validate(founder),
        workspace=WorkspaceOut.model_validate(workspace),
        **tokens.model_dump(),
    )


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOut])
def list_workspace_members(
    workspace_id: str,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[Member]:
    """Everyone in the caller's own workspace (the wall blocks all others)."""
    require_same_workspace(member, workspace_id)
    return db.query(Member).filter(Member.workspace_id == workspace_id).all()
