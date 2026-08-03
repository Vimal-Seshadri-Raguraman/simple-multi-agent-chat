"""Authentication endpoints: login, refresh, logout (registration lives under /workspaces)."""

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.database import get_db
from app.errors import InvalidCredentialsError, InvalidTokenError
from app.models import Member, RefreshToken, Workspace, utcnow
from app.schemas import (
    DiscoverIn,
    DiscoverOut,
    DiscoverWorkspaceOut,
    LoginIn,
    LogoutIn,
    RefreshIn,
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

# A precomputed bcrypt hash of a password no account will ever have. When
# POST /auth/discover finds zero email-matching accounts, it still runs one
# verify_password() against this hash (see `discover` below) purely to burn
# the same order-of-magnitude time a real "email matches, password is
# wrong" attempt would -- so "unknown email" isn't distinguishable from
# "known email, wrong password" by response latency, matching the
# byte-identical-response invariant already required of the response body
# (spec §2.5).
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing-parity-only")


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


@router.post("/auth/discover", response_model=DiscoverOut)
def discover(body: DiscoverIn, db: Session = Depends(get_db)) -> DiscoverOut:
    """Find every workspace whose account matches the given credentials.

    Unauthenticated by design (spec §2.5): this is how the TUI's `/login`
    decides whether to auto-login (one match), show a workspace picker
    (several), or fall into the join flow (zero) -- no workspace_id is
    known up front, unlike /auth/login.

    Email is normalized exactly like /auth/login (lowercased). Candidates
    are every human account (password_hash IS NOT NULL -- agents/bots
    never have one, see Member) whose email matches, ordered by
    workspace_name so the response is deterministically ordered. Every
    candidate's password is verified -- there is no early exit once one
    match is found, so the response time doesn't reveal how many
    workspaces share the email beyond what the (also uniform) matched-list
    length already would. Only rows where the password verifies are
    returned, so the caller only ever learns of workspaces they can
    actually open.

    When zero accounts have a matching email, one dummy password
    verification still runs (against `_DUMMY_PASSWORD_HASH`, not any real
    account's hash) so that "unknown email" costs the same as "known
    email, wrong password" and both produce the exact same
    byte-identical `{"workspaces": []}` body -- neither timing nor content
    can be used to probe which emails or workspaces exist.

    No tokens are issued here: the TUI follows up with the existing
    workspace-first /auth/login for whichever workspace the caller picks,
    so all token-issuing logic stays in one place.
    """
    normalized_email = body.email.lower()
    candidates = (
        db.query(Member, Workspace)
        .join(Workspace, Workspace.workspace_id == Member.workspace_id)
        .filter(
            Member.email == normalized_email,
            Member.password_hash.is_not(None),
        )
        .order_by(Workspace.workspace_name)
        .all()
    )
    if not candidates:
        verify_password(body.password, _DUMMY_PASSWORD_HASH)
        return DiscoverOut(workspaces=[])
    matches: list[DiscoverWorkspaceOut] = []
    for member, workspace in candidates:
        # The `.is_not(None)` filter above guarantees this at the SQL level;
        # mypy can't see through it, so narrow explicitly rather than
        # asserting away real None-safety.
        password_hash = member.password_hash
        if password_hash is not None and verify_password(body.password, password_hash):
            matches.append(
                DiscoverWorkspaceOut(
                    workspace_id=workspace.workspace_id,
                    workspace_name=workspace.workspace_name,
                )
            )
    return DiscoverOut(workspaces=matches)


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
