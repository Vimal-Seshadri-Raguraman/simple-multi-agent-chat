from sqlalchemy.orm import Session

from app.errors import ForbiddenMemberTypeError, NotAMemberError, NotFoundError
from app.models import ChannelMember, Member


def authorize_management_action(member: Member) -> None:
    """Only human members may create/manage workspaces, channels, and membership."""
    if member.member_type != "human":
        raise ForbiddenMemberTypeError(
            f"Member '{member.member_id}' has type '{member.member_type}'; "
            "only 'human' members may perform management actions"
        )


def _require_channel_membership(db: Session, member_id: str, channel_id: str) -> None:
    is_member = (
        db.query(ChannelMember)
        .filter(
            ChannelMember.channel_id == channel_id,
            ChannelMember.member_id == member_id,
        )
        .first()
        is not None
    )
    if not is_member:
        raise NotAMemberError(
            f"Member '{member_id}' is not a member of channel '{channel_id}'"
        )


def authorize_post_message(db: Session, member: Member, channel_id: str) -> None:
    """Any member type may post, but only if already a member of the channel."""
    _require_channel_membership(db, member.member_id, channel_id)


def authorize_channel_read(db: Session, member: Member, channel_id: str) -> None:
    """Reading a channel's members/messages requires channel membership (any member type)."""
    _require_channel_membership(db, member.member_id, channel_id)


def require_same_workspace(member: Member, workspace_id: str) -> None:
    """The workspace wall: a token only works inside its own workspace.

    Raises the uniform not-found error on mismatch so foreign workspaces
    (private or otherwise) are indistinguishable from nonexistent ones.
    """
    if member.workspace_id != workspace_id:
        raise NotFoundError(f"Workspace '{workspace_id}' not found")
