"""Unit tests for the account-creation service (the only way human accounts are born)."""

import pytest

from app.accounts import create_member_account
from app.errors import EmailTakenError
from app.models import Channel, ChannelMember, Member, Workspace


def _workspace(db_session, with_default_channel=True):
    ws = Workspace(workspace_name="Acme")
    db_session.add(ws)
    db_session.flush()
    if with_default_channel:
        ch = Channel(workspace_id=ws.workspace_id, channel_name="general")
        db_session.add(ch)
        db_session.flush()
        ws.default_channel_id = ch.channel_id
    db_session.commit()
    return ws


def test_creates_account_in_workspace_and_default_channel(db_session):
    ws = _workspace(db_session)
    member = create_member_account(
        db_session,
        ws,
        email="Alice@Test.Example",
        password="password-123",
        first_name="Alice",
        last_name="L",
    )
    db_session.commit()
    assert member.workspace_id == ws.workspace_id
    assert member.email == "alice@test.example"  # lowercased
    assert member.member_name == "Alice L"
    assert member.is_admin is False
    assert member.password_hash != "password-123"
    in_channel = (
        db_session.query(ChannelMember)
        .filter_by(channel_id=ws.default_channel_id, member_id=member.member_id)
        .first()
    )
    assert in_channel is not None


def test_duplicate_email_same_workspace_rejected(db_session):
    ws = _workspace(db_session)
    create_member_account(
        db_session,
        ws,
        email="a@test.example",
        password="password-123",
        first_name="A",
        last_name="One",
    )
    db_session.commit()
    with pytest.raises(EmailTakenError):
        create_member_account(
            db_session,
            ws,
            email="A@TEST.EXAMPLE",
            password="password-456",
            first_name="A",
            last_name="Two",
        )


def test_same_email_different_workspaces_ok(db_session):
    ws1, ws2 = _workspace(db_session), _workspace(db_session)
    m1 = create_member_account(
        db_session,
        ws1,
        email="a@test.example",
        password="password-123",
        first_name="A",
        last_name="One",
    )
    m2 = create_member_account(
        db_session,
        ws2,
        email="a@test.example",
        password="password-456",
        first_name="A",
        last_name="Two",
    )
    db_session.commit()
    assert m1.member_id != m2.member_id
    assert {m1.workspace_id, m2.workspace_id} == {ws1.workspace_id, ws2.workspace_id}


def test_admin_flag_and_null_default_channel(db_session):
    ws = _workspace(db_session, with_default_channel=False)
    member = create_member_account(
        db_session,
        ws,
        email="f@test.example",
        password="password-123",
        first_name="F",
        last_name="Ounder",
        is_admin=True,
        display_name="The Founder",
    )
    db_session.commit()
    assert member.is_admin is True
    assert member.member_name == "The Founder"
    assert db_session.query(ChannelMember).count() == 0
