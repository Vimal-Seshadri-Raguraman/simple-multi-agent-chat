"""Workspace invitations: create/list/revoke (workspace-side) + registration (invitee-side)."""

import logging
import os
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import rate_limit
from app.accounts import build_member_self_out, create_member_account
from app.auth import get_current_account, get_current_member
from app.authorization import require_same_workspace
from app.capabilities import Cap, caps_for, require_cap
from app.database import get_db
from app.errors import (
    AlreadyAMemberError,
    CapabilityDeniedError,
    InvalidInviteError,
    NotFoundError,
    RateLimitedError,
)
from app.models import Account, Member, Workspace, WorkspaceInvite, utcnow
from app.routers.auth import _issue_workspace_token_pair
from app.routers.members import _register_member
from app.schemas import (
    AgentJoinIn,
    AgentJoinOut,
    CodeRegisterIn,
    InviteCreateIn,
    InviteOut,
    RegisterIn,
    WorkspaceAuthOut,
    WorkspaceOut,
)

router = APIRouter()
logger = logging.getLogger(__name__)

INVITE_CODE_TTL_DAYS: int = int(os.getenv("INVITE_CODE_TTL_DAYS", "7"))

_INVALID_INVITE_MESSAGE = "Invite is invalid or expired"

# Per-invite-type mint capability (SMAC-92): create_invite's required cap
# depends on body.invite_type.
_MINT_CAP_BY_TYPE: dict[str, Cap] = {
    "email": Cap.MINT_HUMAN_INVITES,
    "code": Cap.MINT_HUMAN_INVITES,
    "agent_code": Cap.MINT_AGENT_INVITES,
}

# list/revoke accept either mint capability: an agent_admin must be able to
# see/revoke the agent-invite codes they mint (Task 3), same as an admin
# manages human invites.
_ANY_MINT_CAPS = (Cap.MINT_HUMAN_INVITES, Cap.MINT_AGENT_INVITES)


def _require_workspace(db: Session, member: Member, workspace_id: str) -> Workspace:
    """Shared gate for workspace-side invite operations: wall + workspace
    exists. Capability checks happen at each call site -- create_invite's
    cap depends on the invite type; list/revoke accept either mint cap."""
    require_same_workspace(member, workspace_id)
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError(f"Workspace '{workspace_id}' not found")
    return workspace


