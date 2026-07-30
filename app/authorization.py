from sqlalchemy.orm import Session

from app.errors import ForbiddenMemberTypeError, NotAMemberError
from app.models import ChannelMember, Member


def authorize_management_action(member: Member) -> None:
    """Only human members may create/manage workspaces, channels, and membership."""
    if member.member_type != "human":
        raise ForbiddenMemberTypeError(
            f"Member '{member.member_id}' has type '{member.member_type}'; "
            "only 'human' members may perform management actions"
        )


def authorize_post_message(db: Session, member: Member, channel_id: str) -> None:
    """Any member type may post, but only if already a member of the channel."""
    is_member = (
        db.query(ChannelMember)
        .filter(
            ChannelMember.channel_id == channel_id,
            ChannelMember.member_id == member.member_id,
        )
        .first()
        is not None
    )
    if not is_member:
        raise NotAMemberError(
            f"Member '{member.member_id}' is not a member of channel '{channel_id}'"
        )
