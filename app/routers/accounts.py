"""Account-tier endpoints (SMAC-79 Task 1, spec §2): global signup/login
and account self-view. No `workspace_id` appears anywhere in this file --
the whole point of the account tier is that it exists independently of
any workspace (spec Decision 1). `POST /workspaces/{id}/token`, the one
door from an account token to a workspace token pair, lives on
`app.routers.workspaces` instead (it's workspace-shaped, not
account-shaped, and keeps that router the single place workspace tokens
are minted).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounts import create_account
from app.auth import get_current_account
from app.database import get_db
from app.errors import InvalidCredentialsError
from app.models import Account, Member, Workspace
from app.routers.auth import _issue_account_token_pair
from app.schemas import (
    AccountAuthOut,
    AccountCreateIn,
    AccountLoginIn,
    AccountLoginOut,
    AccountMembershipOut,
    AccountMeOut,
    AccountOut,
)
from app.security import hash_password, verify_password

router = APIRouter()

_LOGIN_FAILED_MESSAGE = "Invalid email or password"

# A precomputed bcrypt hash of a password no account will ever have, so an
# unknown email still costs one bcrypt verify -- the same timing-parity
# discipline POST /auth/discover used (see that function's docstring,
# app/routers/auth.py), ported here since /auth/discover retires in Task 2.
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing-parity-only")


def _memberships_for(db: Session, account: Account) -> list[AccountMembershipOut]:
    """Every workspace profile this account already has, ordered by
    workspace name (deterministic, same convention as the retiring
    /auth/discover)."""
    rows = (
        db.query(Member, Workspace)
        .join(Workspace, Workspace.workspace_id == Member.workspace_id)
        .filter(Member.account_id == account.account_id)
        .order_by(Workspace.workspace_name)
        .all()
    )
    return [
        AccountMembershipOut(
            workspace_id=workspace.workspace_id,
            workspace_name=workspace.workspace_name,
            member_id=member.member_id,
            handle=member.handle,
        )
        for member, workspace in rows
    ]


@router.post("/accounts", response_model=AccountAuthOut)
def create_account_endpoint(
    body: AccountCreateIn, db: Session = Depends(get_db)
) -> AccountAuthOut:
    """Create a global account: `email`+`password`. Auto-login (spec §2) --
    returns the account plus an account-tier token pair, same as founding
    a workspace already returns tokens immediately."""
    account = create_account(db, body.email, body.password)
    db.commit()
    db.refresh(account)
    tokens = _issue_account_token_pair(db, account)
    return AccountAuthOut(account=AccountOut.model_validate(account), tokens=tokens)


@router.post("/accounts/login", response_model=AccountLoginOut)
def login(body: AccountLoginIn, db: Session = Depends(get_db)) -> AccountLoginOut:
    """Global login: no `workspace_id` (unlike the legacy, workspace-first
    `POST /auth/login`). Unknown email and wrong password produce
    byte-identical 401 bodies with dummy-verify timing parity -- porting
    the discipline `POST /auth/discover` established (spec §7), since
    that endpoint retires in Task 2 and this is its permanent successor
    (a binding ROUTE DECISION: this never shares a path with
    `/auth/login`). Success returns the account, account-tier tokens, and
    every workspace this account already has a profile in -- the real
    thing `/auth/discover` only simulated.
    """
    account = db.query(Account).filter(Account.email_key == body.email.lower()).first()
    password_hash = account.password_hash if account is not None else None
    if password_hash is None:
        verify_password(body.password, _DUMMY_PASSWORD_HASH)
        raise InvalidCredentialsError(_LOGIN_FAILED_MESSAGE)
    if not verify_password(body.password, password_hash):
        raise InvalidCredentialsError(_LOGIN_FAILED_MESSAGE)
    assert account is not None  # password_hash is not None implies account is not None
    tokens = _issue_account_token_pair(db, account)
    return AccountLoginOut(
        account=AccountOut.model_validate(account),
        tokens=tokens,
        workspaces=_memberships_for(db, account),
    )


@router.get("/accounts/me", response_model=AccountMeOut)
def get_my_account(
    account: Account = Depends(get_current_account), db: Session = Depends(get_db)
) -> AccountMeOut:
    """The caller's own account plus their workspace memberships."""
    return AccountMeOut(
        account_id=account.account_id,
        email=account.email,
        created_at=account.created_at,
        memberships=_memberships_for(db, account),
    )
