"""Workspace-join side effects, in one place.

Every path into a workspace — manual member-add, email-invite accept, and
code redemption — must produce the same result: a workspace_members row
plus membership in the workspace's default channel. Centralizing it here
keeps the three routers from drifting apart.
"""

from sqlalchemy.orm import Session

from app.errors import AlreadyAMemberError
from app.models import ChannelMember, Workspace, WorkspaceMember


def join_workspace(db: Session, workspace: Workspace, member_id: str) -> None:
    """Add a member to a workspace and its default channel, then commit.

    Raises AlreadyAMemberError if the member is already in the workspace.
    Default-channel membership is idempotent and skipped entirely for
    pre-feature workspaces whose default_channel_id is NULL.
    """
    exists = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace.workspace_id,
            WorkspaceMember.member_id == member_id,
        )
        .first()
    )
    if exists is not None:
        raise AlreadyAMemberError(
            f"Member '{member_id}' is already in workspace '{workspace.workspace_id}'"
        )
    db.add(WorkspaceMember(workspace_id=workspace.workspace_id, member_id=member_id))

    if workspace.default_channel_id is not None:
        in_channel = (
            db.query(ChannelMember)
            .filter(
                ChannelMember.channel_id == workspace.default_channel_id,
                ChannelMember.member_id == member_id,
            )
            .first()
        )
        if in_channel is None:
            db.add(
                ChannelMember(
                    channel_id=workspace.default_channel_id, member_id=member_id
                )
            )
    db.commit()
