"""Authentication resolution: the single place all credentials are checked.

Humans authenticate with `Authorization: Bearer <JWT>`; agents and
bot_apps use `X-API-Key`. Identity v2 (SMAC-79) has two JWT tiers: ACCOUNT
tokens (global, no workspace) and WORKSPACE tokens (member-scoped).
`get_current_member` accepts workspace-tier tokens only; `get_current_account`
accepts account-tier tokens only -- the token-tier boundary from spec §2,
enforced in both directions. Legacy pre-Identity-v2 tokens (no `scope`
claim at all) are REJECTED as of the Task 2 cutover: every session was
purged by migration B, and every door mints scoped tokens now, so a
scope-less token can only be a forged or otherwise-invalid credential.
"""

import secrets

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import (
    AccountTokenRequiredError,
    InvalidTokenError,
    UnauthorizedError,
    WorkspaceTokenRequiredError,
)
from app.models import Account, Member
from app.security import decode_access_token, decode_access_token_claims, hash_token


def generate_api_key() -> str:
    """Generate a new random API key for an agent/bot_app registration."""
    return secrets.token_urlsafe(32)


def hash_api_key(raw_key: str) -> str:
    """One-way hash of an API key, for storage and lookup (never store the raw key)."""
    return hash_token(raw_key)


def resolve_member(
    db: Session,
    bearer_token: str | None,
    api_key: str | None,
) -> Member | None:
    """Single auth-resolution point: Bearer JWT (humans) or API key (agents/bots).

    Only `scope="workspace"` resolves the member. Anything else that is
    still a well-formed, valid JWT -- `scope="account"` (a real credential
    at the wrong tier) or no `scope` claim at all (a legacy pre-Identity-v2
    token, retired as of the Task 2 cutover) -- raises
    WorkspaceTokenRequiredError rather than being treated as absent, same
    as any other invalid/expired token raises InvalidTokenError. Returns
    None only when no credential was presented at all.
    """
    if bearer_token:
        claims = decode_access_token_claims(bearer_token)
        if claims is None:
            raise InvalidTokenError("Access token is invalid or expired")
        if claims.get("scope") != "workspace":
            raise WorkspaceTokenRequiredError(
                "workspace token required — call POST /workspaces/{id}/token"
            )
        member_id = claims["sub"]
        assert isinstance(member_id, str)  # decode_access_token_claims guarantees this
        member = db.get(Member, member_id)
        if member is None:
            raise InvalidTokenError("Access token references an unknown member")
        return member

    if api_key:
        return (
            db.query(Member).filter(Member.api_key_hash == hash_token(api_key)).first()
        )

    return None


def get_current_member(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Member:
    """FastAPI dependency: the authenticated member, or 401."""
    bearer_token: str | None = None
    if authorization is not None and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:]
    member = resolve_member(db, bearer_token, x_api_key)
    if member is None:
        raise UnauthorizedError(
            "Missing or invalid Authorization bearer token or X-API-Key header"
        )
    return member


def resolve_account(db: Session, bearer_token: str | None) -> Account | None:
    """Resolve an ACCOUNT-tier bearer token only.

    Raises AccountTokenRequiredError for a well-formed workspace/legacy
    token (a real credential, wrong tier) and InvalidTokenError for
    anything invalid/expired/references-nothing. Returns None only when no
    credential was presented at all.
    """
    if not bearer_token:
        return None
    claims = decode_access_token_claims(bearer_token)
    if claims is None:
        raise InvalidTokenError("Access token is invalid or expired")
    if claims.get("scope") != "account":
        raise AccountTokenRequiredError(
            "This endpoint requires an account token — call POST /accounts/login"
        )
    account_id = claims["sub"]
    assert isinstance(account_id, str)  # decode_access_token_claims guarantees this
    account = db.get(Account, account_id)
    if account is None:
        raise InvalidTokenError("Access token references an unknown account")
    return account


def get_current_account(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Account:
    """FastAPI dependency: the authenticated account, or 401."""
    bearer_token: str | None = None
    if authorization is not None and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:]
    account = resolve_account(db, bearer_token)
    if account is None:
        raise UnauthorizedError("Missing or invalid Authorization bearer token")
    return account


def resolve_ws_credential(db: Session, raw: str | None) -> Member | None:
    """Resolve a WebSocket credential: JWT first, then API-key lookup."""
    if not raw:
        return None
    member_id = decode_access_token(raw)
    if member_id is not None:
        return db.get(Member, member_id)
    return db.query(Member).filter(Member.api_key_hash == hash_token(raw)).first()
