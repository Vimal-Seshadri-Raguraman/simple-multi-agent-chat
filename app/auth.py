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
from app.security import decode_access_token_claims, hash_token


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


def resolve_member_or_account(
    db: Session, bearer_token: str | None
) -> tuple[Member | None, Account | None]:
    """Resolve a bearer token at EITHER tier -- unlike `resolve_member`/
    `resolve_account`, which each lock to one tier and reject the other as
    wrong-tier, this accepts a workspace-tier token as `(member, None)` or
    an account-tier token as `(None, account)`. For `/auth/logout` only
    (spec §2 lists it in the account-scope surface, but it must also stay
    reachable with a workspace token -- a bare account with no workspace
    yet still needs a way to revoke its refresh token). A scope-less
    (pre-Identity-v2) token has no tier to resolve at either door, so it's
    treated the same as any other invalid/expired token. Returns `(None,
    None)` only when no credential was presented at all.
    """
    if not bearer_token:
        return None, None
    claims = decode_access_token_claims(bearer_token)
    if claims is None:
        raise InvalidTokenError("Access token is invalid or expired")
    scope = claims.get("scope")
    if scope == "account":
        account_id = claims["sub"]
        assert isinstance(account_id, str)  # decode_access_token_claims guarantees this
        account = db.get(Account, account_id)
        if account is None:
            raise InvalidTokenError("Access token references an unknown account")
        return None, account
    if scope == "workspace":
        member_id = claims["sub"]
        assert isinstance(member_id, str)  # decode_access_token_claims guarantees this
        member = db.get(Member, member_id)
        if member is None:
            raise InvalidTokenError("Access token references an unknown member")
        return member, None
    raise InvalidTokenError("Access token is invalid or expired")


def get_current_member_or_account(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> tuple[Member | None, Account | None]:
    """FastAPI dependency: the authenticated member OR account, or 401.

    Exactly one element of the returned pair is non-None on success.
    """
    bearer_token: str | None = None
    if authorization is not None and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:]
    member, account = resolve_member_or_account(db, bearer_token)
    if member is None and account is None:
        raise UnauthorizedError("Missing or invalid Authorization bearer token")
    return member, account


def resolve_ws_credential(db: Session, raw: str | None) -> Member | None:
    """Resolve a WebSocket credential: JWT first, then API-key lookup.

    Mirrors `resolve_member`'s HTTP-side tier gate (spec §2): only a JWT
    with `scope == "workspace"` resolves. A well-formed JWT with no
    `scope` claim at all (legacy pre-Identity-v2 shape) or `scope ==
    "account"` (right tier of credential, wrong door) is a real,
    valid-looking token that must still be REJECTED here -- returning
    None, same as any other bad credential, which both socket routes
    already turn into `close(code=4401)`. Without this gate, a
    scope-less token that `get_current_member` rejects on every HTTP
    route would still open a live channel/events feed (the WS path sat
    outside the two-tier boundary the rest of the app enforces). The
    fallback to API-key lookup only runs when `raw` isn't a decodable
    JWT at all (a real API key never is one).
    """
    if not raw:
        return None
    claims = decode_access_token_claims(raw)
    if claims is not None:
        if claims.get("scope") != "workspace":
            return None
        member_id = claims["sub"]
        assert isinstance(member_id, str)  # decode_access_token_claims guarantees this
        return db.get(Member, member_id)
    return db.query(Member).filter(Member.api_key_hash == hash_token(raw)).first()
