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

_DEFAULT_SECRET_KEY = "change-me-in-production"

SECRET_KEY: str = os.getenv("SECRET_KEY", _DEFAULT_SECRET_KEY)
ACCESS_TOKEN_TTL_MINUTES: int = int(os.getenv("ACCESS_TOKEN_TTL_MINUTES", "15"))
REFRESH_TOKEN_TTL_DAYS: int = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "30"))

_JWT_ALGORITHM = "HS256"


_MIN_SECRET_KEY_BYTES = 32


def check_secret_key_is_safe_for_environment() -> None:
    """Fail fast if a production-like deployment is using an unsafe JWT secret.

    `SECRET_KEY` silently falling back to the publicly-known string
    "change-me-in-production" means anyone can forge valid JWTs against a
    production deployment that forgot to set the env var. We can't tell
    "forgot" from "intentional dev/test run" just by looking at
    `SECRET_KEY`, so we key the check off `ENVIRONMENT`: only
    "development" and "test" (the values used locally and in CI) are
    allowed to run with the default secret. Anything else -- including a
    typo'd or unset `ENVIRONMENT` in a real deployment -- raises instead of
    silently signing forgeable tokens.

    Production-like environments must also use a secret of at least 32
    bytes: RFC 7518 Section 3.2 requires HS256 keys no shorter than the
    hash output, and short secrets are realistically brute-forceable
    offline from any captured token.
    """
    environment = os.getenv("ENVIRONMENT", "development")
    if environment in {"development", "test"}:
        return
    if SECRET_KEY == _DEFAULT_SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY is unset (using the public default "
            f"'{_DEFAULT_SECRET_KEY}') while ENVIRONMENT="
            f"'{environment}'. Set a real SECRET_KEY before running in this "
            "environment."
        )
    if len(SECRET_KEY.encode("utf-8")) < _MIN_SECRET_KEY_BYTES:
        raise RuntimeError(
            f"SECRET_KEY is only {len(SECRET_KEY.encode('utf-8'))} bytes; "
            f"HS256 requires at least {_MIN_SECRET_KEY_BYTES} bytes "
            f"(RFC 7518) in ENVIRONMENT='{environment}'. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )


check_secret_key_is_safe_for_environment()


def hash_password(raw: str) -> str:
    """Bcrypt-hash a password for storage (salt is embedded in the hash)."""
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    """Check a candidate password against a stored bcrypt hash."""
    return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(
    subject_id: str, *, scope: str | None = None, account_id: str | None = None
) -> str:
    """Issue a short-lived JWT.

    `subject_id` is a member id for workspace-tier tokens or an account id
    for account-tier tokens. `scope` is omitted by default, producing the
    bare `sub`+`exp` pre-Identity-v2 (SMAC-79) shape -- no live caller
    mints this anymore (every door mints `scope="workspace"` or
    `scope="account"` now, and `app.auth.resolve_member`/`resolve_account`
    reject anything else), but the capability stays for low-level JWT
    mechanics tests. Pass `scope="workspace"` or `scope="account"` for a
    real two-tier token; workspace-tier tokens additionally carry
    `account_id` (today's claim shape + account_id, per spec §2).
    """
    expires = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES)
    claims: dict[str, object] = {"sub": subject_id, "exp": expires}
    if scope is not None:
        claims["scope"] = scope
    if account_id is not None:
        claims["account_id"] = account_id
    return jwt.encode(claims, SECRET_KEY, algorithm=_JWT_ALGORITHM)


def decode_access_token_claims(token: str) -> dict[str, object] | None:
    """Return the full claim set of a valid JWT (at least `sub`+`exp`,
    optionally `scope`/`account_id`), or None if invalid/expired/malformed.

    The two-tier auth dependencies (`app.auth.resolve_member`,
    `resolve_account`) need the `scope` claim to enforce the token-tier
    boundary; `decode_access_token` below stays as the sub-only
    convenience wrapper every pre-existing caller already uses.
    """
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[_JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError:
        return None
    if not isinstance(payload.get("sub"), str):
        return None
    return payload


def decode_access_token(token: str) -> str | None:
    """Return the subject id from a valid JWT, or None if invalid/expired."""
    payload = decode_access_token_claims(token)
    if payload is None:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None


def generate_refresh_token() -> str:
    """Generate a new opaque refresh token (returned to the client once)."""
    return secrets.token_urlsafe(48)


def hash_token(raw: str) -> str:
    """One-way hash for storing/looking up opaque tokens (never store raw)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
