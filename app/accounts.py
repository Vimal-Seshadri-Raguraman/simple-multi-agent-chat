"""Account creation — the single way any human account is born.

Under the Slack model an account lives inside exactly one workspace, so
creation always happens against a workspace: founding it (is_admin=True),
registering into a public one, redeeming an invite/code into a private one.
All three endpoints delegate here so uniqueness, hashing, naming, and
default-channel landing can never drift apart.
"""

from sqlalchemy.orm import Session

from app.errors import EmailTakenError
from app.models import ChannelMember, Member, Workspace
from app.security import hash_password


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
            ChannelMember(
                channel_id=workspace.default_channel_id, member_id=member.member_id
            )
        )
    return member
