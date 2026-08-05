"""Tests for the Member/Account link and the RefreshToken model
(Identity v2, SMAC-79 Task 2: email/password now live only on Account)."""

from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Account, Member, RefreshToken, Workspace, utcnow


def _make_workspace(db_session) -> Workspace:
    ws = Workspace(workspace_name="Acme")
    db_session.add(ws)
    db_session.commit()
    return ws


def _make_account(db_session, account_type: str = "human") -> Account:
    account = Account(account_type=account_type)
    db_session.add(account)
    db_session.flush()
    return account


def test_member_profile_columns_default_to_none(db_session):
    member = db_session.get(Member, _make_member(db_session, "Alice").member_id)
    assert member.first_name is None
    assert member.company is None


def test_duplicate_account_same_workspace_rejected(db_session):
    """One profile per account per workspace: same account_id + same
    workspace_id collides (uq_members_workspace_account)."""
    ws = Workspace(workspace_name="Acme")
    db_session.add(ws)
    db_session.commit()
    account = _make_account(db_session)
    m1 = Member(
        member_name="A",
        member_type="human",
        account_id=account.account_id,
        workspace_id=ws.workspace_id,
        handle="a",
    )
    m2 = Member(
        member_name="B",
        member_type="human",
        account_id=account.account_id,
        workspace_id=ws.workspace_id,
        handle="b",
    )
    db_session.add_all([m1, m2])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_same_account_different_workspaces_allowed(db_session):
    """The same account may hold a separate profile in a different
    workspace -- that's the whole point of the per-workspace-profile
    model (spec Decision 1)."""
    ws1, ws2 = Workspace(workspace_name="Acme"), Workspace(workspace_name="Beta")
    db_session.add_all([ws1, ws2])
    db_session.commit()
    account = _make_account(db_session)
    db_session.add_all(
        [
            Member(
                member_name="A",
                member_type="human",
                account_id=account.account_id,
                workspace_id=ws1.workspace_id,
                handle="a",
            ),
            Member(
                member_name="A",
                member_type="human",
                account_id=account.account_id,
                workspace_id=ws2.workspace_id,
                handle="a",
            ),
        ]
    )
    db_session.commit()
    assert db_session.query(Member).count() == 2


def test_member_account_id_not_null(db_session):
    ws = _make_workspace(db_session)
    db_session.add(
        Member(member_name="A", member_type="human", workspace_id=ws.workspace_id)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_refresh_token_round_trip(db_session):
    member = _make_member(db_session, "Alice")
    row = RefreshToken(
        token_hash="abc123",
        member_id=member.member_id,
        scope="workspace",
        expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(row)
    db_session.commit()
    loaded = db_session.get(RefreshToken, "abc123")
    assert loaded is not None
    assert loaded.member_id == member.member_id
    assert loaded.expires_at.tzinfo is not None  # UTCDateTime preserved offset
    assert loaded.created_at is not None


def _make_member(db_session, name: str) -> Member:
    ws = _make_workspace(db_session)
    account = _make_account(db_session)
    member = Member(
        member_name=name,
        member_type="human",
        account_id=account.account_id,
        workspace_id=ws.workspace_id,
        handle=name.lower(),
    )
    db_session.add(member)
    db_session.commit()
    return member
