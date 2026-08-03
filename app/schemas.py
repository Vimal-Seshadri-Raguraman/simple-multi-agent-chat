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
from sqlalchemy.orm import Session

from app.mentions import resolve_payload_refs
from app.models import Channel, Member, Message, Workspace


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
    handle: str
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
    handle: str
    api_key: str


class MessageCreate(BaseModel):
    message_text: str = Field(min_length=1, max_length=4000)


class MemberSelfOut(BaseModel):
    """A member's own full profile, including their private email."""

    model_config = ConfigDict(from_attributes=True)
    member_id: str
    member_name: str
    member_type: str
    handle: str
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
    handle: str | None = Field(
        default=None, min_length=2, max_length=32, pattern=r"^[a-z0-9-]+$"
    )

    @field_validator("display_name", "first_name", "last_name", "handle", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: str | None) -> str | None:
        """Reject an explicit JSON null for required-on-registration fields.

        `min_length`/`pattern` only constrain the `str` branch of
        `str | None`; an explicit JSON `null` bypasses them entirely.
        Pydantic only invokes validators when the field is actually present
        in the payload (by default it does not validate an unset field's
        default), so this only fires when the client explicitly sends
        `null` -- omitting the field entirely still leaves it untouched, as
        intended for a partial update. Without this check, `PATCH
        /members/me {"display_name": null}` would set the member's NOT NULL
        `member_name` column to None (raw 500 IntegrityError outside the
        error envelope), and `{"first_name": null}` / `{"last_name": null}`
        would silently wipe registration-required fields (200 OK).
        `{"handle": null}` has the same shape: it skips the taken-check
        (`handle IS NULL` matches nobody) and hits the members NOT NULL
        constraint at commit, surfacing as a generic 409 `conflict` instead
        of a 422. company/occupation/job_role are intentionally exempt:
        explicitly clearing them is legitimate.
        """
        if value is None:
            raise ValueError("must not be null")
        return value


class RegisterIn(BaseModel):
    """Registration request. Names are required; the rest of the profile is optional."""

    email: EmailStr
    password: str = Field(min_length=8)
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    display_name: str | None = Field(default=None, min_length=1)
    company: str | None = None
    occupation: str | None = None
    job_role: str | None = None

    @field_validator("password")
    @classmethod
    def _password_within_bcrypt_limit(cls, value: str) -> str:
        """bcrypt silently truncates beyond 72 BYTES; reject instead of truncating."""
        if len(value.encode("utf-8")) > 72:
            raise ValueError(
                "password must be at most 72 bytes when UTF-8 encoded (bcrypt limit)"
            )
        return value


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

    @field_validator("workspace_name", mode="before")
    @classmethod
    def _reject_whitespace_only_workspace_name(cls, value: str) -> str:
        """Reject whitespace-only workspace names; strip and store the cleaned name."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("workspace_name must not be empty or whitespace-only")
            return stripped
        return value


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


class UnreadsRowOut(BaseModel):
    channel_id: str
    channel_name: str
    unread_count: int
    first_unread_message_id: str | None
    mention_count: int


class UnreadsOut(BaseModel):
    unreads: list[UnreadsRowOut]


def build_message_payload(
    message: Message,
    workspace: Workspace,
    channel: Channel,
    sender: Member,
    db: Session,
) -> dict:
    """The single source of truth for the wire schema shared by REST and WebSocket.

    `mentions` and `channel_refs` are resolved fresh from the stored
    canonical text on every call (via `resolve_payload_refs`), so a handle
    or channel rename is reflected immediately without rewriting any
    stored message -- both arrays are always present, empty when nothing
    resolves. The sender's own token is excluded from `mentions`: a
    self-mention is already visible via `Sender` and never produced a
    `Mention` row at post time (see `canonicalize`), so it's a no-op here
    too, on both the POST response and every later GET.
    """
    mentioned_members, referenced_channels = resolve_payload_refs(
        db, workspace.workspace_id, message.message_text
    )
    mentioned_members = [
        m for m in mentioned_members if m.member_id != sender.member_id
    ]
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
        "mentions": [
            {
                "member_id": m.member_id,
                "handle": m.handle,
                "member_name": m.member_name,
            }
            for m in mentioned_members
        ],
        "channel_refs": [
            {"channel_id": c.channel_id, "channel_name": c.channel_name}
            for c in referenced_channels
        ],
    }
