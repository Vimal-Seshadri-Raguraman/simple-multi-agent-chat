from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import Channel, Member, Message, Workspace


class WorkspaceCreate(BaseModel):
    workspace_name: str


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    workspace_id: str
    workspace_name: str


class ChannelCreate(BaseModel):
    channel_name: str


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    channel_id: str
    channel_name: str


class MemberIdIn(BaseModel):
    member_id: str


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    member_id: str
    member_name: str
    member_type: str
    created_at: datetime


class MemberRegisterIn(BaseModel):
    member_name: str


class MemberRegisterOut(BaseModel):
    member_id: str
    member_name: str
    member_type: str
    api_key: str


class MessageCreate(BaseModel):
    message_text: str = Field(min_length=1, max_length=4000)


class MemberSelfOut(BaseModel):
    """A member's own full profile, including their private email."""

    model_config = ConfigDict(from_attributes=True)
    member_id: str
    member_name: str
    member_type: str
    created_at: datetime
    email: str | None
    first_name: str | None
    last_name: str | None
    company: str | None
    occupation: str | None
    job_role: str | None


class RegisterIn(BaseModel):
    """Registration request. Names are required; the rest of the profile is optional."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    display_name: str | None = Field(default=None, min_length=1)
    company: str | None = None
    occupation: str | None = None
    job_role: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenPairOut(BaseModel):
    """An access/refresh token pair, mirrored after every login/refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RegisterOut(TokenPairOut):
    """Registration response: the new member's profile plus a token pair."""

    member: MemberSelfOut


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str


def build_message_payload(
    message: Message, workspace: Workspace, channel: Channel, sender: Member
) -> dict:
    """The single source of truth for the wire schema shared by REST and WebSocket."""
    return {
        "timestamp": message.created_at.isoformat(),
        "workspace": {
            "workspace_id": workspace.workspace_id,
            "workspace_name": workspace.workspace_name,
        },
        "Channel": {
            "channel_id": channel.channel_id,
            "channel_name": channel.channel_name,
        },
        "Sender": {"member_id": sender.member_id, "member_name": sender.member_name},
        "Message": {
            "message_id": message.message_id,
            "message_text": message.message_text,
        },
    }
