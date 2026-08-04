"""Unit tests for the account-creation service (the only way any human
per-workspace profile is born, Identity v2 SMAC-79 Task 2)."""

import pytest

from app.accounts import create_account, create_member_account
from app.errors import AlreadyAMemberError
from app.models import Channel, ChannelMember, Member, Workspace


def _workspace(db_session, with_default_channel=True, name="Acme"):
    ws = Workspace(workspace_name=name)
    db_session.add(ws)
    db_session.flush()
    if with_default_channel:
        ch = Channel(workspace_id=ws.workspace_id, channel_name="general")
        db_session.add(ch)
        db_session.flush()
        ws.default_channel_id = ch.channel_id
    db_session.commit()
    return ws


def _account(db_session, email, password="password-123"):
    """A real global Account, the same shape every account-authed birth
    door receives via `get_current_account`."""
    account = create_account(db_session, email, password)
    db_session.flush()
    return account


def test_creates_account_in_workspace_and_default_channel(db_session):
    ws = _workspace(db_session)
    account = _account(db_session, "Alice@Test.Example")
    member = create_member_account(
        db_session,
        ws,
        account=account,
        first_name="Alice",
        last_name="L",
    )
    db_session.commit()
    assert member.workspace_id == ws.workspace_id
    assert member.account_id == account.account_id
    assert member.member_name == "Alice L"
    assert member.is_admin is False
    in_channel = (
        db_session.query(ChannelMember)
        .filter_by(channel_id=ws.default_channel_id, member_id=member.member_id)
        .first()
    )
    assert in_channel is not None


def test_duplicate_account_same_workspace_rejected(db_session):
    ws = _workspace(db_session)
    account = _account(db_session, "a@test.example")
    create_member_account(
        db_session, ws, account=account, first_name="A", last_name="One"
    )
    db_session.commit()
    with pytest.raises(AlreadyAMemberError):
        create_member_account(
            db_session, ws, account=account, first_name="A", last_name="Two"
        )


def test_same_account_different_workspaces_ok(db_session):
    ws1, ws2 = _workspace(db_session, name="Acme"), _workspace(
        db_session, name="Acme Two"
    )
    shared_account = _account(db_session, "a@test.example")
    m1 = create_member_account(
        db_session, ws1, account=shared_account, first_name="A", last_name="One"
    )
    m2 = create_member_account(
        db_session, ws2, account=shared_account, first_name="A", last_name="Two"
    )
    db_session.commit()
    assert m1.member_id != m2.member_id
    assert {m1.workspace_id, m2.workspace_id} == {ws1.workspace_id, ws2.workspace_id}
    assert m1.account_id == m2.account_id == shared_account.account_id


def test_admin_flag_and_null_default_channel(db_session):
    ws = _workspace(db_session, with_default_channel=False)
    account = _account(db_session, "f@test.example")
    member = create_member_account(
        db_session,
        ws,
        account=account,
        first_name="F",
        last_name="Ounder",
        is_admin=True,
        display_name="The Founder",
    )
    db_session.commit()
    assert member.is_admin is True
    assert member.member_name == "The Founder"
    assert db_session.query(ChannelMember).count() == 0


def test_does_not_commit_caller_must(db_session):
    """create_member_account flushes but never commits; a caller rollback
    must leave no trace, proving the service never commits on its own."""
    ws = _workspace(db_session)
    account = _account(db_session, "rollback@test.example")
    member = create_member_account(
        db_session, ws, account=account, first_name="Roll", last_name="Back"
    )
    member_id = member.member_id
    db_session.rollback()
    assert db_session.get(Member, member_id) is None
