"""Tests for the WorkspaceInvite model and Workspace.default_channel_id."""

from datetime import timedelta

from app.models import Channel, Member, Workspace, WorkspaceInvite, utcnow


def _make(db_session, model, **kwargs):
    obj = model(**kwargs)
    db_session.add(obj)
    db_session.commit()
    return obj


def test_email_invite_round_trip(db_session):
    inviter = _make(db_session, Member, member_name="Host", member_type="human")
    workspace = _make(db_session, Workspace, workspace_name="Acme")
    invite = _make(
        db_session,
        WorkspaceInvite,
        workspace_id=workspace.workspace_id,
        invite_type="email",
        email="alice@test.example",
        created_by=inviter.member_id,
    )
    loaded = db_session.get(WorkspaceInvite, invite.invite_id)
    assert loaded.email == "alice@test.example"
    assert loaded.code is None
    assert loaded.expires_at is None
    assert loaded.created_at.tzinfo is not None


def test_code_invite_round_trip(db_session):
    inviter = _make(db_session, Member, member_name="Host", member_type="human")
    workspace = _make(db_session, Workspace, workspace_name="Acme")
    invite = _make(
        db_session,
        WorkspaceInvite,
        workspace_id=workspace.workspace_id,
        invite_type="code",
        code="abc123def456",
        created_by=inviter.member_id,
        expires_at=utcnow() + timedelta(days=7),
    )
    loaded = db_session.get(WorkspaceInvite, invite.invite_id)
    assert loaded.code == "abc123def456"
    assert loaded.email is None
    assert loaded.expires_at.tzinfo is not None


def test_workspace_default_channel_nullable(db_session):
    workspace = _make(db_session, Workspace, workspace_name="Old")
    assert workspace.default_channel_id is None
    channel = _make(
        db_session,
        Channel,
        workspace_id=workspace.workspace_id,
        channel_name="general",
    )
    workspace.default_channel_id = channel.channel_id
    db_session.commit()
    assert (
        db_session.get(Workspace, workspace.workspace_id).default_channel_id
        == channel.channel_id
    )
