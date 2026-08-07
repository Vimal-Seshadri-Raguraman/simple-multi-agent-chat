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

from app.capabilities import VALID_ROLES
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
    account_id: str
    role: str
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    occupation: str | None = None
    job_role: str | None = None


class MemberRegisterIn(BaseModel):
    """Create a brand-new agent/bot_app member (`member_name`), or attach an
    EXISTING agent/bot_app account as a new per-workspace membership
    (`account_id`, spec §4) -- exactly one of the two, never both/neither.
    """

    member_name: str | None = None
    account_id: str | None = None

    @model_validator(mode="after")
    def _exactly_one_of_member_name_or_account_id(self) -> "MemberRegisterIn":
        if (self.member_name is None) == (self.account_id is None):
            raise ValueError(
                "exactly one of member_name or account_id must be provided"
            )
        return self


class MemberRegisterOut(BaseModel):
    member_id: str
    member_name: str
    member_type: str
    handle: str
    api_key: str


class MessageCreate(BaseModel):
    message_text: str = Field(min_length=1, max_length=4000)


class MemberSelfOut(BaseModel):
    """A member's own full profile.

    Identity v2 (SMAC-79 Task 2, spec §7): member payloads never expose
    email anymore -- accounts hold it now; only `GET /accounts/me` shows
    the caller their own. `account_id` links this profile back to the
    caller's global account (additive, spec §4).

    `role` and `capabilities` (SMAC-92, spec §2-3) are the roles-and-
    privileges wire contract: `role` is the real `Member.role` column,
    `capabilities` is `[c.value for c in caps_for(member)]` -- the derived
    list every client (web, TUI) should render from instead of
    re-implementing the capability table. Unlike the old `is_admin` flag,
    `role`/`capabilities` are visible for ANY member lookup, not just the
    caller's own (spec §3 transparency: roles are public, only *managing*
    them is gated) -- see `app.accounts.build_member_self_out`, the one
    place that assembles this schema.

    `workspace_visibility` (SMAC-72 task 6) isn't a member attribute at
    all (no GET-your-own-workspace endpoint exists), so it's carried here
    instead. It stays SELF-view-only: `GET /member` (looking up ANOTHER
    member in your own workspace) nulls it out -- there's no product
    reason yet for one member to learn another workspace-level fact
    through this route (deliberate, minimal scope, unrelated to the
    role-visibility change above).
    """

    model_config = ConfigDict(from_attributes=True)
    member_id: str
    member_name: str
    member_type: str
    handle: str
    workspace_id: str
    account_id: str
    created_at: datetime
    first_name: str | None
    last_name: str | None
    company: str | None
    occupation: str | None
    job_role: str | None
    role: str
    capabilities: list[str]
    workspace_visibility: str | None


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
    """Account-authed registration into a workspace (spec §3): the caller
    already holds an account (identified by their account token), so this
    only asks the per-workspace display name + optional profile fields --
    no email/password here anymore (Identity v2, SMAC-79 Task 2)."""

    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    display_name: str | None = Field(default=None, min_length=1)
    company: str | None = None
    occupation: str | None = None
    job_role: str | None = None


class MetaOut(BaseModel):
    """Unauthenticated version handshake -- see GET /meta."""

    server_version: str
    api_version: int


class TokenPairOut(BaseModel):
    """An access/refresh token pair, mirrored after every login/refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AccountCreateIn(BaseModel):
    """POST /accounts: create a global account (spec §2)."""

    email: EmailStr
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def _password_within_bcrypt_limit(cls, value: str) -> str:
        """bcrypt silently truncates beyond 72 BYTES; reject instead of truncating."""
        if len(value.encode("utf-8")) > 72:
            raise ValueError(
                "password must be at most 72 bytes when UTF-8 encoded (bcrypt limit)"
            )
        return value


class AccountLoginIn(BaseModel):
    """POST /accounts/login: global login, no workspace_id (spec §2)."""

    email: EmailStr
    password: str


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    account_id: str
    email: str | None
    created_at: datetime


class AccountAuthOut(BaseModel):
    """POST /accounts' response shape: the new account, auto-logged-in."""

    account: AccountOut
    tokens: TokenPairOut


class AccountMembershipOut(BaseModel):
    """One of the caller's workspace profiles, as surfaced by
    POST /accounts/login and GET /accounts/me."""

    workspace_id: str
    workspace_name: str
    member_id: str
    handle: str


