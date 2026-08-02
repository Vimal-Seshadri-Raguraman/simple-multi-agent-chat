from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    """Generate a new UUID4 string for use as a primary key."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Current UTC time, used for all created_at columns."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    """A timezone-aware DateTime that round-trips correctly through SQLite.

    Plain `DateTime(timezone=True)` is not sufficient on its own: SQLite has no
    native timezone-aware storage, so the stock type still hands back a naive
    datetime after a DB round-trip, silently dropping the UTC offset that
    `.isoformat()` (used by `build_message_payload`) needs to emit. Every value
    written through this column is produced by `utcnow()`, so it's always UTC;
    we simply reattach that offset on the way back out.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Member(Base):
    __tablename__ = "members"

    member_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    member_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    member_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # human | agent | bot_app
    api_key_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True, index=True
    )
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    occupation: Mapped[str | None] = mapped_column(String, nullable=True)
    job_role: Mapped[str | None] = mapped_column(String, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("workspaces.workspace_id"), nullable=True, index=True
    )  # Staging: nullable until the Slack-model cutover makes it required.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class Workspace(Base):
    __tablename__ = "workspaces"

    workspace_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workspace_name: Mapped[str] = mapped_column(String, nullable=False)
    visibility: Mapped[str] = mapped_column(
        String, nullable=False, default="private"
    )  # public | private
    default_channel_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("channels.channel_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspaces.workspace_id"), primary_key=True
    )
    member_id: Mapped[str] = mapped_column(
        String, ForeignKey("members.member_id"), primary_key=True
    )


class Channel(Base):
    __tablename__ = "channels"

    channel_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspaces.workspace_id"), nullable=False
    )
    channel_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class ChannelMember(Base):
    __tablename__ = "channel_members"

    channel_id: Mapped[str] = mapped_column(
        String, ForeignKey("channels.channel_id"), primary_key=True
    )
    member_id: Mapped[str] = mapped_column(
        String, ForeignKey("members.member_id"), primary_key=True
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_channel_seq", "channel_id", "seq"),
        UniqueConstraint("channel_id", "seq", name="uq_messages_channel_seq"),
    )

    message_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    channel_id: Mapped[str] = mapped_column(
        String, ForeignKey("channels.channel_id"), nullable=False
    )
    sender_member_id: Mapped[str] = mapped_column(
        String, ForeignKey("members.member_id"), nullable=False
    )
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class RefreshToken(Base):
    """A DB-backed opaque refresh token (stored only as a SHA-256 hash).

    One row per issued refresh token. Rows are deleted on rotation
    (/auth/refresh), logout, or when found expired.
    """

    __tablename__ = "refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    member_id: Mapped[str] = mapped_column(
        String, ForeignKey("members.member_id"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class WorkspaceInvite(Base):
    """A pending invitation into a workspace.

    Two kinds, discriminated by invite_type:
    - "email": targets one address (lowercased); no expiry; deleted on
      accept/decline/revoke.
    - "code": shareable multi-use code stored in PLAINTEXT — a deliberate
      deviation from the hash-everything pattern, because codes must be
      re-viewable and listable by workspace members. Bounded exposure:
      workspace membership only, revocable, 7-day expiry.
    """

    __tablename__ = "workspace_invites"

    invite_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspaces.workspace_id"), nullable=False, index=True
    )
    invite_type: Mapped[str] = mapped_column(String, nullable=False)  # email | code
    email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    code: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True, index=True
    )
    created_by: Mapped[str] = mapped_column(
        String, ForeignKey("members.member_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class WorkspaceRecord(Base):
    """Permanent ledger row for every workspace that ever existed.

    Written at creation (status "active"), updated at deletion (status
    "deleted" + who/when). Never deleted — the workspaces row itself is
    hard-deleted so live queries need no status filtering; this tombstone
    carries the audit trail and is the single source of truth for nothing
    at runtime except deletion history (admin checks use Member.is_admin).
    """

    __tablename__ = "workspace_records"

    workspace_id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_name: Mapped[str] = mapped_column(String, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="active"
    )  # active | deleted
    deleted_by: Mapped[str | None] = mapped_column(String, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
