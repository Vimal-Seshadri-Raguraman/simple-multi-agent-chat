"""Authentication endpoints: login, refresh, logout (registration lives under /workspaces)."""

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.database import get_db
from app.errors import InvalidCredentialsError, InvalidTokenError
from app.models import Member, RefreshToken, utcnow
from app.schemas import LoginIn, LogoutIn, RefreshIn, TokenPairOut
from app.security import (
    ACCESS_TOKEN_TTL_MINUTES,
    REFRESH_TOKEN_TTL_DAYS,
    create_access_token,
    generate_refresh_token,
    hash_token,
    verify_password,
)

router = APIRouter()

_LOGIN_FAILED_MESSAGE = "Invalid email or password"


def _issue_token_pair(db: Session, member: Member) -> TokenPairOut:
    """Create an access JWT and a stored, hashed refresh token for a member."""
    raw_refresh = generate_refresh_token()
    db.add(
        RefreshToken(
            token_hash=hash_token(raw_refresh),
            member_id=member.member_id,
            expires_at=utcnow() + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
        )
    )
    db.commit()
    return TokenPairOut(
        access_token=create_access_token(member.member_id),
        refresh_token=raw_refresh,
        expires_in=ACCESS_TOKEN_TTL_MINUTES * 60,
    )


@router.post("/auth/login", response_model=TokenPairOut)
def login(body: LoginIn, db: Session = Depends(get_db)) -> TokenPairOut:
    """Exchange workspace_id+email+password for a token pair.

    Unknown workspace_id, unknown email, and wrong password intentionally
    raise the identical error so responses cannot be used to probe which
    workspaces or emails exist.
    """
    member = (
        db.query(Member)
        .filter(
            Member.workspace_id == body.workspace_id,
            Member.email == body.email.lower(),
        )
        .first()
    )
    if (
        member is None
        or member.password_hash is None
        or not verify_password(body.password, member.password_hash)
    ):
        raise InvalidCredentialsError(_LOGIN_FAILED_MESSAGE)
    return _issue_token_pair(db, member)


@router.post("/auth/refresh", response_model=TokenPairOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)) -> TokenPairOut:
    """Rotate a refresh token: revoke the presented one, issue a new pair."""
    row = db.get(RefreshToken, hash_token(body.refresh_token))
    if row is None:
        raise InvalidTokenError("Refresh token is invalid or expired")
    if row.expires_at < utcnow():
        db.delete(row)
        db.commit()
        raise InvalidTokenError("Refresh token is invalid or expired")
    member = db.get(Member, row.member_id)
    db.delete(row)
    if member is None:
        # SQLite FK enforcement (PRAGMA foreign_keys) isn't guaranteed to be
        # on, so a refresh token can outlive its owning member. Treat a
        # missing owner the same as any other invalid/expired token.
        db.commit()
        raise InvalidTokenError("Refresh token is invalid or expired")
    return _issue_token_pair(db, member)


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
