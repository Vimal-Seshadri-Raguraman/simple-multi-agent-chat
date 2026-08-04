"""Authentication endpoints: refresh, logout.

Identity v2 (SMAC-79 Task 2 cutover): `/auth/login` (workspace-scoped) and
`/auth/discover` are RETIRED -- `POST /accounts/login` (global) is their
permanent successor (`app/routers/accounts.py`). Workspace-birth doors
(`POST /workspaces`, `.../register`, `/workspaces/join`) are all
account-authed now and mint workspace-tier tokens directly; there is no
more "legacy" (scope-less) token shape being issued anywhere.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.database import get_db
from app.errors import InvalidTokenError
from app.models import Account, Member, RefreshToken, utcnow
from app.schemas import LogoutIn, RefreshIn, TokenPairOut
from app.security import (
    ACCESS_TOKEN_TTL_MINUTES,
    REFRESH_TOKEN_TTL_DAYS,
    create_access_token,
    generate_refresh_token,
    hash_token,
)

router = APIRouter()


def _issue_workspace_token_pair(db: Session, member: Member) -> TokenPairOut:
    """Create a WORKSPACE-tier token pair (spec §2): the JWT carries an
    explicit `scope="workspace"` claim + `account_id` (today's claims
    shape + account_id); the stored refresh row records `scope`,
    `workspace_id`, and `account_id` so `/auth/refresh` can echo them back
    on rotation. Used by every account-authed workspace-birth door
    (found/register/join) and by `POST /workspaces/{id}/token`, and by
    `/auth/refresh` when rotating a workspace-scope row.
    """
    raw_refresh = generate_refresh_token()
    db.add(
        RefreshToken(
            token_hash=hash_token(raw_refresh),
            member_id=member.member_id,
            account_id=member.account_id,
            scope="workspace",
            workspace_id=member.workspace_id,
            expires_at=utcnow() + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
        )
    )
    db.commit()
    return TokenPairOut(
        access_token=create_access_token(
            member.member_id, scope="workspace", account_id=member.account_id
        ),
        refresh_token=raw_refresh,
        expires_in=ACCESS_TOKEN_TTL_MINUTES * 60,
    )


def _issue_account_token_pair(db: Session, account: Account) -> TokenPairOut:
    """Create an ACCOUNT-tier token pair (spec §2): JWT `scope="account"`,
    subject is the account id; the stored refresh row has `account_id`
    set and `member_id`/`workspace_id` NULL (a brand-new account may have
    no member at all yet). Used by `POST /accounts`, `POST
    /accounts/login`, and `/auth/refresh` when rotating an account-scope
    row.
    """
    raw_refresh = generate_refresh_token()
    db.add(
        RefreshToken(
            token_hash=hash_token(raw_refresh),
            account_id=account.account_id,
            scope="account",
            expires_at=utcnow() + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
        )
    )
    db.commit()
    return TokenPairOut(
        access_token=create_access_token(account.account_id, scope="account"),
        refresh_token=raw_refresh,
        expires_in=ACCESS_TOKEN_TTL_MINUTES * 60,
    )


@router.post("/auth/refresh", response_model=TokenPairOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)) -> TokenPairOut:
    """Rotate a refresh token: revoke the presented one, issue a new pair
    in the SAME scope the stored row carries (spec §2) -- `scope` is
    always explicitly set on every row now (`_issue_workspace_token_pair`/
    `_issue_account_token_pair`, both below), so this is a straight
    echo-back, no legacy default to fall through to.
    """
    row = db.get(RefreshToken, hash_token(body.refresh_token))
    if row is None:
        raise InvalidTokenError("Refresh token is invalid or expired")
    if row.expires_at < utcnow():
        db.delete(row)
        db.commit()
        raise InvalidTokenError("Refresh token is invalid or expired")

    if row.scope == "account":
        account = db.get(Account, row.account_id) if row.account_id else None
        db.delete(row)
        if account is None:
            db.commit()
            raise InvalidTokenError("Refresh token is invalid or expired")
        return _issue_account_token_pair(db, account)

    member = db.get(Member, row.member_id) if row.member_id else None
    db.delete(row)
    if member is None:
        # SQLite FK enforcement (PRAGMA foreign_keys) isn't guaranteed to be
        # on, so a refresh token can outlive its owning member. Treat a
        # missing owner the same as any other invalid/expired token.
        db.commit()
        raise InvalidTokenError("Refresh token is invalid or expired")
    return _issue_workspace_token_pair(db, member)


@router.post("/auth/logout")
def logout(
    body: LogoutIn,
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Revoke one refresh token belonging to the caller.

    Idempotent: unknown or already-revoked tokens still return 200 so the
    response can't be used to probe token validity. The access token remains
    valid until its natural expiry (accepted JWT tradeoff, see spec).
    """
    row = db.get(RefreshToken, hash_token(body.refresh_token))
    if row is not None and row.member_id == current_member.member_id:
        db.delete(row)
        db.commit()
    return {"status": "logged_out"}
