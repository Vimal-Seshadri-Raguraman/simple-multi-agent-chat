"""Authentication endpoints: register, login, refresh, logout."""

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.database import get_db
from app.errors import EmailTakenError, InvalidCredentialsError, InvalidTokenError
from app.models import Member, RefreshToken, utcnow
from app.schemas import (
    LoginIn,
    LogoutIn,
    MemberSelfOut,
    RefreshIn,
    RegisterIn,
    RegisterOut,
    TokenPairOut,
)
from app.security import (
    ACCESS_TOKEN_TTL_MINUTES,
    REFRESH_TOKEN_TTL_DAYS,
    create_access_token,
    generate_refresh_token,
    hash_password,
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


@router.post("/auth/register", response_model=RegisterOut)
def register(body: RegisterIn, db: Session = Depends(get_db)) -> RegisterOut:
    """Create a human member account and log it in (returns a token pair)."""
    email = body.email.lower()
    if db.query(Member).filter(Member.email == email).first() is not None:
        raise EmailTakenError(f"An account with email '{email}' already exists")
    member = Member(
        member_name=body.display_name or f"{body.first_name} {body.last_name}",
        member_type="human",
        email=email,
        password_hash=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        company=body.company,
        occupation=body.occupation,
        job_role=body.job_role,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    tokens = _issue_token_pair(db, member)
    return RegisterOut(
        member=MemberSelfOut.model_validate(member),
        **tokens.model_dump(),
    )


@router.post("/auth/login", response_model=TokenPairOut)
def login(body: LoginIn, db: Session = Depends(get_db)) -> TokenPairOut:
    """Exchange email+password for a token pair.

    Unknown email and wrong password intentionally raise the identical
    error so responses cannot be used to probe which emails exist.
    """
    member = db.query(Member).filter(Member.email == body.email.lower()).first()
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
