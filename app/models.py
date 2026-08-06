from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates
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


class Account(Base):
    """A global identity (Identity v2 / SMAC-79): one per human (email +
    password) or one per agent/bot identity (no email/password -- keys
    stay per-workspace on `Member`, Decision 2). Each workspace `Member`
    row is a per-workspace PROFILE that links back here via
    `Member.account_id`.

    `email_key` is the SMAC-68 shadow-key pattern (see
    `Workspace.workspace_name_key`): SQLAlchemy's SQLite dialect can't
    reflect expression indexes, so case-insensitive global uniqueness is
    enforced via a plain `UniqueConstraint` on a lowercased shadow column
    instead, kept in sync by `_sync_email_key` on every write to `email`.
    `email`/`email_key`/`password_hash` are nullable because agent/bot
    accounts have none of the three -- they are identity-only.
    """

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("email_key", name="uq_accounts_email_ci"),)

    account_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    account_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # human | agent | bot_app
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    email_key: Mapped[str | None] = mapped_column(String, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    @validates("email")
    def _sync_email_key(self, key: str, value: str | None) -> str | None:
        """Keep `email_key` = lower(email) on every write, same mechanism as
        `Workspace._sync_name_key` -- see that docstring for why a shadow
        column (not an expression index) is the fallback here."""
        self.email_key = value.lower() if value is not None else None
        return value


class Member(Base):
    """A per-workspace profile (Identity v2 / SMAC-79 Task 2): identity now
    lives entirely on the linked `Account` (email/password, if any) --
    `email`/`password_hash` were dropped from this table by migration B,
    and every member must link to a real account (`account_id` NOT NULL).
    `uq_members_workspace_account` is the new per-workspace invariant: one
    profile per account per workspace (replacing the old per-workspace
    email uniqueness). `member_type` stays here for query convenience but
    must always equal the linked account's `account_type` -- spec §1's
    invariant, covered by a dedicated test rather than a DB constraint.
    """

    __tablename__ = "members"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "account_id", name="uq_members_workspace_account"
        ),
        UniqueConstraint("workspace_id", "handle", name="uq_members_workspace_handle"),
    )

    member_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    member_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    member_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # human | agent | bot_app
    handle: Mapped[str] = mapped_column(String, nullable=False)
    api_key_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    occupation: Mapped[str | None] = mapped_column(String, nullable=True)
    job_role: Mapped[str | None] = mapped_column(String, nullable=True)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspaces.workspace_id"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.account_id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String, default="member", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class Workspace(Base):
    __tablename__ = "workspaces"
    # Not an expression index on lower(workspace_name): attempted and BLOCKED
    # (SMAC-68 task 1, first attempt) -- SQLAlchemy's SQLite dialect cannot
    # reflect expression indexes (verified through 2.0.51), which left the
    # drift guard permanently blind to them. This shadow-column + plain
    # UniqueConstraint is the controller-approved fallback: it reflects
    # normally, so the drift guard can actually prove red -> green.
    __table_args__ = (
        UniqueConstraint("workspace_name_key", name="uq_workspaces_name_ci"),
    )

    workspace_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workspace_name: Mapped[str] = mapped_column(String, nullable=False)
    workspace_name_key: Mapped[str] = mapped_column(String, nullable=False)
    visibility: Mapped[str] = mapped_column(
        String, nullable=False, default="private"
    )  # public | private
    default_channel_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("channels.channel_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    @validates("workspace_name")
    def _sync_name_key(self, key: str, value: str) -> str:
        """The shadow key IS the case-insensitivity: every write to
        workspace_name lowercases into workspace_name_key, so the plain
        unique constraint enforces case-insensitive uniqueness while the
        display name keeps its casing."""
        self.workspace_name_key = value.lower()
        return value


class Channel(Base):
    __tablename__ = "channels"
    # Not an expression index on lower(channel_name): same rationale as
    # Workspace._sync_name_key above -- expression indexes leave the drift
    # guard blind on SQLite, so this uses a shadow column + plain
    # UniqueConstraint (scoped per workspace) instead.
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "channel_name_key", name="uq_channels_workspace_name_ci"
        ),
    )

    channel_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspaces.workspace_id"), nullable=False
    )
    channel_name: Mapped[str] = mapped_column(String, nullable=False)
    channel_name_key: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    @validates("channel_name")
    def _sync_name_key(self, key: str, value: str) -> str:
        """See Workspace._sync_name_key -- same mechanism, scoped per
        workspace."""
        self.channel_name_key = value.lower()
        return value


