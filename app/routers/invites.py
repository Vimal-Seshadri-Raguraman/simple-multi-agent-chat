"""Workspace invitations: create/list/revoke (workspace-side) + registration (invitee-side)."""

import os
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounts import (
    build_member_self_out,
    create_member_account,
    get_or_create_account_for_email,
)
from app.auth import get_current_member
from app.authorization import authorize_management_action, require_same_workspace
from app.database import get_db
from app.errors import AlreadyAMemberError, InvalidInviteError, NotFoundError
from app.models import Member, Workspace, WorkspaceInvite, utcnow
from app.routers.auth import _issue_token_pair
from app.schemas import (
    CodeRegisterIn,
    InviteCreateIn,
    InviteOut,
    RegisterIn,
    WorkspaceAuthOut,
    WorkspaceOut,
)

router = APIRouter()

INVITE_CODE_TTL_DAYS: int = int(os.getenv("INVITE_CODE_TTL_DAYS", "7"))

_INVALID_INVITE_MESSAGE = "Invite is invalid or expired"


def _require_human_workspace_member(
    db: Session, member: Member, workspace_id: str
) -> Workspace:
    """Shared gate for workspace-side invite operations: human + member + workspace exists."""
    authorize_management_action(member)
    require_same_workspace(member, workspace_id)
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError(f"Workspace '{workspace_id}' not found")
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
        if body.email is None:
            # Guaranteed by InviteCreateIn's model validator; an `assert` here
            # would silently vanish under `python -O`, so raise explicitly.
            raise ValueError("email is required for invite_type 'email'")
        email = body.email.lower()
        existing_member = (
            db.query(Member)
            .filter(Member.workspace_id == workspace_id, Member.email == email)
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


def _register_account(
    db: Session, workspace: Workspace, body: RegisterIn
) -> WorkspaceAuthOut:
    """Shared tail of both registration paths: create account, commit, log in."""
    account = get_or_create_account_for_email(db, body.email, body.password)
    member = create_member_account(
        db,
        workspace,
        email=body.email,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
        account=account,
        display_name=body.display_name,
        company=body.company,
        occupation=body.occupation,
        job_role=body.job_role,
    )
    db.commit()
    db.refresh(member)
    tokens = _issue_token_pair(db, member)
    return WorkspaceAuthOut(
        member=build_member_self_out(db, member),
        workspace=WorkspaceOut.model_validate(workspace),
        **tokens.model_dump(),
    )


@router.post("/workspaces/join", response_model=WorkspaceAuthOut)
def register_by_code(
    body: CodeRegisterIn, db: Session = Depends(get_db)
) -> WorkspaceAuthOut:
    """Sign up into the workspace a shareable code belongs to (code = registration key)."""
    invite = db.query(WorkspaceInvite).filter(WorkspaceInvite.code == body.code).first()
    if invite is None or _delete_if_expired(db, invite):
        raise InvalidInviteError(_INVALID_INVITE_MESSAGE)
    workspace = db.get(Workspace, invite.workspace_id)
    if workspace is None:
        raise InvalidInviteError(_INVALID_INVITE_MESSAGE)
    # If this email also holds a pending reserved seat here, consume it —
    # otherwise the seat becomes permanently unusable (the email now has an
    # account) and its existence would leak via create_invite's 409. The
    # staged delete rides _register_account's single commit; on a failed
    # registration (e.g. email_taken) nothing is flushed and the seat stays.
    seat = (
        db.query(WorkspaceInvite)
        .filter(
            WorkspaceInvite.workspace_id == workspace.workspace_id,
            WorkspaceInvite.invite_type == "email",
            WorkspaceInvite.email == body.email.lower(),
        )
        .first()
    )
    if seat is not None:
        db.delete(seat)
    return _register_account(db, workspace, body)


@router.post("/workspaces/{workspace_id}/register", response_model=WorkspaceAuthOut)
def register_into_workspace(
    workspace_id: str, body: RegisterIn, db: Session = Depends(get_db)
) -> WorkspaceAuthOut:
    """Sign up into a workspace directly.

    Public workspace: open door. Private: only with a reserved seat (a
    pending email invite matching the registration email), consumed on
    success. No seat -> the uniform 404: private workspaces never confirm
    their own existence.
    """
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError(f"Workspace '{workspace_id}' not found")
    if workspace.visibility != "public":
        seat = (
            db.query(WorkspaceInvite)
            .filter(
                WorkspaceInvite.workspace_id == workspace_id,
                WorkspaceInvite.invite_type == "email",
                WorkspaceInvite.email == body.email.lower(),
            )
            .first()
        )
        if seat is None:
            raise NotFoundError(f"Workspace '{workspace_id}' not found")
        # Delete the seat *before* creating the account, not after: the delete
        # is only staged here (autoflush is off), so it rides along with
        # _register_account's single commit as one atomic transaction. If
        # create_member_account raises (e.g. EmailTakenError), nothing has
        # been flushed yet, the request's session is closed without a commit,
        # and the seat survives untouched -- no window where a crash (or a
        # failed registration) could burn a seat without an account to show
        # for it.
        db.delete(seat)
        return _register_account(db, workspace, body)
    return _register_account(db, workspace, body)
