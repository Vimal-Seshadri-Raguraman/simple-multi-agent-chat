"""Account creation — the single way any human account is born.

Under the Slack model an account lives inside exactly one workspace, so
creation always happens against a workspace: founding it (is_admin=True),
registering into a public one, redeeming an invite/code into a private one.
All three endpoints delegate here so uniqueness, hashing, naming, and
default-channel landing can never drift apart.
"""

from sqlalchemy.orm import Session

from app.errors import EmailTakenError
from app.handles import generate_unique_handle
from app.models import Member, Workspace
from app.schemas import MemberSelfOut
from app.security import hash_password
from app.unreads import new_channel_membership


def create_member_account(
    db: Session,
    workspace: Workspace,
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    display_name: str | None = None,
    company: str | None = None,
    occupation: str | None = None,
    job_role: str | None = None,
    is_admin: bool = False,
) -> Member:
    """Create a human account inside a workspace. Flushes; caller commits.

    Raises EmailTakenError when the (lowercased) email already has an
    account in this workspace. The same email in another workspace is a
    different account — that's the model.
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
        email=normalized,
        password_hash=hash_password(password),
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