class ChannelMember(Base):
    __tablename__ = "channel_members"

    channel_id: Mapped[str] = mapped_column(
        String, ForeignKey("channels.channel_id"), primary_key=True
    )
    member_id: Mapped[str] = mapped_column(
        String, ForeignKey("members.member_id"), primary_key=True
    )
    last_read_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


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
    # Nullable as of SMAC-92 (migration `7a3b580f5d0c`): member removal nulls
    # this out rather than deleting the message, so chat history survives a
    # departed member (see app/routers/workspaces.py's remove_member and
    # app/schemas.py's build_message_payload, which renders a placeholder
    # sender when this is None).
    sender_member_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("members.member_id"), nullable=True
    )
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class RefreshToken(Base):
    """A DB-backed opaque refresh token (stored only as a SHA-256 hash).

    One row per issued refresh token. Rows are deleted on rotation
    (/auth/refresh), logout, or when found expired.

    Identity v2 (SMAC-79) two-tier auth: `scope` is `"workspace"` (every
    workspace-birth/join door and `POST /workspaces/{id}/token`,
    `member_id`/`workspace_id` set, `account_id` set to the member's
    linked account) or `"account"` (account-tier tokens from
    `POST /accounts` / `POST /accounts/login`, `account_id` set,
    `member_id`/`workspace_id` NULL -- a brand-new account has no member
    yet). `member_id` is therefore nullable; `/auth/refresh` reads
    `scope` back off the stored row and echoes it into the reissued pair.
    """

    __tablename__ = "refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    member_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("members.member_id"), nullable=True, index=True
    )
    account_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("accounts.account_id"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(
        String, nullable=False, server_default="workspace"
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("workspaces.workspace_id"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class WorkspaceInvite(Base):
    """A pending invitation into a workspace.

    Three kinds, discriminated by invite_type -- every query against
    `code` MUST filter by `invite_type` too (see `app.routers.invites.
    _invite_by_code`, the one place `code` is ever looked up; final
    review F1 was exactly this discriminator being omitted at one call
    site, letting an agent_code redeem as a full human membership):
    - "email": targets one address (lowercased); no expiry; deleted on
      accept/decline/revoke. Redeemed at `POST /workspaces/join` (as a
      seat consumed opportunistically) or `POST /workspaces/{id}/register`.
    - "code": human-facing, shareable, multi-use code stored in PLAINTEXT
      — a deliberate deviation from the hash-everything pattern, because
      codes must be re-viewable and listable by workspace members.
      Bounded exposure: workspace membership only, revocable, 7-day
      expiry. Redeemed at `POST /workspaces/join`.
    - "agent_code": agent-facing, single-use (burnt on redemption), same
      plaintext/7-day-expiry shape as "code" but minted under a different
      capability (`Cap.MINT_AGENT_INVITES`) and redeemed at the separate,
      unauthenticated `POST /agents/join` door.
    """

    __tablename__ = "workspace_invites"

    invite_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspaces.workspace_id"), nullable=False, index=True
    )
    invite_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # email | code | agent_code
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
    at runtime except deletion history (admin checks use Member.role).
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


class Mention(Base):
    """One row per `@handle` resolved in a message: the mentioned member's inbox.

    Pending rows (acknowledged_at is None) are the inbox; acknowledged_at
    set means the mention was handled (read/acked) by that member.
    """

    __tablename__ = "mentions"

    mention_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(
        String, ForeignKey("messages.message_id"), nullable=False, index=True
    )
    # Nullable as of SMAC-92: same reasoning as Message.sender_member_id --
    # a removed member's past mentions survive their row going away.
    mentioned_member_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("members.member_id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
