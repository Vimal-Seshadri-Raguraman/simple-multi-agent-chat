"""Account creation — the single way any human account is born.

Identity v2 (SMAC-79 Task 2, the cutover): every workspace birth/join door
is now account-authed (spec §3) — the caller already holds an account (an
account-tier token), so these doors only ever LINK an existing `Account`
to a new per-workspace `Member` profile; they never create or hash a
password. `POST /accounts` (below) is the only door where a human account
is actually created.

- `create_account` — POST /accounts (signup): raises EmailTakenError on
  an existing account, because signup IS the door where that leak is
  accepted (spec §7).
- `create_agent_account` — a brand-new identity-only agent/bot account
  (app/routers/members.py): no email/password.
- `create_member_account` — the shared tail of every workspace birth/join
  door (found, register, code-join): creates the per-workspace `Member`
  profile linking to an already-authenticated `account`. Raises
  AlreadyAMemberError when that account already has a profile in this
  workspace (`uq_members_workspace_account`).
"""

from sqlalchemy.orm import Session

from app.errors import AlreadyAMemberError, EmailTakenError
from app.handles import generate_unique_handle
from app.models import Account, Member, Workspace
from app.schemas import MemberSelfOut
from app.security import hash_password
from app.unreads import new_channel_membership


def create_account(db: Session, email: str, password: str) -> Account:
    """Create a new global human Account (POST /accounts). Flushes; caller commits.

    Raises EmailTakenError when an account with this email (case-
    insensitively, via `email_key`) already exists — signup is the one
    door where that's a genuine conflict (spec §7).
    """
    normalized = email.lower()
    exists = db.query(Account).filter(Account.email_key == normalized).first()
    if exists is not None:
        raise EmailTakenError(f"An account with email '{normalized}' already exists")
    account = Account(
        account_type="human", email=email, password_hash=hash_password(password)
    )
    db.add(account)
    db.flush()
    return account


def create_agent_account(db: Session, account_type: str) -> Account:
    """Create a new identity-only Account for an agent/bot_app member
    (Decision 2: agent accounts are identities; API keys stay
    per-workspace on `Member`, unaffected). Flushes; caller commits.
    """
    account = Account(account_type=account_type)
    db.add(account)
    db.flush()
    return account


def create_member_account(
    db: Session,
    workspace: Workspace,
    *,
    account: Account,
    first_name: str,
    last_name: str,
    display_name: str | None = None,
    company: str | None = None,
    occupation: str | None = None,
    job_role: str | None = None,
    is_admin: bool = False,
) -> Member:
    """Link `account` into `workspace` as a new per-workspace profile.
    Flushes; caller commits.

    Raises AlreadyAMemberError when `account` already has a profile in
    this workspace (`uq_members_workspace_account`) — the same account in
    a DIFFERENT workspace is a separate Member profile, that's the model
    (spec Decision 1).
    """
    exists = (
        db.query(Member)
        .filter(
            Member.workspace_id == workspace.workspace_id,
            Member.account_id == account.account_id,
        )
        .first()
    )
    if exists is not None:
        raise AlreadyAMemberError(
            f"Account '{account.account_id}' is already a member of this workspace"
        )
    member = Member(
        workspace_id=workspace.workspace_id,
        member_name=display_name or f"{first_name} {last_name}",
        member_type="human",
        handle=generate_unique_handle(
            db, workspace.workspace_id, f"{first_name[0]}{last_name}"
        ),
        account_id=account.account_id,
        first_name=first_name,
        last_name=last_name,
        company=company,
        occupation=occupation,
        job_role=job_role,
        is_admin=is_admin,
    )
    db.add(member)
    db.flush()
    if workspace.default_channel_id is not None:
        db.add(
            new_channel_membership(db, workspace.default_channel_id, member.member_id)
        )
    return member


def build_member_self_out(db: Session, member: Member) -> MemberSelfOut:
    """Assemble `MemberSelfOut` for `member`, looking up its workspace's
    `visibility` (not a `Member` attribute -- see the schema's own
    docstring for why that lookup lives here rather than on the ORM
    model). The one place every `/member*` route builds this response,
    so `is_admin`/`workspace_visibility` can never drift out of sync
    across the routes that return this shape (`GET /member`, `GET
    /members/me`, `PATCH /members/me`, `POST /workspaces` and the
    register-into-workspace routes).
    """
    workspace = (
        db.query(Workspace).filter(Workspace.workspace_id == member.workspace_id).one()
    )
    return MemberSelfOut(
        member_id=member.member_id,
        member_name=member.member_name,
        member_type=member.member_type,
        handle=member.handle,
        workspace_id=member.workspace_id,
        account_id=member.account_id,
        created_at=member.created_at,
        first_name=member.first_name,
        last_name=member.last_name,
        company=member.company,
        occupation=member.occupation,
        job_role=member.job_role,
        is_admin=member.is_admin,
        workspace_visibility=workspace.visibility,
    )