class AccountLoginOut(AccountAuthOut):
    """POST /accounts/login's response shape: account + tokens + every
    workspace the account already has a profile in (the real thing
    /auth/discover used to simulate)."""

    workspaces: list[AccountMembershipOut]


class AccountMeOut(BaseModel):
    """GET /accounts/me: the caller's own account + their memberships."""

    account_id: str
    email: str | None
    created_at: datetime
    memberships: list[AccountMembershipOut]


class FoundWorkspaceIn(BaseModel):
    """Found a workspace (spec §3): account-authed -- the caller already
    has an account (via their account token), so this only carries the
    workspace's own details plus the founder's per-workspace display name
    (`display_first_name`/`display_last_name`, deliberately distinct field
    names from the join doors' `first_name`/`last_name` -- a locked
    interface decision, spec §3)."""

    workspace_name: str = Field(min_length=1)
    visibility: Literal["public", "private"] = "private"
    display_first_name: str = Field(min_length=1)
    display_last_name: str = Field(min_length=1)

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
    """Create an invite: email-targeted, a shareable human code, or a
    shareable agent code (`agent_code`, SMAC-92 -- redeemable only via
    the unauthenticated `POST /agents/join`)."""

    invite_type: Literal["email", "code", "agent_code"]
    email: EmailStr | None = None

    @model_validator(mode="after")
    def _email_iff_email_type(self) -> "InviteCreateIn":
        if self.invite_type == "email" and self.email is None:
            raise ValueError("email is required for invite_type 'email'")
        if self.invite_type != "email" and self.email is not None:
            raise ValueError(
                f"email is not allowed for invite_type '{self.invite_type}'"
            )
        return self


class WorkspaceVisibilityIn(BaseModel):
    """Admin-only visibility toggle for a workspace."""

    visibility: Literal["public", "private"]


class MemberRoleIn(BaseModel):
    """`Cap.ASSIGN_ROLES`-gated role change for a workspace member
    (SMAC-92, replaces the old boolean `MemberAdminIn`)."""

    role: str

    @field_validator("role")
    @classmethod
    def _valid_role(cls, value: str) -> str:
        if value not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}")
        return value


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


class AgentJoinIn(BaseModel):
    """Redeem a single-use agent invite code -- unauthenticated (`POST
    /agents/join`, SMAC-92): the caller has no credential yet, only the
    code and a display name for the new agent."""

    code: str = Field(min_length=1)
    name: str = Field(min_length=1)


class AgentJoinOut(BaseModel):
    """The freshly minted agent's identity and per-workspace API key,
    shown exactly once here -- there is no other way to retrieve it
    later (`Member.api_key_hash` is one-way)."""

    account_id: str
    member_id: str
    handle: str
    api_key: str
    workspace: WorkspaceOut


class UnreadsRowOut(BaseModel):
    channel_id: str
    channel_name: str
    unread_count: int
    first_unread_message_id: str | None
    mention_count: int


class UnreadsOut(BaseModel):
    unreads: list[UnreadsRowOut]


class MarkReadIn(BaseModel):
    """Mark-read request body. `last_read_message_id: null` and an
    omitted/empty body are equivalent -- both mean "caught up to latest".
    There is no third meaning available (e.g. "leave unchanged")."""

    last_read_message_id: str | None = None


_REMOVED_SENDER_NAME = "(removed member)"


def build_message_payload(
    message: Message,
    workspace: Workspace,
    channel: Channel,
    sender: Member | None,
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

    `sender` is `None` when `message.sender_member_id` is null (SMAC-92:
    the sender was removed from the workspace -- `remove_member` nulls it
    rather than deleting the message, so history survives). The `Sender`
    field then renders a placeholder rather than crashing; there is no
    snapshot of the departed member's handle to fall back to, only the
    generic label below.
    """
    mentioned_members, referenced_channels = resolve_payload_refs(
        db, workspace.workspace_id, message.message_text
    )
    if sender is not None:
        mentioned_members = [
            m for m in mentioned_members if m.member_id != sender.member_id
        ]
    sender_out: dict[str, str | None] = (
        {"member_id": sender.member_id, "member_name": sender.member_name}
        if sender is not None
        else {"member_id": None, "member_name": _REMOVED_SENDER_NAME}
    )
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
        "Sender": sender_out,
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
