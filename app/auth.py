"""Authentication resolution: the single place all credentials are checked.

Humans authenticate with `Authorization: Bearer <JWT>` (obtained from
/auth/login after founding or registering via workspace endpoints);
agents and bot_apps use `X-API-Key`.
"""

import secrets

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import InvalidTokenError, UnauthorizedError
from app.models import Member
from app.security import decode_access_token, hash_token


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

    Raises InvalidTokenError when a bearer token is presented but is
    expired, malformed, or references a deleted member. Returns None only
    when no valid credential was presented at all.
    """
    if bearer_token:
        member_id = decode_access_token(bearer_token)
        if member_id is None:
            raise InvalidTokenError("Access token is invalid or expired")
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


def resolve_ws_credential(db: Session, raw: str | None) -> Member | None:
    """Resolve a WebSocket credential: JWT first, then API-key lookup."""
    if not raw:
        return None
    member_id = decode_access_token(raw)
    if member_id is not None:
        return db.get(Member, member_id)
    return db.query(Member).filter(Member.api_key_hash == hash_token(raw)).first()
