"""Cryptographic primitives for authentication.

Pure functions only: no database access and no FastAPI imports, so this
module can be tested in isolation and reused by any caller.
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
ACCESS_TOKEN_TTL_MINUTES: int = int(os.getenv("ACCESS_TOKEN_TTL_MINUTES", "15"))
REFRESH_TOKEN_TTL_DAYS: int = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "30"))

_JWT_ALGORITHM = "HS256"


def hash_password(raw: str) -> str:
    """Bcrypt-hash a password for storage (salt is embedded in the hash)."""
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    """Check a candidate password against a stored bcrypt hash."""
    return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(member_id: str) -> str:
    """Issue a short-lived JWT whose subject is the member id."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES)
    return jwt.encode(
        {"sub": member_id, "exp": expires}, SECRET_KEY, algorithm=_JWT_ALGORITHM
    )


def decode_access_token(token: str) -> str | None:
    """Return the member id from a valid JWT, or None if invalid/expired."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[_JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None


def generate_refresh_token() -> str:
    """Generate a new opaque refresh token (returned to the client once)."""
    return secrets.token_urlsafe(48)


def hash_token(raw: str) -> str:
    """One-way hash for storing/looking up opaque tokens (never store raw)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
