from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounts import create_member_account
from app.auth import get_current_member
from app.authorization import require_same_workspace, require_workspace_admin
from app.database import get_db
from app.errors import ForbiddenMemberTypeError, LastAdminError, NotFoundError
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
    MemberAdminIn,
    MemberOut,
    MemberSelfOut,
    WorkspaceAuthOut,
    WorkspaceOut,
    WorkspaceVisibilityIn,
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


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def update_workspace_visibility(
    workspace_id: str,
    body: WorkspaceVisibilityIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Workspace:
    """Flip a workspace's visibility. Admin-only, wall-gated."""
    require_workspace_admin(member, workspace_id)
    workspace = db.query(Workspace).filter(Workspace.workspace_id == workspace_id).one()
    workspace.visibility = body.visibility
    db.commit()
    db.refresh(workspace)
    return workspace


@router.patch(
    "/workspaces/{workspace_id}/members/{member_id}", response_model=MemberOut
)
def update_member_admin(
    workspace_id: str,
    member_id: str,
    body: MemberAdminIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Member:
    """Promote/demote a workspace member's admin flag. Admin-only, wall-gated.

    Guards: the target must exist in the same workspace, must be human (not
    an agent/bot_app), and demoting the last remaining admin is rejected so
    a workspace can never end up with zero admins.
    """
    require_workspace_admin(member, workspace_id)
    target = db.query(Member).filter(Member.member_id == member_id).first()
    if target is None or target.workspace_id != workspace_id:
        raise NotFoundError(f"Member '{member_id}' not found")
    if target.member_type != "human":
        raise ForbiddenMemberTypeError(
            f"Member '{target.member_id}' has type '{target.member_type}'; "
            "only 'human' members may be granted admin"
        )
    if not body.is_admin and target.is_admin:
        admin_count = (
            db.query(Member)
            .filter(Member.workspace_id == workspace_id, Member.is_admin.is_(True))
            .count()
        )
        if admin_count == 1:
            raise LastAdminError(
                f"Cannot demote member '{target.member_id}': the workspace "
                "must retain at least one admin"
            )
    target.is_admin = body.is_admin
    db.commit()
    db.refresh(target)
    return target
