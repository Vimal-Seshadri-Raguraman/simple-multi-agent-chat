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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    """Generate a new UUID4 string for use as a primary key."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Current UTC time, used for all created_at columns."""
    return datetime.now(timezone.utc)


class Member(Base):
    __tablename__ = "members"

    member_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    member_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    member_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # human | agent | bot_app
    api_key_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )


class Workspace(Base):
    __tablename__ = "workspaces"

    workspace_id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workspace_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
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
        DateTime, default=utcnow, nullable=False
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
        DateTime, default=utcnow, nullable=False
    )
