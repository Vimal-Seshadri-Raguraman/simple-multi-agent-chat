"""Single source of truth for who can do what (SMAC-92, spec §2).

Roles live on the membership row and are looked up LIVE per request --
never baked into tokens, so demotion is instantaneous. The agent type cap
is intersected LAST so no future role addition can accidentally grant an
agent management powers.
"""

import enum

from app.errors import CapabilityDeniedError
from app.models import Member


class Cap(str, enum.Enum):
    POST = "post"
    READ = "read"
    ACK_MENTIONS = "ack_mentions"
    CREATE_CHANNELS = "create_channels"
    VIEW_MEMBERS = "view_members"
    VIEW_AGENTS = "view_agents"
    MINT_HUMAN_INVITES = "mint_human_invites"
    MINT_AGENT_INVITES = "mint_agent_invites"
    MANAGE_AGENTS = "manage_agents"
    MANAGE_WORKSPACE = "manage_workspace"
    ASSIGN_ROLES = "assign_roles"
    REMOVE_MEMBERS = "remove_members"


VALID_ROLES = ("member", "agent_admin", "admin")
_MEMBER_CAPS = frozenset(
    {
        Cap.POST,
        Cap.READ,
        Cap.ACK_MENTIONS,
        Cap.CREATE_CHANNELS,
        Cap.VIEW_MEMBERS,
        Cap.VIEW_AGENTS,
    }
)
ROLE_CAPS: dict[str, frozenset[Cap]] = {
    "member": _MEMBER_CAPS,
    "agent_admin": _MEMBER_CAPS | {Cap.MANAGE_AGENTS, Cap.MINT_AGENT_INVITES},
    "admin": frozenset(Cap),
}
_AGENT_TYPE_CAPS = frozenset({Cap.POST, Cap.READ, Cap.ACK_MENTIONS})


def caps_for(member: Member) -> frozenset[Cap]:
    caps = ROLE_CAPS[member.role]
    if member.member_type != "human":
        caps = caps & _AGENT_TYPE_CAPS
    return caps


def require_cap(member: Member, cap: Cap) -> None:
    if cap not in caps_for(member):
        raise CapabilityDeniedError(f"This action requires {cap.value}.")