def _require_any_mint_cap(member: Member) -> None:
    if not caps_for(member) & set(_ANY_MINT_CAPS):
        joined = " or ".join(cap.value for cap in _ANY_MINT_CAPS)
        raise CapabilityDeniedError(f"This action requires {joined}.")


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
    _require_workspace(db, member, workspace_id)
    require_cap(member, _MINT_CAP_BY_TYPE[body.invite_type])

    if body.invite_type == "email":
        if body.email is None:
            # Guaranteed by InviteCreateIn's model validator; an `assert` here
            # would silently vanish under `python -O`, so raise explicitly.
            raise ValueError("email is required for invite_type 'email'")
        email = body.email.lower()
        # Members no longer carry email directly (Identity v2, SMAC-79 Task
        # 2) -- an existing membership for this email means the ACCOUNT
        # with this email (if any) already has a Member row here.
        existing_account = db.query(Account).filter(Account.email_key == email).first()
        if existing_account is not None:
            existing_member = (
                db.query(Member)
                .filter(
                    Member.workspace_id == workspace_id,
                    Member.account_id == existing_account.account_id,
                )
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
        # "code" (human, multi-use) and "agent_code" (SMAC-92, single-use --
        # burnt on redemption by `join_as_agent`) are minted identically:
        # same token shape/TTL/cleanup, differing only in invite_type and
        # which door will accept them later.
        invite = WorkspaceInvite(
            workspace_id=workspace_id,
            invite_type=body.invite_type,
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
    _require_workspace(db, member, workspace_id)
    _require_any_mint_cap(member)
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
    _require_workspace(db, member, workspace_id)
    _require_any_mint_cap(member)
    invite = db.get(WorkspaceInvite, invite_id)
    if invite is None or invite.workspace_id != workspace_id:
        raise InvalidInviteError(_INVALID_INVITE_MESSAGE)
    db.delete(invite)
    db.commit()
    return {"status": "revoked"}


def _register_account(
    db: Session, workspace: Workspace, account: Account, body: RegisterIn
) -> WorkspaceAuthOut:
    """Shared tail of every account-authed registration door: link the
    caller's account into a new per-workspace profile, commit, log in
    with a convenience WORKSPACE token pair."""
    member = create_member_account(
        db,
        workspace,
        account=account,
        first_name=body.first_name,
        last_name=body.last_name,
        display_name=body.display_name,
        company=body.company,
        occupation=body.occupation,
        job_role=body.job_role,
    )
    db.commit()
    db.refresh(member)
    tokens = _issue_workspace_token_pair(db, member)
    return WorkspaceAuthOut(
        member=build_member_self_out(db, member),
        workspace=WorkspaceOut.model_validate(workspace),
        **tokens.model_dump(),
    )


@router.post("/workspaces/join", response_model=WorkspaceAuthOut)
def register_by_code(
    body: CodeRegisterIn,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> WorkspaceAuthOut:
    """Sign up into the workspace a shareable code belongs to (code = registration key).

    Account-authed (spec §3, SMAC-79 Task 2): the caller already has an
    account, identified via the account token, not a body email/password.
    """
    invite = db.query(WorkspaceInvite).filter(WorkspaceInvite.code == body.code).first()
    if invite is None or _delete_if_expired(db, invite):
        raise InvalidInviteError(_INVALID_INVITE_MESSAGE)
    workspace = db.get(Workspace, invite.workspace_id)
    if workspace is None:
        raise InvalidInviteError(_INVALID_INVITE_MESSAGE)
    # If the caller's ACCOUNT email also holds a pending reserved seat
    # here, consume it -- otherwise the seat becomes permanently unusable
    # (the account already exists) and its existence would leak via
    # create_invite's 409. The staged delete rides _register_account's
    # single commit; on a failed registration (e.g. already_a_member)
    # nothing is flushed and the seat stays.
    if account.email is not None:
        seat = (
            db.query(WorkspaceInvite)
            .filter(
                WorkspaceInvite.workspace_id == workspace.workspace_id,
                WorkspaceInvite.invite_type == "email",
                WorkspaceInvite.email == account.email.lower(),
            )
            .first()
        )
        if seat is not None:
            db.delete(seat)
    return _register_account(db, workspace, account, body)


@router.post("/workspaces/{workspace_id}/register", response_model=WorkspaceAuthOut)
def register_into_workspace(
    workspace_id: str,
    body: RegisterIn,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> WorkspaceAuthOut:
    """Sign up into a workspace directly. Account-authed (spec §3, SMAC-79
    Task 2): the caller already has an account, identified via the
    account token, not a body email/password.

    Public workspace: open door. Private: only with a reserved seat (a
    pending email invite matching the caller's ACCOUNT email), consumed
    on success. No seat -> the uniform 404: private workspaces never
    confirm their own existence.
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
                WorkspaceInvite.email == (account.email or "").lower(),
            )
            .first()
        )
        if seat is None:
            raise NotFoundError(f"Workspace '{workspace_id}' not found")
        # Delete the seat *before* creating the profile, not after: the
        # delete is only staged here (autoflush is off), so it rides along
        # with _register_account's single commit as one atomic
        # transaction. If create_member_account raises (e.g.
        # AlreadyAMemberError), nothing has been flushed yet, the
        # request's session is closed without a commit, and the seat
        # survives untouched -- no window where a crash (or a failed
        # registration) could burn a seat without a profile to show for it.
        db.delete(seat)
        return _register_account(db, workspace, account, body)
    return _register_account(db, workspace, account, body)


def _client_key(request: Request) -> str:
    """Best-effort caller identity for `agent_join_limiter`, keyed by
    client IP since `join_as_agent` has no credential/member_id to key
    a budget by. `request.client` is only unset for certain non-HTTP
    transports (never real HTTP traffic); the fallback just shares one
    bucket across those rather than crashing."""
    return request.client.host if request.client is not None else "unknown"


@router.post("/agents/join", response_model=AgentJoinOut, status_code=201)
def join_as_agent(
    body: AgentJoinIn,
    request: Request,
    db: Session = Depends(get_db),
) -> AgentJoinOut:
    """Redeem a single-use agent invite code (SMAC-92) -- UNAUTHENTICATED:
    the caller has no account/credential yet, only the code. Mints a
    brand-new agent account + per-workspace member + API key and returns
    the key here, exactly once -- `Member.api_key_hash` is one-way, so
    there is no other way to retrieve it later.

    Every failure mode -- unknown code, expired, already-redeemed,
    revoked, a human ('email'/'code') invite presented at this door, or
    an orphaned invite whose workspace no longer exists -- raises the
    exact same `InvalidInviteError` (404 `invalid_invite` / "Invite is
    invalid or expired"): a caller can never learn from the response
    which of those happened, same uniform-404 contract every other
    invite door in this file already uses.

    Rate-limited per client IP (`rate_limit.agent_join_limiter`), since
    an unauthenticated door has no member_id to throttle by otherwise --
    the one thing standing between a bogus code and a brute-force loop.
    """
    if not rate_limit.agent_join_limiter.allow(_client_key(request)):
        raise RateLimitedError("Too many attempts -- wait a moment")

    invite = (
        db.query(WorkspaceInvite)
        .filter(
            WorkspaceInvite.code == body.code,
            WorkspaceInvite.invite_type == "agent_code",
        )
        .first()
    )
    if invite is None or _delete_if_expired(db, invite):
        raise InvalidInviteError(_INVALID_INVITE_MESSAGE)

    workspace = db.get(Workspace, invite.workspace_id)
    if workspace is None:
        raise InvalidInviteError(_INVALID_INVITE_MESSAGE)
    # Captured before the claim/commit below so the response never has to
    # touch a possibly-expired ORM attribute post-commit.
    workspace_id = workspace.workspace_id
    workspace_out = WorkspaceOut.model_validate(workspace)

    # Atomic single-use claim: a bulk DELETE keyed by invite_id (not the
    # SELECT above). Two concurrent redemptions of the same code both
    # pass the lookup/expiry/workspace checks above -- but SQLite
    # serializes the two DELETEs that follow (the loser's WHERE matches
    # zero rows once the winner's has committed), so exactly one caller
    # observes `claimed == 1` and only that one goes on to mint an
    # account/key. The other gets the same uniform 404 a bogus code
    # would, never a distinguishable "already used" response.
    claimed = (
        db.query(WorkspaceInvite)
        .filter(WorkspaceInvite.invite_id == invite.invite_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    if claimed == 0:
        raise InvalidInviteError(_INVALID_INVITE_MESSAGE)

    member, raw_key = _register_member(db, body.name, "agent", workspace_id)
    # Never the code or the key -- only the identifiers a server operator
    # is entitled to see after the fact.
    logger.info(
        "agent invite redeemed workspace_id=%s member_id=%s",
        workspace_id,
        member.member_id,
    )
    return AgentJoinOut(
        account_id=member.account_id,
        member_id=member.member_id,
        handle=member.handle,
        api_key=raw_key,
        workspace=workspace_out,
    )
