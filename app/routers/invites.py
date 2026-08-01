"""Workspace invitations: create/list/revoke (this task) + invitee flows (later tasks)."""

import os
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.authorization import authorize_management_action, authorize_workspace_read
from app.database import get_db
from app.errors import AlreadyAMemberError, InvalidInviteError, NotFoundError
from app.models import Member, Workspace, WorkspaceInvite, WorkspaceMember, utcnow
from app.schemas import InviteCreateIn, InviteOut

router = APIRouter()

INVITE_CODE_TTL_DAYS: int = int(os.getenv("INVITE_CODE_TTL_DAYS", "7"))

_INVALID_INVITE_MESSAGE = "Invite is invalid or expired"


def _require_human_workspace_member(
    db: Session, member: Member, workspace_id: str
) -> Workspace:
    """Shared gate for workspace-side invite operations: human + member + workspace exists."""
    authorize_management_action(member)
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError(f"Workspace '{workspace_id}' not found")
    authorize_workspace_read(db, member, workspace_id)
    return workspace


def _delete_if_expired(db: Session, invite: WorkspaceInvite) -> bool:
    """Delete an expired code invite on sight; returns True if it was expired."""
    if invite.expires_at is not None and invite.expires_at < utcnow():
        db.delete(invite)
        db.commit()
        return True
    return False


@router.post("/workspaces/{workspace_id}/invites", response_model=InviteOut)
def create_invite(
    workspace_id: str,
    body: InviteCreateIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> WorkspaceInvite:
    """Create an email-targeted invite or a shareable multi-use code."""
    _require_human_workspace_member(db, member, workspace_id)

    if body.invite_type == "email":
        assert body.email is not None  # guaranteed by schema validator
        email = body.email.lower()
        existing_member = (
            db.query(WorkspaceMember)
            .join(Member, Member.member_id == WorkspaceMember.member_id)
            .filter(WorkspaceMember.workspace_id == workspace_id, Member.email == email)
            .first()
        )
        if existing_member is not None:
            raise AlreadyAMemberError(
                f"'{email}' already belongs to a member of this workspace"
            )
        pending = (
            db.query(WorkspaceInvite)
            .filter(
                WorkspaceInvite.workspace_id == workspace_id,
                WorkspaceInvite.email == email,
            )
            .first()
        )
        if pending is not None:
            raise AlreadyAMemberError(
                f"A pending invite for '{email}' already exists in this workspace"
            )
        invite = WorkspaceInvite(
            workspace_id=workspace_id,
            invite_type="email",
            email=email,
            created_by=member.member_id,
        )
    else:
        invite = WorkspaceInvite(
            workspace_id=workspace_id,
            invite_type="code",
            code=secrets.token_urlsafe(9),
            created_by=member.member_id,
            expires_at=utcnow() + timedelta(days=INVITE_CODE_TTL_DAYS),
        )

    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


@router.get("/workspaces/{workspace_id}/invites", response_model=list[InviteOut])
def list_invites(
    workspace_id: str,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[WorkspaceInvite]:
    """List pending invites (codes shown in full, so they can be re-shared)."""
    _require_human_workspace_member(db, member, workspace_id)
    invites = (
        db.query(WorkspaceInvite)
        .filter(WorkspaceInvite.workspace_id == workspace_id)
        .all()
    )
    return [i for i in invites if not _delete_if_expired(db, i)]


@router.delete("/workspaces/{workspace_id}/invites/{invite_id}")
def revoke_invite(
    workspace_id: str,
    invite_id: str,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Revoke any pending invite (either type)."""
    _require_human_workspace_member(db, member, workspace_id)
    invite = db.get(WorkspaceInvite, invite_id)
    if invite is None or invite.workspace_id != workspace_id:
        raise InvalidInviteError(_INVALID_INVITE_MESSAGE)
    db.delete(invite)
    db.commit()
    return {"status": "revoked"}
