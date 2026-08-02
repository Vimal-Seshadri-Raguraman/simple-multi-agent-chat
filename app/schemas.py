from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models import Channel, Member, Message, Workspace


class WorkspaceCreate(BaseModel):
    workspace_name: str


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    workspace_id: str
    workspace_name: str
    visibility: str


class WorkspaceSearchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    workspace_id: str
    workspace_name: str
    visibility: str


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
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    occupation: str | None = None
    job_role: str | None = None


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


class MemberProfileUpdate(BaseModel):
    """Partial update of one's own profile. Email/password are not editable here."""

    display_name: str | None = Field(default=None, min_length=1)
    first_name: str | None = Field(default=None, min_length=1)
    last_name: str | None = Field(default=None, min_length=1)
    company: str | None = None
    occupation: str | None = None
    job_role: str | None = None

    @field_validator("display_name", "first_name", "last_name", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: str | None) -> str | None:
        """Reject an explicit JSON null for required-on-registration fields.

        `min_length` only constrains the `str` branch of `str | None`; an
        explicit JSON `null` bypasses it entirely. Pydantic only invokes
        validators when the field is actually present in the payload (by
        default it does not validate an unset field's default), so this
        only fires when the client explicitly sends `null` -- omitting the
        field entirely still leaves it untouched, as intended for a partial
        update. Without this check, `PATCH /members/me {"display_name":
        null}` would set the member's NOT NULL `member_name` column to
        None (raw 500 IntegrityError outside the error envelope), and
        `{"first_name": null}` / `{"last_name": null}` would silently wipe
        registration-required fields (200 OK). company/occupation/job_role
        are intentionally exempt: explicitly clearing them is legitimate.
        """
        if value is None:
            raise ValueError("must not be null")
        return value


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
    workspace_id: str
    email: EmailStr
    password: str


class TokenPairOut(BaseModel):
    """An access/refresh token pair, mirrored after every login/refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class FoundWorkspaceIn(RegisterIn):
    """Found a workspace: workspace details + the founder's account in one body."""

    workspace_name: str = Field(min_length=1)
    visibility: Literal["public", "private"] = "private"


class CodeRegisterIn(RegisterIn):
    """Register into a workspace identified by a shareable invite code."""

    code: str = Field(min_length=1)


class WorkspaceAuthOut(TokenPairOut):
    """Every account-birth endpoint returns this: you're signed up AND logged in."""

    member: MemberSelfOut
    workspace: WorkspaceOut


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str


class InviteCreateIn(BaseModel):
    """Create an invite: email-targeted, or a shareable code."""

    invite_type: Literal["email", "code"]
    email: EmailStr | None = None

    @model_validator(mode="after")
    def _email_iff_email_type(self) -> "InviteCreateIn":
        if self.invite_type == "email" and self.email is None:
            raise ValueError("email is required for invite_type 'email'")
        if self.invite_type == "code" and self.email is not None:
            raise ValueError("email is not allowed for invite_type 'code'")
        return self


class WorkspaceVisibilityIn(BaseModel):
    """Admin-only visibility toggle for a workspace."""

    visibility: Literal["public", "private"]


class MemberAdminIn(BaseModel):
    """Admin-only promotion/demotion of a workspace member."""

    is_admin: bool


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    invite_id: str
    workspace_id: str
    invite_type: str
    email: str | None
    code: str | None
    created_by: str
    created_at: datetime
    expires_at: datetime | None


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
