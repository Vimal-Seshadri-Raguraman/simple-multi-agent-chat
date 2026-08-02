"""Tests for the auth-related columns on Member and the RefreshToken model."""

from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Member, RefreshToken, Workspace, utcnow


def _make_workspace(db_session) -> Workspace:
    ws = Workspace(workspace_name="Acme")
    db_session.add(ws)
    db_session.commit()
    return ws


def test_member_auth_columns_default_to_none(db_session):
    member = db_session.get(Member, _make_member(db_session, "Alice").member_id)
    assert member.email is None
    assert member.password_hash is None
    assert member.first_name is None
    assert member.company is None


def test_duplicate_email_same_workspace_rejected(db_session):
    """Email uniqueness is per-workspace: same email + same workspace_id collides."""
    ws = Workspace(workspace_name="Acme")
    db_session.add(ws)
    db_session.commit()
    m1 = Member(
        member_name="A",
        member_type="human",
        email="a@x.com",
        workspace_id=ws.workspace_id,
    )
    m2 = Member(
        member_name="B",
        member_type="human",
        email="a@x.com",
        workspace_id=ws.workspace_id,
    )
    db_session.add_all([m1, m2])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_multiple_null_emails_allowed(db_session):
    """Agents/bots have no email; UNIQUE must not reject multiple NULLs."""
    ws = _make_workspace(db_session)
    db_session.add_all(
        [
            Member(
                member_name="Agent1", member_type="agent", workspace_id=ws.workspace_id
            ),
            Member(
                member_name="Agent2", member_type="agent", workspace_id=ws.workspace_id
            ),
        ]
    )
    db_session.commit()
    assert db_session.query(Member).count() == 2


def test_refresh_token_round_trip(db_session):
    member = _make_member(db_session, "Alice")
    row = RefreshToken(
        token_hash="abc123",
        member_id=member.member_id,
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
    member = Member(
        member_name=name,
        member_type="human",
        workspace_id=_make_workspace(db_session).workspace_id,
    )
    db_session.add(member)
    db_session.commit()
    return member
