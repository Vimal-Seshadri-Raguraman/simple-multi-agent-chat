import hashlib
import secrets

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import UnauthorizedError
from app.models import Member


def generate_api_key() -> str:
    """Generate a new random API key for an agent/bot_app registration."""
    return secrets.token_urlsafe(32)


def hash_api_key(raw_key: str) -> str:
    """One-way hash of an API key, for storage and lookup (never store the raw key)."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def resolve_member(
    db: Session,
    dev_member_id: str | None,
    dev_member_name: str | None,
    api_key: str | None,
) -> Member | None:
    """
    Single auth-resolution point. Today: dev header or API key.
    Later: swap this function's body for Entra ID JWT validation — no caller changes.
    """
    if dev_member_id:
        member = db.get(Member, dev_member_id)
        if member is None:
            member = Member(
                member_id=dev_member_id,
                member_name=dev_member_name or dev_member_id,
                member_type="human",
            )
            db.add(member)
            db.commit()
            db.refresh(member)
        return member

    if api_key:
        key_hash = hash_api_key(api_key)
        return db.query(Member).filter(Member.api_key_hash == key_hash).first()

    return None


def get_current_member(
    x_dev_member_id: str | None = Header(default=None),
    x_dev_member_name: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Member:
    member = resolve_member(db, x_dev_member_id, x_dev_member_name, x_api_key)
    if member is None:
        raise UnauthorizedError(
            "Missing or invalid X-Dev-Member-Id or X-API-Key header"
        )
    return member
