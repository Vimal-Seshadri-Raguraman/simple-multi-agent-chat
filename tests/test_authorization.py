import pytest

from app.authorization import (
    authorize_channel_read,
    authorize_management_action,
    authorize_post_message,
    authorize_workspace_read,
)
from app.errors import ForbiddenMemberTypeError, NotAMemberError
from app.models import ChannelMember, Member, WorkspaceMember


def _make_member(db, member_type: str) -> Member:
    member = Member(member_name="Test", member_type=member_type)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


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
    db_session.add(ChannelMember(channel_id="c_1", member_id=member.member_id))
    db_session.commit()
    authorize_post_message(db_session, member, "c_1")  # should not raise


def test_non_channel_member_may_not_post(db_session):
    member = _make_member(db_session, "bot_app")
    with pytest.raises(NotAMemberError):
        authorize_post_message(db_session, member, "c_1")


def test_channel_member_may_read(db_session):
    member = _make_member(db_session, "human")
    db_session.add(ChannelMember(channel_id="c_1", member_id=member.member_id))
    db_session.commit()
    authorize_channel_read(db_session, member, "c_1")  # should not raise


def test_non_channel_member_may_not_read(db_session):
    member = _make_member(db_session, "human")
    with pytest.raises(NotAMemberError):
        authorize_channel_read(db_session, member, "c_1")


def test_workspace_member_may_read(db_session):
    member = _make_member(db_session, "agent")
    db_session.add(WorkspaceMember(workspace_id="w_1", member_id=member.member_id))
    db_session.commit()
    authorize_workspace_read(db_session, member, "w_1")  # should not raise


def test_non_workspace_member_may_not_read(db_session):
    member = _make_member(db_session, "bot_app")
    with pytest.raises(NotAMemberError):
        authorize_workspace_read(db_session, member, "w_1")
