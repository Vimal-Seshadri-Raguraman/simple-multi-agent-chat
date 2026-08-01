from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
