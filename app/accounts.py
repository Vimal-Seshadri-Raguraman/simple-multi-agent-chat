"""Account creation — the single way any human account is born.

Under the Slack model an account lives inside exactly one workspace, so
creation always happens against a workspace: founding it (is_admin=True),
registering into a public one, redeeming an invite/code into a private one.
All three endpoints delegate here so uniqueness, hashing, naming, and
default-channel landing can never drift apart.

Identity v2 (SMAC-79 Task 1) layers a global `Account` underneath every
`Member` profile (dual-write: both the legacy per-workspace columns AND
the new `accounts` table are written, so old login keeps working
unmodified while new account-tier auth comes online). Three account-side
entry points live here too:

- `create_account` — POST /accounts (signup): raises EmailTakenError on
  an existing account, because signup IS the door where that leak is
  accepted (spec §7).
- `get_or_create_account_for_email` — the legacy workspace-birth doors
  (founding, registering, code-join): the SAME real-world email can
  already have founded/registered into other workspaces (per-workspace
  email uniqueness, unchanged), so an existing account is LINKED, never
  rejected and never re-hashed/overwritten — `create_member_account`'s own
  per-workspace duplicate check is what raises EmailTakenError for THIS
  workspace.
- `create_agent_account` — agent/bot dual-write (app/routers/members.py):
  identity-only, no email/password.
"""

from sqlalchemy.orm import Session

from app.errors import EmailTakenError
from app.handles import generate_unique_handle
from app.models import Account, Member, Workspace
from app.schemas import MemberSelfOut
from app.security import hash_password
from app.unreads import new_channel_membership


def create_account(db: Session, email: str, password: str) -> Account:
    """Create a new global human Account (POST /accounts). Flushes; caller commits.

    Raises EmailTakenError when an account with this email (case-
    insensitively, via `email_key`) already exists — this is the one door
    where that's a genuine conflict rather than a silent link (see
    `get_or_create_account_for_email` for the legacy doors' behavior).
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


def get_or_create_account_for_email(db: Session, email: str, password: str) -> Account:
    """Get-or-create the global Account for a human's email, for the
    LEGACY workspace-birth flows (founding/registering/code-join) that
    now dual-write into `accounts` (SMAC-79 Task 1).

    If an account with this email (case-insensitively) already exists, it
    is LINKED as-is — its password is never overwritten, since the caller
    might be registering into a second workspace with a password that
    doesn't match the first (allowed today, see tests/test_discover.py).
    Only a brand-new email mints a brand-new account. Flushes; caller
    commits.
    """
    normalized = email.lower()
    existing = db.query(Account).filter(Account.email_key == normalized).first()
    if existing is not None:
        return existing
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
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    account: Account,
    display_name: str | None = None,
    company: str | None = None,
    occupation: str | None = None,
    job_role: str | None = None,
    is_admin: bool = False,
) -> Member:
    """Create a human account inside a workspace. Flushes; caller commits.

    Raises EmailTakenError when the (lowercased) email already has an
    account in this workspace. The same email in another workspace is a
    different Member profile — that's the model — but, since SMAC-79 Task
    1, the SAME global `Account` (passed in by the caller, typically via
    `get_or_create_account_for_email`): `member.account_id` links them.
    """
    normalized = email.lower()
    exists = (
        db.query(Member)
        .filter(
            Member.workspace_id == workspace.workspace_id,
            Member.email == normalized,
        )
        .first()
    )
    if exists is not None:
        raise EmailTakenError(
            f"An account with email '{normalized}' already exists in this workspace"
        )
    member = Member(
        workspace_id=workspace.workspace_id,
        member_name=display_name or f"{first_name} {last_name}",
        member_type="human",
        handle=generate_unique_handle(
            db, workspace.workspace_id, f"{first_name[0]}{last_name}"
        ),
        # TASK2: stop dual-write -- account.email/password_hash (already
        # set on `account`, above) become the source of truth once legacy
        # /auth/login and these two Member columns are retired; until then
        # both are written so old login keeps working unmodified.
        email=normalized,
        password_hash=hash_password(password),
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
        created_at=member.created_at,
        email=member.email,
        first_name=member.first_name,
        last_name=member.last_name,
        company=member.company,
        occupation=member.occupation,
        job_role=member.job_role,
        is_admin=member.is_admin,
        workspace_visibility=workspace.visibility,
    )
