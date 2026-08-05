import pytest

from app.authorization import (
    authorize_channel_read,
    authorize_management_action,
    authorize_post_message,
    require_same_workspace,
)
from app.errors import ForbiddenMemberTypeError, NotAMemberError, NotFoundError
from app.models import Account, Channel, ChannelMember, Member, Workspace


def _make_workspace(db, workspace_id: str) -> None:
    """Insert a bare workspace row so FK-constrained membership rows are valid."""
    if db.get(Workspace, workspace_id) is None:
        db.add(Workspace(workspace_id=workspace_id, workspace_name="Test Workspace"))
        db.commit()


def _make_member(db, member_type: str, workspace_id: str = "w_1") -> Member:
    """Insert a member with a valid workspace + account (both NOT NULL post-cutover)."""
    _make_workspace(db, workspace_id)
    account = Account(account_type=member_type)
    db.add(account)
    db.flush()
    member = Member(
        member_name="Test",
        member_type=member_type,
        workspace_id=workspace_id,
        account_id=account.account_id,
        handle="test",
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def _make_channel(db, channel_id: str, workspace_id: str = "w_1") -> None:
    """Insert a bare workspace + channel row so FK-constrained membership rows are valid."""
    _make_workspace(db, workspace_id)
    db.add(
        Channel(
            channel_id=channel_id, workspace_id=workspace_id, channel_name="general"
        )
    )
    db.commit()


def test_human_may_perform_management_action(db_session):
    human = _make_member(db_session, "human")
    authorize_management_action(human)  # should not raise


@pytest.mark.parametrize("member_type", ["agent", "bot_app"])
def test_non_human_may_not_perform_management_action(db_session, member_type):
    member = _make_member(db_session, member_type)
    with pytest.raises(ForbiddenMemberTypeError):
        authorize_management_action(member)


def test_channel_member_may_post(db_session):
    member = _make_member(db_session, "agent")
    _make_channel(db_session, "c_1")
    db_session.add(ChannelMember(channel_id="c_1", member_id=member.member_id))
    db_session.commit()
    authorize_post_message(db_session, member, "c_1")  # should not raise


def test_non_channel_member_may_not_post(db_session):
    member = _make_member(db_session, "bot_app")
    with pytest.raises(NotAMemberError):
        authorize_post_message(db_session, member, "c_1")


def test_channel_member_may_read(db_session):
    member = _make_member(db_session, "human")
    _make_channel(db_session, "c_1")
    db_session.add(ChannelMember(channel_id="c_1", member_id=member.member_id))
    db_session.commit()
    authorize_channel_read(db_session, member, "c_1")  # should not raise


def test_non_channel_member_may_not_read(db_session):
    member = _make_member(db_session, "human")
    with pytest.raises(NotAMemberError):
        authorize_channel_read(db_session, member, "c_1")


def test_require_same_workspace_allows_own_workspace(db_session):
    member = _make_member(db_session, "human", workspace_id="w_1")
    require_same_workspace(member, "w_1")  # should not raise


def test_require_same_workspace_blocks_foreign_workspace(db_session):
    member = _make_member(db_session, "human", workspace_id="w_1")
    with pytest.raises(NotFoundError):
        require_same_workspace(member, "w_2")
